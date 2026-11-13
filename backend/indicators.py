"""Registry of completed-candle indicators and confirmed SMA crossovers."""
from collections import deque
from fractions import Fraction


def validate_window(settings):
    if set(settings) != {'window'}:
        raise ValueError('Settings must contain only window')
    window = settings['window']
    if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
        raise ValueError('Window must be a positive whole number')
    return window


def sma_value(candles, settings):
    return sum(Fraction(str(c['close'])) for c in candles) / len(candles)


def range_value(candles, settings):
    return max(Fraction(str(c['high'])) for c in candles) - min(Fraction(str(c['low'])) for c in candles)


# Add a type by registering metadata, setting validation, and a calculation.
# The validator returns the window size; the calculation receives exactly that
# many completed candles and validated settings and returns a numeric value.
INDICATOR_TYPES = {
    'sma': {'label': 'Simple moving average', 'units': 'price', 'settings': [{'key': 'window', 'label': 'Window', 'type': 'integer', 'minimum': 1, 'default': 2}], 'validate': validate_window, 'calculate': sma_value},
    'price_range': {'label': 'Rolling price range', 'units': 'price difference', 'settings': [{'key': 'window', 'label': 'Window', 'type': 'integer', 'minimum': 1, 'default': 2}], 'validate': validate_window, 'calculate': range_value},
}


def types_metadata():
    return [{'type': key, 'label': spec['label'], 'units': spec['units'], 'settings': spec['settings']} for key, spec in INDICATOR_TYPES.items()]


def indicator_values(records, kind, settings):
    if kind not in INDICATOR_TYPES:
        raise ValueError('Unknown indicator type')
    window = INDICATOR_TYPES[kind]['validate'](settings)
    candles = deque(maxlen=window)
    values = []
    for entry in records:
        value = None
        if entry['ended'] and entry['coverage']['complete']:
            candles.append(entry['candle'])
            if len(candles) == window:
                value = INDICATOR_TYPES[kind]['calculate'](candles, settings)
        values.append(value)
    return values


def validate_windows(short, long):
    validate_window({'window': short})
    validate_window({'window': long})
    if short >= long:
        raise ValueError('SMA windows must be positive whole numbers with short smaller than long')


def add_sma(records, short, long):
    validate_windows(short, long)
    short_values = indicator_values(records, 'sma', {'window': short})
    long_values = indicator_values(records, 'sma', {'window': long})
    previous_side = None
    result = []
    for entry, short_value, long_value in zip(records, short_values, long_values):
        signal = None
        if short_value is not None and long_value is not None:
            side = (short_value > long_value) - (short_value < long_value)
            if side:
                if previous_side is not None and side != previous_side:
                    signal = 'up' if side > 0 else 'down'
                previous_side = side
        result.append({**entry, 'sma': {'short': float(short_value) if short_value is not None else None,
                                       'long': float(long_value) if long_value is not None else None,
                                       'signal': signal}})
    return result


def add_instances(records, instances):
    result = [{**record, 'indicators': {}} for record in records]
    for instance in instances:
        values = indicator_values(records, instance['type'], instance['settings'])
        for entry, value in zip(result, values):
            entry['indicators'][str(instance['id'])] = float(value) if value is not None else None
    return result
