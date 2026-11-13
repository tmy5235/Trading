"""Aggregate minute records against explicit UTC regular-session boundaries."""
from datetime import datetime, timedelta, timezone

DURATIONS = {'1m': 1, '5m': 5, '15m': 15, '1h': 60, '1d': None}


def parse_utc(value):
    timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError('timestamps must have a timezone')
    return timestamp.astimezone(timezone.utc)


def aggregate(minutes, sessions, timeframe, as_of, *, include_forming=False):
    """Aggregate available minute revisions; sessions are (open, close) datetimes.

    Each input is {'candle': six fields, 'available_at': aware datetime}.
    The last available record for a timestamp replaces earlier revisions.
    """
    if timeframe not in DURATIONS:
        raise ValueError('unsupported timeframe')
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError('as_of must include a timezone')
    as_of = as_of.astimezone(timezone.utc)
    latest = {}
    for index, item in enumerate(minutes):
        candle = item['candle']
        start = parse_utc(candle['timestamp'])
        if start.second or start.microsecond:
            raise ValueError('minute timestamps must be aligned to the minute')
        available = item['available_at']
        if available.tzinfo is None or available.utcoffset() is None:
            raise ValueError('available_at must include a timezone')
        available = available.astimezone(timezone.utc)
        if available > as_of or start > as_of or (not include_forming and start + timedelta(minutes=1) > as_of):
            continue
        key = (available, index)
        if start not in latest or key > latest[start][0]:
            latest[start] = (key, candle)
    grouped = {}
    for start, (_, candle) in sorted(latest.items()):
        session = next(((op, cl) for op, cl in sessions if op <= start < cl), None)
        if session is None:
            continue
        op, cl = session
        size = DURATIONS[timeframe]
        group_start = op if size is None else op + timedelta(minutes=(int((start - op).total_seconds() // 60) // size) * size)
        group_end = cl if size is None else min(group_start + timedelta(minutes=size), cl)
        grouped.setdefault((group_start, group_end), []).append(candle)
    result = []
    for (start, end), data in sorted(grouped.items()):
        volumes = [c['volume'] for c in data]
        expected = int((end - start).total_seconds() // 60)
        result.append({
            'candle': {'timestamp': start.isoformat().replace('+00:00', 'Z'),
                       'open': data[0]['open'], 'high': max(c['high'] for c in data),
                       'low': min(c['low'] for c in data), 'close': data[-1]['close'],
                       'volume': None if any(v is None for v in volumes) else sum(volumes)},
            'coverage': {'minutes_present': len(data), 'minutes_expected': expected,
                         'complete': len(data) == expected},
            'ended': as_of >= end,
        })
    return result
