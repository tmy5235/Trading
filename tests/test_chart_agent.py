import asyncio
import json
import os
import unittest
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from agents import Model, ModelResponse, Usage
from openai.types.responses import ResponseFunctionToolCall

from backend import classroom_decisions as classroom, strategy
from backend.chart_agent import ChartAgentRequest, chart_agent, run_chart_agent
from tests.test_classroom_decisions import CONTEXT, NOW, URL, reply


class FixedToolModel(Model):
    """Offline model boundary. The real SDK still validates and invokes the tool."""
    def __init__(self, name='assess_visible_chart', arguments=None):
        self.name = name
        self.arguments = arguments or {}
        self.inputs = []

    async def get_response(self, **request):
        self.inputs.append(request['input'])
        return ModelResponse(
            output=[ResponseFunctionToolCall(type='function_call', name=self.name,
                    call_id='fixture-call', arguments=json.dumps(self.arguments))],
            usage=Usage(), response_id='fixture-response',
        )

    async def stream_response(self, *args, **kwargs):
        raise AssertionError('This check uses the one-shot agent.')


class ChartAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        keys = patch.dict(os.environ, OPENAI_API_KEY='offline-fixture-only')
        keys.start()
        self.addCleanup(keys.stop)

    def test_registered_tools_and_schemas(self):
        lookup, assess = chart_agent.tools
        self.assertEqual([lookup.name, assess.name], ['lookup_workstation_rules', 'assess_visible_chart'])
        self.assertIn('explain', lookup.description)
        self.assertIn('new', assess.description)
        self.assertIn('captured chart', assess.description)
        self.assertFalse(chart_agent.handoffs)
        for tool in chart_agent.tools:
            self.assertTrue(tool.strict_json_schema)
            self.assertFalse(tool.params_json_schema['additionalProperties'])
        self.assertEqual(lookup.params_json_schema['required'], ['rule_set'])
        self.assertEqual(lookup.params_json_schema['properties']['rule_set']['enum'],
                         ['chart_signal', 'paper_strategy'])
        self.assertEqual(assess.params_json_schema['properties'], {})
        self.assertEqual(assess.params_json_schema['required'], [])

    async def test_rule_sets_and_invalid_input(self):
        for scope in ('chart_signal', 'paper_strategy', 'bad_scope'):
            with patch.object(chart_agent, 'model', FixedToolModel('lookup_workstation_rules', {'rule_set': scope})):
                result = await run_chart_agent(ChartAgentRequest(question='Explain the rules.'))
            if scope == 'bad_scope':
                self.assertEqual(result['status'], 'invalid_model_reply')
                self.assertFalse(result['tools'])
                continue
            call, = result['tools']
            self.assertEqual(call['name'], 'lookup_workstation_rules')
            if scope == 'chart_signal':
                self.assertEqual(call['result']['question'], classroom.QUESTION)
            else:
                self.assertEqual(call['result']['rules'], strategy.RULES)

    async def test_missing_invalid_expired_capture_and_missing_key(self):
        model = FixedToolModel()
        with patch.object(chart_agent, 'model', model), patch.object(classroom, 'post_decision') as provider:
            cases = [(None, 'missing_snapshot'), ({'image_url':'bad', 'context':CONTEXT}, 'invalid_snapshot'),
                     ({'image_url':URL, 'context':{**CONTEXT, 'capture_time':'2001-01-01T00:00:00Z'}}, 'invalid_snapshot')]
            for capture, expected in cases:
                result = await run_chart_agent(ChartAgentRequest(question='Assess the chart.', snapshot=capture))
                self.assertEqual(result['tools'][0]['result']['status'], expected)
            with patch.dict(os.environ, OPENAI_API_KEY=''):
                self.assertEqual((await run_chart_agent(ChartAgentRequest(question='Assess.')))['status'], 'missing_key')
            provider.assert_not_called()
        self.assertNotIn('data:image', repr(model.inputs))

    async def test_concurrent_requests_use_their_own_snapshot(self):
        model = FixedToolModel()
        now = datetime.now(timezone.utc).isoformat()
        def assessment(image_url, context, *, conversation_id=None):
            return {'status':'ok', 'image_url':image_url, 'context':context,
                    'answer':{'choice':'wait', 'probability':0.125}}
        with patch.object(chart_agent, 'model', model), patch.object(classroom, 'decide', side_effect=assessment):
            requests = [ChartAgentRequest(question='Assess.', snapshot={
                'image_url': 'fixture-' + interval, 'context':{**CONTEXT, 'capture_time':now, 'interval':interval}
            }) for interval in ('1m', '5m')]
            results = await asyncio.gather(*(run_chart_agent(request) for request in requests))
        stored = [r['tools'][0]['result'] for r in results]
        self.assertEqual([(r['image_url'], r['context']['interval']) for r in stored],
                         [('fixture-1m', '1m'), ('fixture-5m', '5m')])
        self.assertIn('WAIT (probability: 0.13)', results[0]['message'])
        self.assertNotIn('fixture-1m', repr(model.inputs))
        self.assertNotIn('fixture-5m', repr(model.inputs))

    async def test_unclear_and_rule_turns_cannot_assess_attached_chart(self):
        request = ChartAgentRequest(question='Should I wait?', snapshot={'image_url':URL, 'context':CONTEXT})
        fixed = FixedToolModel('assess_visible_chart')
        with patch.object(chart_agent, 'model', fixed), patch.object(classroom, 'decide') as decide:
            reply = await run_chart_agent(request)
            self.assertEqual(reply['status'], 'clarification')
            self.assertEqual(reply['tools'], [])
            self.assertIn('assess', reply['message'])
            request.question = 'Explain the rules for this chart.'
            reply = await run_chart_agent(request)
            self.assertEqual(reply['status'], 'invalid_model_reply')
            decide.assert_not_called()

    async def test_comparison_uses_saved_result_scoped_to_chat_ticker_and_mode(self):
        model = FixedToolModel()
        chat, other = uuid4(), uuid4()
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'assessment.sqlite3'
            outputs = iter([reply('long'), reply('short'), {}, reply('wait', []), reply('short')])
            decide = partial(classroom.decide, path=path, now=NOW, provider=lambda *_: next(outputs))
            lookup = partial(classroom.previous_assessment, path=path)
            async def assess(conversation, mode, stamp):
                request = ChartAgentRequest(question='Run a new decision and compare.', conversation_id=conversation,
                    snapshot={'image_url':URL, 'context':{**CONTEXT, 'mode':mode, 'last_completed_candle':stamp}})
                response = await run_chart_agent(request)
                return response, response['tools'][0]['result']
            with patch.object(chart_agent, 'model', model), patch.object(classroom, 'decide', decide), \
                    patch.object(classroom, 'previous_assessment', lookup):
                _, first = await assess(chat, 'replay', '2026-11-17T14:55:00Z')
                self.assertIsNone(first['previous_assessment'])
                _, live = await assess(chat, 'live', '2026-11-17T14:56:00Z')
                self.assertIsNone(live['previous_assessment'])
                _, failed = await assess(chat, 'replay', '2026-11-17T14:57:00Z')
                self.assertEqual(failed['status'], 'invalid_response')
                comparison, current = await assess(chat, 'replay', '2026-11-17T14:58:00Z')
                self.assertEqual(current['previous_assessment']['id'], first['id'])
                self.assertEqual(current['previous_assessment']['answer'], first['answer'])
                self.assertIn(first['id'], str(model.inputs[-1]))
                for text in ('14:55:00Z', '14:58:00Z', 'LONG (probability: 0.00)', 'WAIT (probability: unavailable)'):
                    self.assertIn(text, comparison['message'])
                empty, initial = await assess(other, 'replay', '2026-11-17T14:59:00Z')
                self.assertIsNone(initial['previous_assessment'])
                self.assertIn('No earlier AAPL assessment in replay mode', empty['message'])
                self.assertNotIn(first['id'], str(model.inputs[-1]))
                self.assertIsNone(lookup(str(chat), 'MSFT', 'replay'))
