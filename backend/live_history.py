"""Derive live chart history from the recorder and regular-session calendar."""
from datetime import datetime, timedelta, timezone
import json
import math
from functools import lru_cache
import exchange_calendars as xcals
from backend.candles import one_minute_candles
from backend.observations import connect
from backend.timeframes import aggregate, parse_utc
from backend.live_feed import init_status


@lru_cache(maxsize=8)
def schedule(first, last):
    table = xcals.get_calendar('XNAS', start=first, end=last).schedule
    return tuple((r['open'].to_pydatetime(), r['close'].to_pydatetime()) for _, r in table.iterrows())


def snapshot(symbol, timeframe, now=None):
    now = now or datetime.now(timezone.utc)
    db = connect()
    try:
        init_status(db)
        minutes = one_minute_candles(db, symbol, now=now, as_of_receipt=True)
        row = db.execute('SELECT state, updated_at FROM feed_status WHERE symbol=?', (symbol,)).fetchone()
        status = 'disconnected'
        if row and (now - datetime.fromisoformat(row[1])).total_seconds() < 8:
            status = row[0]
        last = db.execute('SELECT o.id, o.source_time, o.message_json, r.received_at_utc FROM observations o JOIN receipts r ON r.observation_id=o.id WHERE o.ticker=? AND r.received_at_utc <= ? ORDER BY r.id DESC LIMIT 1', (symbol, now.isoformat())).fetchone()
        source_time = None
        latest_price = None
        observation_session = None
        if last:
            try:
                source_dt = datetime.fromtimestamp(int(last[1]) / 1000, timezone.utc)
                source_time = source_dt.isoformat()
                sessions_at_source = schedule((source_dt-timedelta(days=2)).date().isoformat(), (source_dt+timedelta(days=2)).date().isoformat())
                observation_session = 'regular' if any(op <= source_dt < cl for op,cl in sessions_at_source) else 'outside regular session'
                raw_price = json.loads(last[2]).get('price')
                if isinstance(raw_price, (int,float)) and not isinstance(raw_price, bool) and math.isfinite(raw_price):
                    latest_price = raw_price
            except (TypeError, ValueError, OSError, OverflowError):
                pass
        if not minutes:
            return {'symbol': symbol, 'timeframe': timeframe, 'source': 'Yahoo Finance', 'feed_status': status, 'source_time': source_time, 'observation_id': last[0] if last else None, 'latest_price': latest_price, 'receipt_time': last[3] if last else None, 'observation_session': observation_session, 'records': []}
        stamps = [parse_utc(x['candle']['timestamp']) for x in minutes]
        sessions = schedule((min(stamps) - timedelta(days=2)).date().isoformat(), (max(stamps) + timedelta(days=2)).date().isoformat())
        records = aggregate([{'candle': x['candle'], 'available_at': now} for x in minutes], sessions, timeframe, now, include_forming=True)
        return {'symbol': symbol, 'timeframe': timeframe, 'source': 'Yahoo Finance', 'feed_status': status, 'source_time': source_time, 'observation_id': last[0] if last else None, 'latest_price': latest_price, 'receipt_time': last[3] if last else None, 'observation_session': observation_session, 'records': records}
    finally:
        db.close()
