"""Read-only, receipt-ordered replay of saved observations."""
import json
from contextlib import closing
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.candles import one_minute_candles
from backend.live_history import schedule
from backend.observations import DEFAULT_DB, connect, record
from backend.timeframes import aggregate, parse_utc

MAX_RECEIPTS = 5000


def read_only(path=DEFAULT_DB):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def options(path=DEFAULT_DB):
    if not Path(path).exists():
        return []
    with closing(read_only(path)) as db:
        rows = db.execute('SELECT o.ticker, MIN(r.received_at_utc), MAX(r.received_at_utc) FROM observations o JOIN receipts r ON r.observation_id=o.id GROUP BY o.ticker ORDER BY o.ticker').fetchall()
    return [{'symbol': symbol, 'start': start, 'end': (datetime.fromisoformat(end) + timedelta(microseconds=1)).isoformat()} for symbol, start, end in rows]


def replay(symbol, start, end, timeframe, position=0, cutoff=None, path=DEFAULT_DB):
    start, end = parse_utc(start), parse_utc(end)
    if end <= start:
        raise ValueError('end must be after start')
    if position < 0:
        raise ValueError('position must be non-negative')
    if not Path(path).exists():
        return {'symbol': symbol, 'timeframe': timeframe, 'count': 0, 'position': 0, 'cutoff': 0, 'event': None, 'records': []}
    with closing(read_only(path)) as db:
        # Receipt IDs define the replay order. Freeze the upper ID for repeatable steps.
        if cutoff is None:
            cutoff = db.execute('SELECT COALESCE(MAX(id), 0) FROM receipts').fetchone()[0]
        rows = db.execute('SELECT r.id, r.received_at_utc, o.id, o.message_json FROM receipts r JOIN observations o ON o.id=r.observation_id WHERE o.ticker=? AND r.id<=? ORDER BY r.id', (symbol, cutoff)).fetchall()
    rows = [row for row in rows if start <= parse_utc(row[1]) < end]
    if len(rows) > MAX_RECEIPTS:
        raise ValueError(f'selection exceeds {MAX_RECEIPTS} receipts; choose a shorter range')
    if position > len(rows):
        raise ValueError('position exceeds selected receipt count')
    result = {'symbol': symbol, 'timeframe': timeframe, 'count': len(rows), 'position': position, 'cutoff': cutoff, 'event': None, 'records': []}
    if not position:
        return result
    mem = connect(':memory:')
    try:
        for receipt_id, received_at, observation_id, raw in rows[:position]:
            record(mem, json.loads(raw), received_at)
        receipt_id, received_at, observation_id, raw = rows[position - 1]
        message = json.loads(raw)
        as_of = parse_utc(received_at)
        minutes = one_minute_candles(mem, symbol, now=as_of)
        if minutes:
            stamps = [parse_utc(item['candle']['timestamp']) for item in minutes]
            sessions = schedule((min(stamps) - timedelta(days=2)).date().isoformat(), (max(stamps) + timedelta(days=2)).date().isoformat())
            result['records'] = aggregate([{'candle': item['candle'], 'available_at': as_of} for item in minutes], sessions, timeframe, as_of, include_forming=True)
        result['event'] = {'receipt_id': receipt_id, 'observation_id': observation_id,
                           'received_at': received_at, 'source_time_raw': message.get('time'),
                           'source_time_utc': datetime.fromtimestamp(int(message['time']) / 1000, timezone.utc).isoformat(),
                           'price': message.get('price')}
    finally:
        mem.close()
    return result
