"""One-shot, read-only chart questions with a browser capture scoped to one run."""
import asyncio
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Literal
from uuid import UUID

from agents import (Agent, AgentsException, ModelSettings, RunConfig, RunContextWrapper,
                    Runner, ToolCallItem, ToolCallOutputItem, function_tool)
from agents.exceptions import ModelBehaviorError, ModelRefusalError, ModelTimeoutError
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError
from openai.types.responses import ResponseFunctionToolCall
from pydantic import BaseModel, Field, StringConstraints

from backend import classroom_decisions as classroom
from backend import strategy


class ChatLine(BaseModel):
    role: Literal['user', 'assistant']
    content: Annotated[str, StringConstraints(min_length=1, max_length=2400)]


class ChartAgentRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1500)]
    snapshot: classroom.ClassroomRequest | None = None
    history: list[ChatLine] = Field(default_factory=list, max_length=12)
    conversation_id: UUID | None = None


@dataclass
class ChartSession:
    snapshot: classroom.ClassroomRequest | None
    conversation_id: str | None = None
    previous: dict | None = None
    assessment: asyncio.Task | None = None


@function_tool(failure_error_function=None)
def lookup_workstation_rules(rule_set: Annotated[
    Literal['chart_signal', 'paper_strategy'],
    Field(description='chart_signal for candle direction labels, paper_strategy for SMA crossover actions.'),
]) -> dict:
    """Read the rules when asked to explain chart signal labels or the paper account's SMA strategy."""
    if rule_set == 'chart_signal':
        return {'rule_set': rule_set, 'question': classroom.QUESTION, 'limitations': classroom.LIMITATIONS}
    return {'rule_set': rule_set, 'strategy_id': strategy.STRATEGY_ID, 'rules': strategy.RULES,
            'decision_max_age_seconds': strategy.MAX_AGE_SECONDS}


@function_tool(failure_error_function=None)
async def assess_visible_chart(ctx: RunContextWrapper[ChartSession]) -> dict:
    """Request a new long, short, or wait Decisions API result when the user asks to assess the captured chart."""
    session = ctx.context
    snapshot = session.snapshot
    if snapshot is None:
        return {'status': 'missing_snapshot', 'detail': 'Capture the visible chart and send it with this request.'}
    if session.assessment is None:
        session.assessment = asyncio.create_task(asyncio.to_thread(
            classroom.decide, snapshot.image_url, snapshot.context.model_dump(), conversation_id=session.conversation_id
        ))
    try:
        result = await session.assessment
        if session.conversation_id and result['status'] == 'ok':
            return {**result, 'previous_assessment': session.previous}
        return result
    except (ValueError, OverflowError) as error:
        return {'status': 'invalid_snapshot', 'detail': str(error)}
    except sqlite3.Error as error:
        return {'status': 'storage_error', 'detail': getattr(error, 'sqlite_errorname', type(error).__name__)}


CLARIFICATION = 'Would you like me to explain the decision rules or assess the chart currently on screen?'

chart_agent = Agent[ChartSession](
    name='Market workstation chart reader',
    instructions=(
        'Choose the tool matching the request. For explanations, look up chart_signal rules '
        'for long/short/wait or paper_strategy rules for SMA crossovers and positions. '
        'Call assess_visible_chart only for an explicit request for a new chart interpretation. '
        'Base tool selection on the latest user message. Earlier messages provide context only. '
        'For an unclear request such as "Should I wait?", ask whether the user wants the wait rule '
        'explained or the visible chart assessed. In that case call no tools. Rule explanations alone '
        'must use only lookup_workstation_rules, even when a capture is attached. For an unsupported '
        'or unclear question, ask a brief clarifying question. Never infer a signal or probability '
        'from conversation text. When asked to run a decision and compare, assess the new capture once. '
        'Use only the supplied earlier tool record for the same conversation, ticker and mode as comparison '
        'evidence. A missing record means there is no earlier matching assessment. '
        'Report model choices as chart interpretations for this exercise. You cannot trade.'
    ),
    tools=[lookup_workstation_rules, assess_visible_chart],
    model_settings=ModelSettings(tool_choice='auto', parallel_tool_calls=False, timeout=30),
    tool_use_behavior='stop_on_first_tool',
)


def assessment_line(data):
    context = data.get('context', {})
    stamp = context.get('last_completed_candle') or context.get('forming_candle')
    if context and not stamp:
        stamp = 'chart time unavailable'
    chart = ' · '.join(value for value in [context.get('symbol'), context.get('interval'), stamp] if value)
    if data.get('status') != 'ok':
        reason = data.get('detail') or data['status'].replace('_', ' ')
        return f"{chart + ' · ' if chart else ''}Assessment failed ({reason})."
    answer = data['answer']
    probability = answer['probability']
    # Match the chart's JavaScript toFixed(2), including exact halfway values.
    formatted = (str(Decimal(probability).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
                 if probability is not None else 'unavailable')
    return f"{chart} · {answer['choice'].upper()} (probability: {formatted})"


def tool_summary(call):
    data = call['result']
    if call['name'] == lookup_workstation_rules.name:
        if data['rule_set'] == 'paper_strategy':
            return f"{data['rules']} A decision expires after {data['decision_max_age_seconds']} seconds."
        return 'Use the last two completed visible candles. ' + ' '.join(
            f"{item['value'].capitalize()} — {item['description']}" for item in data['question']['choices']
        )
    current = assessment_line(data)
    if 'previous_assessment' not in data:
        return current
    previous = data['previous_assessment']
    context = data['context']
    if previous is None:
        return (f"{current}\nNo earlier {context['symbol']} assessment in {context['mode']} mode "
                "in this conversation.")
    same = previous['answer']['choice'] == data['answer']['choice']
    return (f"Earlier · {assessment_line(previous)}\nCurrent · {current}\n"
            + ('The choice stayed the same.' if same else 'The choice changed.'))


async def run_chart_agent(request: ChartAgentRequest) -> dict:
    """Return the SDK's actual tool outputs, including application error results."""
    if re.fullmatch(r'(?:should (?:i|we) wait|what should (?:i|we) do)[.!?\s]*', request.question, re.I):
        return {'status': 'clarification', 'message': CLARIFICATION, 'tools': []}
    if not os.environ.get('OPENAI_API_KEY'):
        return {'status': 'missing_key', 'detail': 'Configure OPENAI_API_KEY on the backend.', 'tools': []}
    agent = chart_agent
    # Educational questions can carry a capture. Remove the assessment capability
    # for explicit rule/definition questions, even if the model selects it by mistake.
    asks_about_rules = re.search(
        r'\b(rules?|definitions?|criteria|what does (?:long|short|wait) mean|how (?:do|does) .+ work)\b',
        request.question, re.I)
    asks_for_analysis = re.search(
        r'(?:^|\b(?:please|can you|could you|would you|and|then)\s+)(?:also\s+)?'
        r'(?:assess|analy[sz]e|evaluate|classify|interpret|apply|review|check|run (?:a new |the |a )?decision)\b',
        request.question, re.I)
    references_chart = re.search(r'\b(chart|snapshot|screen|now|here|current|latest|visible|decision)\b',
                                 request.question, re.I)
    if not asks_for_analysis and (asks_about_rules or not references_chart):
        agent = chart_agent.clone(tools=[lookup_workstation_rules])
    messages = [line.model_dump() for line in request.history]
    session = ChartSession(request.snapshot, str(request.conversation_id) if request.conversation_id else None)
    if session.conversation_id and session.snapshot:
        context = session.snapshot.context
        try:
            session.previous = await asyncio.to_thread(
                classroom.previous_assessment, session.conversation_id, context.symbol, context.mode)
        except sqlite3.Error as error:
            return {'status': 'storage_error', 'detail': f'Could not read earlier assessments ({type(error).__name__}).',
                    'tools': []}
        messages.append({'role': 'user', 'content': 'Earlier matching assessment tool result from SQLite '
                         '(null means none): ' + json.dumps(session.previous)})
    messages.append({'role': 'user', 'content': request.question})
    try:
        run = await Runner.run(
            agent, messages, context=session, max_turns=1,
            run_config=RunConfig(tracing_disabled=True),
        )
    except (APITimeoutError, ModelTimeoutError):
        return {'status': 'timeout', 'detail': 'The agent model timed out.', 'tools': []}
    except APIStatusError as error:
        return {'status': 'provider_error', 'detail': f'The agent provider returned HTTP {error.status_code}.',
                'http_status': error.status_code, 'tools': []}
    except APIConnectionError:
        return {'status': 'connection_error', 'detail': 'Could not connect to the agent provider.', 'tools': []}
    except ModelRefusalError:
        return {'status': 'refused', 'detail': 'The agent model refused the request.', 'tools': []}
    except ModelBehaviorError:
        return {'status': 'invalid_model_reply', 'detail': 'The agent returned an invalid tool call.', 'tools': []}
    except (AgentsException, APIError) as error:
        return {'status': 'agent_error', 'detail': type(error).__name__, 'tools': []}

    calls = {item.raw_item.call_id: item.raw_item for item in run.new_items
             if isinstance(item, ToolCallItem) and isinstance(item.raw_item, ResponseFunctionToolCall)}
    outputs = [{'name': calls[item.call_id].name, 'arguments': json.loads(calls[item.call_id].arguments),
                'result': item.output} for item in run.new_items
               if isinstance(item, ToolCallOutputItem) and item.call_id in calls]
    if not outputs:
        message = run.final_output if isinstance(run.final_output, str) and run.final_output.strip() else CLARIFICATION
        return {'status': 'clarification', 'message': message, 'tools': []}
    return {'status': 'completed', 'message': '\n'.join(tool_summary(call) for call in outputs), 'tools': outputs}
