"""Versioned Yahoo one-minute bars, separate from streaming observations."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import math

import yfinance as yf
from backend.observations import connect
from backend.timeframes import parse_utc


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS yahoo_minute_bars (
        id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, timestamp TEXT NOT NULL,
        open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
        volume INTEGER, received_at_utc TEXT NOT NULL
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS yahoo_minute_bars_lookup ON yahoo_minute_bars(symbol,timestamp,received_at_utc)')
    db.commit()


def save_history(db, symbol, candles, received_at):
    """Save changed completed Yahoo bars with their actual availability time."""
    observed = parse_utc(received_at)
    init(db)
    inserted = 0
    with db:
        for candle in candles:
            t = parse_utc(candle['timestamp'])
            if t.second or t.microsecond or t + timedelta(minutes=1) > observed:
                continue
            prices = [float(candle[k]) for k in ('open','high','low','close')]
            volume = candle['volume']
            if (not all(math.isfinite(v) and v > 0 for v in prices) or
                prices[1] < max(prices[0], prices[3]) or prices[2] > min(prices[0], prices[3]) or
                prices[1] < prices[2] or (volume is not None and (isinstance(volume, bool) or not isinstance(volume, int) or volume < 0))):
                continue
            stamp = t.isoformat().replace('+00:00', 'Z')
            prior = db.execute('SELECT open,high,low,close,volume FROM yahoo_minute_bars WHERE symbol=? AND timestamp=? ORDER BY id DESC LIMIT 1', (symbol,stamp)).fetchone()
            values = (*prices, volume)
            if prior != values:
                db.execute('INSERT INTO yahoo_minute_bars(symbol,timestamp,open,high,low,close,volume,received_at_utc) VALUES (?,?,?,?,?,?,?,?)', (symbol,stamp,*values,observed.isoformat()))
                inserted += 1
    return inserted


def refresh(symbol, database):
    frame = yf.Ticker(symbol).history(period='1d', interval='1m', prepost=False, auto_adjust=False, actions=False, timeout=12)
    received = datetime.now(timezone.utc).isoformat()
    if frame.empty:
        return 0, 'Yahoo returned no one-minute history'
    candles=[]
    for timestamp, row in frame.iterrows():
        try:
            t=timestamp.to_pydatetime().astimezone(timezone.utc)
            values=[float(row[k]) for k in ('Open','High','Low','Close')]
            raw_volume=float(row['Volume'])
            volume=int(raw_volume) if math.isfinite(raw_volume) and raw_volume >= 0 and raw_volume.is_integer() else None
            candles.append(dict(zip(('timestamp','open','high','low','close','volume'), (t.isoformat(),*values,volume))))
        except (ValueError, TypeError, OverflowError, KeyError):
            continue
    with closing(connect(database)) as db:
        return save_history(db, symbol, candles, received), None


def available(db, symbol, now):
    """Return the latest revision known at `now`; historical rows are never observations."""
    exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='yahoo_minute_bars'").fetchone()
    if not exists:
        return []
    rows=db.execute('''SELECT timestamp,open,high,low,close,volume FROM (
        SELECT *, ROW_NUMBER() OVER(PARTITION BY timestamp ORDER BY received_at_utc DESC,id DESC) AS n
        FROM yahoo_minute_bars WHERE symbol=? AND received_at_utc<=?
    ) WHERE n=1 ORDER BY timestamp''', (symbol,now.isoformat())).fetchall()
    return [{'timestamp':ts, 'open':op, 'high':hi, 'low':lo, 'close':cl, 'volume':vol} for ts,op,hi,lo,cl,vol in rows if parse_utc(ts)+timedelta(minutes=1)<=now]
