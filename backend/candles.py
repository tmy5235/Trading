"""Build one-minute candles from distinct saved Yahoo observations.

Source time is interpreted as Unix epoch milliseconds and day_volume as a
cumulative session total; these feed semantics are not yet provider-verified.
"""

from datetime import datetime, timedelta, timezone
import json
import math


def one_minute_candles(db, ticker, *, now=None, as_of_receipt=False):
    """Return [{'candle': six-field dict, 'ended': bool}, ...] for one ticker.

    Recomputes from stored observations so late arrivals are incorporated.
    An aware `now` controls the separate end-of-minute indicator.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('now must include a timezone')
    now = now.astimezone(timezone.utc)
    rows = db.execute(
        'SELECT o.id, o.source_time, o.message_json FROM observations o '
        'WHERE o.ticker = ? AND (? = 0 OR EXISTS '
        '(SELECT 1 FROM receipts r WHERE r.observation_id = o.id AND r.received_at_utc <= ?))',
        (ticker, int(as_of_receipt), now.isoformat()),
    ).fetchall()
    points = []
    for observation_id, raw_time, raw_message in rows:
        message = json.loads(raw_message)
        try:
            ms = int(raw_time)
            timestamp = datetime.fromtimestamp(ms // 1000, timezone.utc)
            price = float(message['price'])
            if not math.isfinite(price) or isinstance(message['price'], bool):
                raise ValueError('invalid price')
        except (TypeError, ValueError, OverflowError, OSError, KeyError) as error:
            raise ValueError(f'observation {observation_id} has invalid time or price') from error
        raw_volume = message.get('day_volume')
        try:
            if raw_volume is None or isinstance(raw_volume, bool):
                raise ValueError('missing or invalid volume')
            volume = int(raw_volume)
            if volume < 0 or str(volume) != str(raw_volume):
                raise ValueError('invalid volume')
        except (TypeError, ValueError, OverflowError):
            volume = None
        if as_of_receipt and ms > int(now.timestamp() * 1000):
            continue
        points.append((ms, observation_id, timestamp, price, volume))
    points.sort(key=lambda point: (point[0], point[1]))

    groups = {}
    for point in points:
        minute = point[2].replace(second=0, microsecond=0)
        groups.setdefault(minute, []).append(point)

    results = []
    previous = None
    for minute, group in groups.items():
        # A prior total is usable only within the same UTC date. A decrease,
        # missing total, or conflicting totals at one instant makes volume unknown.
        chain = ([previous] if previous is not None else []) + group
        volume = None
        if previous is not None and previous[2].date() == minute.date():
            totals = [point[4] for point in chain]
            if all(value is not None for value in totals):
                nondecreasing = all(a <= b for a, b in zip(totals, totals[1:]))
                unambiguous = all(a[0] != b[0] or a[4] == b[4] for a, b in zip(chain, chain[1:]))
                if nondecreasing and unambiguous:
                    volume = totals[-1] - totals[0]
        prices = [point[3] for point in group]
        results.append({
            'candle': {
                'timestamp': minute.isoformat().replace('+00:00', 'Z'),
                'open': prices[0], 'high': max(prices), 'low': min(prices),
                'close': prices[-1], 'volume': volume,
            },
            'ended': now >= minute + timedelta(minutes=1),
        })
        previous = group[-1]
    return results
