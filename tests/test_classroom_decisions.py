import base64
import json
import unittest
from contextlib import closing
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.classroom_decisions import build_request, decide, history, parse_answer
from backend.observations import connect
from backend.paper import account, initialize
from tests.test_chart_decisions import png

NOW = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)
CONTEXT = {'symbol':'AAPL', 'interval':'1m', 'mode':'live', 'capture_time':NOW.isoformat(),
           'source_time':'2026-11-17T14:59:55Z', 'last_completed_candle':'2026-11-17T14:58:00Z',
           'forming_candle':'2026-11-17T14:59:00Z', 'feed_status':'connected'}
URL = 'data:image/png;base64,' + base64.b64encode(png()).decode()


def reply(value='long', probabilities=None):
    return {'answers':[{'name':'chart_signal', 'type':'choice', 'choice':value,
                        'probabilities': probabilities if probabilities is not None else [
                            {'value':'wait','probability':0.6}, {'value':'long','probability':0.0},
                            {'value':'short','probability':0.4}]}]}


class ClassroomDecisionTests(unittest.TestCase):
    def test_named_probability_and_response_errors(self):
        status, answer = parse_answer(reply())
        self.assertEqual((status,answer['choice'],answer['probability']),('ok','long',0.0))
        self.assertIsNone(parse_answer(reply('wait',[]))[1]['probability'])
        self.assertEqual(parse_answer(reply('hold'))[0], 'invalid_response')
        self.assertEqual(parse_answer({'answers':[{'type':'refusal','name':'chart_signal'}]})[0], 'refused')
        self.assertEqual(parse_answer({'answers':[]})[0], 'invalid_response')
        self.assertEqual(parse_answer(reply('wait',[{'value':'wait','probability':.2},{'value':'wait','probability':.8}]))[0], 'invalid_response')

    def test_image_only_payload_storage_staleness_and_no_orders(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'fixture.sqlite3'
            with closing(connect(path)) as db:
                initialize(db)
            before = account(path)
            captured = []
            def provider(body, key, timeout):
                captured.append(body)
                return reply()
            result = decide(URL, CONTEXT, path=path, provider=provider, api_key='fixture-only', now=NOW)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['answer']['probability'],0.0)
            self.assertFalse(result['context']['is_stale'])
            parts = captured[0]['input'][0]['content']
            self.assertEqual([p['type'] for p in parts],['input_image','input_json','input_text'])
            self.assertEqual(base64.b64decode(parts[0]['image_url'].split(',')[1]),png())
            self.assertNotIn('candle', parts[1]['data'])
            self.assertNotIn('chart_records', parts[1]['data'])
            self.assertNotIn('account', parts[1]['data'])
            self.assertEqual(captured[0]['questions'][0]['name'],'chart_signal')
            self.assertEqual([c['value'] for c in captured[0]['questions'][0]['choices']],['long','short','wait'])
            stale = decide(URL,{**CONTEXT,'feed_status':'disconnected'},path=path,provider=provider,api_key='fixture-only',now=NOW)
            self.assertTrue(stale['context']['is_stale'])
            self.assertEqual(stale['answer']['choice'],'long')  # do not replace the actual provider answer
            replay = decide(URL,{**CONTEXT,'mode':'replay','feed_status':'disconnected','replay_receipt_time':'2026-11-17T14:59:56Z'},path=path,provider=provider,api_key='fixture-only',now=NOW)
            self.assertFalse(replay['context']['is_stale'])
            old_replay = decide(URL,{**CONTEXT,'mode':'replay','feed_status':'disconnected','replay_receipt_time':'2026-11-17T15:05:00Z'},path=path,provider=provider,api_key='fixture-only',now=NOW)
            self.assertTrue(old_replay['context']['is_stale'])
            with closing(connect(path)) as db:
                row=db.execute('SELECT png,context_json,answer_json FROM classroom_chart_decisions WHERE id=?',(result['id'],)).fetchone()
                self.assertEqual(row[0],png())
                self.assertEqual(json.loads(row[2]),result['answer'])
                self.assertEqual(db.execute('SELECT count(*) FROM paper_orders').fetchone()[0],0)
            self.assertEqual(account(path),before)
            self.assertEqual(history(path)[-1]['id'],result['id'])
            failure=decide(URL,CONTEXT,path=path,provider=lambda *_: (_ for _ in ()).throw(urllib.error.URLError('offline')),api_key='fixture-only',now=NOW)
            self.assertEqual(failure['status'],'provider_error')
            self.assertIsNone(failure['answer'])
            with self.assertRaisesRegex(ValueError,'Unreadable'):
                decide('data:image/png;base64,YWJj',CONTEXT,path=path,api_key='fixture-only',now=NOW)


if __name__ == '__main__': unittest.main()
