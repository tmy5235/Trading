"""Read-only, receipt-ordered replay of saved observations."""
import json
from contextlib import closing
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.candles import one_minute_candles
from backend.live_history import schedule, chart_records
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
        ranges = {symbol: [parse_utc(start), parse_utc(end)] for symbol, start, end in rows}
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='yahoo_minute_bars'").fetchone():
            for symbol, start, end in db.execute('SELECT symbol, MIN(timestamp), MAX(received_at_utc) FROM yahoo_minute_bars GROUP BY symbol'):
                first, last = parse_utc(start), parse_utc(end)
                if symbol in ranges:
                    ranges[symbol] = [min(first, ranges[symbol][0]), max(last, ranges[symbol][1])]
                else:
                    ranges[symbol] = [first, last]
    return [{'symbol': symbol, 'start': first.isoformat(), 'end': (last + timedelta(microseconds=1)).isoformat()}
            for symbol, (first, last) in sorted(ranges.items())]


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


def chart_snapshot(symbol, start, end, timeframe, path=DEFAULT_DB, *, short=2, long=3):
    """Show the saved chart as it was known at the cutoff, without advancing orders."""
    start, end = parse_utc(start), parse_utc(end)
    if end <= start:
        raise ValueError('End must be after start')
    if symbol.upper() != 'AAPL':
        raise ValueError('Only AAPL is configured')
    if not Path(path).exists():
        return {'symbol':symbol, 'timeframe':timeframe, 'as_of':end.isoformat(), 'records':[], 'event':None}
    # Do not imply future market observations are available.
    as_of = min(end, datetime.now(timezone.utc))
    if start >= as_of:
        raise ValueError('Choose a start before the current time')
    with closing(read_only(path)) as db:
        db.execute('BEGIN')  # One read snapshot for candles and event metadata.
        records = chart_records(db, symbol.upper(), timeframe, as_of, start=start)
        from backend.indicators import add_instances, add_sma, validate_windows
        validate_windows(short, long)
        has_indicators = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='indicator_instances'").fetchone()
        instances = [{'id':i, 'name':name, 'type':kind, 'settings':json.loads(settings)}
                     for i,name,kind,settings in db.execute('SELECT id,name,type,settings_json FROM indicator_instances ORDER BY id')] if has_indicators else []
        records = add_instances(add_sma(records, short, long), instances)
        last = db.execute("""SELECT r.id,r.received_at_utc,o.id,o.source_time
            FROM receipts r JOIN observations o ON o.id=r.observation_id
            WHERE o.ticker=? AND r.received_at_utc>=? AND r.received_at_utc<=?
              AND CAST(o.source_time AS INTEGER)<=? ORDER BY r.id DESC LIMIT 1""",
            (symbol.upper(), start.isoformat(), as_of.isoformat(), int(as_of.timestamp()*1000))).fetchone()
        event = None
        if last:
            try:
                event = {'receipt_id':last[0], 'received_at':last[1], 'observation_id':last[2],
                         'source_time_utc':datetime.fromtimestamp(int(last[3])/1000, timezone.utc).isoformat()}
            except (ValueError, TypeError, OSError, OverflowError):
                event = None
        return {'symbol':symbol.upper(), 'timeframe':timeframe, 'as_of':as_of.isoformat(),
                'records':records, 'event':event, 'source':'Recorded Yahoo data'}
