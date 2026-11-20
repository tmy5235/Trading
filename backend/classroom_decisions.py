"""Image-only classroom chart interpretation; never creates paper orders."""
import base64
import hashlib
import json
import math
import os
import socket
import sqlite3
import urllib.error
import uuid
from contextlib import closing
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

from backend.chart_decisions import decode_png
from backend.decisions import MODEL, post_decision
from backend.observations import DEFAULT_DB, connect
from backend.timeframes import parse_utc

QUESTION = {
    'type': 'choice', 'name': 'chart_signal',
    'instructions': ('For this hypothetical chart-reading exercise, inspect the last two completed visible candles. '
                     'Exclude the candle marked forming. Use the supplied last-completed and forming timestamps; for replay use the replay point rather than the wall-clock capture time. '
                     'Choose wait if two completed candles cannot be identified, evidence is unclear or insufficient, '
                     'or the supplied context marks it stale. The chart is a limited snapshot.'),
    'choices': [
        {'value': 'long', 'description': 'Both completed candles close above their own open.'},
        {'value': 'short', 'description': 'Both completed candles close below their own open.'},
        {'value': 'wait', 'description': 'Neither directional condition holds, either close equals its open, or evidence is insufficient, unclear, or stale.'},
    ],
}
LIMITATIONS = ('The chart shows partial quote coverage; volume is estimated from cumulative snapshots and may be delayed, revised, or unavailable. '
               'A completed candle is not necessarily complete market data. A replay is recorded data. No paper order is requested.')


class ClassroomContext(BaseModel):
    model_config = {'extra': 'forbid', 'frozen': True}
    symbol: Literal['AAPL']
    interval: Literal['1m', '5m', '15m', '1h', '1d']
    mode: Literal['live', 'replay']
    capture_time: str
    source_time: str | None = None
    replay_receipt_time: str | None = None
    last_completed_candle: str | None = None
    forming_candle: str | None = None
    feed_status: Literal['connected', 'disconnected', 'unknown']


class ClassroomRequest(BaseModel):
    model_config = {'extra': 'forbid', 'frozen': True}
    image_url: str
    context: ClassroomContext


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS classroom_chart_decisions (
      id TEXT PRIMARY KEY, created_at TEXT NOT NULL, png BLOB NOT NULL, png_sha256 TEXT NOT NULL,
      context_json TEXT NOT NULL, question_json TEXT NOT NULL, answer_json TEXT, status TEXT NOT NULL
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS classroom_chat_assessments (
        decision_id TEXT PRIMARY KEY REFERENCES classroom_chart_decisions(id),
        conversation_id TEXT NOT NULL
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS classroom_chat_by_conversation ON classroom_chat_assessments(conversation_id)')
    db.commit()


def build_request(context, png):
    # Explicitly allowlist metadata: do not send OHLCV arrays, account, or indicators.
    keys = ('symbol', 'interval', 'mode', 'data_source', 'capture_time', 'source_time', 'last_completed_candle',
            'forming_candle', 'feed_status', 'replay_receipt_time', 'is_stale')
    metadata = {k: context.get(k) for k in keys}
    return {'model': MODEL, 'input': [{'role': 'user', 'content': [
        {'type': 'input_image', 'image_url': 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')},
        {'type': 'input_json', 'data': metadata},
        {'type': 'input_text', 'text': LIMITATIONS},
    ]}], 'questions': [QUESTION]}


def parse_answer(response):
    if not isinstance(response, dict) or not isinstance(response.get('answers'), list):
        return 'invalid_response', None
    matches = [a for a in response['answers'] if isinstance(a, dict) and a.get('name') == 'chart_signal']
    if len(matches) != 1:
        return 'invalid_response', None
    answer = matches[0]
    if answer.get('type') == 'refusal':
        return 'refused', None
    choice = answer.get('choice')
    if answer.get('type') != 'choice' or choice not in ('long', 'short', 'wait'):
        return 'invalid_response', None
    probabilities = {}
    for entry in answer.get('probabilities', []) if isinstance(answer.get('probabilities', []), list) else []:
        if not isinstance(entry, dict) or entry.get('value') not in ('long', 'short', 'wait'):
            continue
        value, number = entry['value'], entry.get('probability')
        if value in probabilities:
            return 'invalid_response', None
        if isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number) and 0 <= number <= 1:
            probabilities[value] = number
    return 'ok', {'choice': choice, 'probability': probabilities.get(choice), 'probabilities': probabilities}


def decide(image_url, context, *, path=DEFAULT_DB, provider=None, api_key=None, now=None, conversation_id=None, persist=True, data_source=None):
    png = decode_png(image_url)
    now = now or datetime.now(timezone.utc)
    capture_time = parse_utc(context['capture_time'])
    if abs((now - capture_time).total_seconds()) > 120:
        raise ValueError('The capture is no longer current. Please capture the chart again.')
    context = dict(context)
    context['data_source'] = data_source or os.environ.get('TRADING_BOT_DATA_LABEL', 'Yahoo Finance')
    if context['mode'] == 'live':
        try:
            age = (capture_time - parse_utc(context['source_time'])).total_seconds()
            stale = age > 120 or age < -5 or context['feed_status'] != 'connected'
        except (ValueError, TypeError, KeyError, AttributeError):
            stale = True
        context['is_stale'] = stale
    else:
        try:
            replay_age = (parse_utc(context['replay_receipt_time']) - parse_utc(context['source_time'])).total_seconds()
            context['is_stale'] = replay_age > 120 or replay_age < -5
        except (ValueError, TypeError, KeyError, AttributeError):
            context['is_stale'] = True
    context['capture_time'] = capture_time.isoformat()
    request = build_request(context, png)
    key = api_key if api_key is not None else os.environ.get('OPENAI_API_KEY', '')
    status, answer = 'missing_key', None
    if key:
        try:
            result = (provider or post_decision)(request, key, 30)
            status, answer = parse_answer(result)
        except (TimeoutError, socket.timeout):
            status = 'timeout'
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            status = 'provider_error'
    decision_id = str(uuid.uuid4())
    sha = hashlib.sha256(png).hexdigest()
    if persist:
        with closing(connect(path)) as db:
            init(db)
            db.execute('INSERT INTO classroom_chart_decisions VALUES (?,?,?,?,?,?,?,?)',
                       (decision_id, now.isoformat(), png, sha, json.dumps(context), json.dumps(QUESTION),
                        json.dumps(answer) if answer is not None else None, status))
            if conversation_id is not None and status == 'ok':
                db.execute('INSERT INTO classroom_chat_assessments(decision_id,conversation_id) VALUES (?,?)',
                           (decision_id, conversation_id))
            db.commit()
    return {'id': decision_id, 'created_at': now.isoformat(), 'model': MODEL, 'status': status,
            'answer': answer, 'context': context, 'question': QUESTION, 'limitations': LIMITATIONS,
            'png_sha256': sha, 'image_url': f'/api/classroom-decisions/{decision_id}/chart.png' if persist else None}


def saved_result(row):
    decision_id, created, context, answer, status, sha = row
    return {'id': decision_id, 'created_at': created, 'context': json.loads(context),
            'answer': json.loads(answer) if answer else None, 'status': status, 'png_sha256': sha,
            'image_url': f'/api/classroom-decisions/{decision_id}/chart.png'}


def previous_assessment(conversation_id, symbol, mode, path=DEFAULT_DB):
    with closing(connect(path)) as db:
        init(db)
        row = db.execute('''
            SELECT d.id,d.created_at,d.context_json,d.answer_json,d.status,d.png_sha256
            FROM classroom_chat_assessments AS chat
            JOIN classroom_chart_decisions AS d ON d.id = chat.decision_id
            WHERE chat.conversation_id = ? AND json_extract(d.context_json, '$.symbol') = ?
              AND json_extract(d.context_json, '$.mode') = ? AND d.status = 'ok'
            ORDER BY chat.rowid DESC LIMIT 1
        ''', (conversation_id, symbol, mode)).fetchone()
    return saved_result(row) if row else None


def history(path=DEFAULT_DB):
    with closing(connect(path)) as db:
        init(db)
        rows = db.execute('SELECT id,created_at,context_json,answer_json,status,png_sha256 FROM classroom_chart_decisions ORDER BY rowid DESC LIMIT 20').fetchall()
    return [saved_result(row) for row in rows]
