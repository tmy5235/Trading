import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.indicators import add_sma, validate_windows
from backend.observations import connect, record
from backend.replay import replay

CLOSES = [13, 10, 7, 13, 19, 7, 1]
EXPECTED = [(None,None,None),(11.5,None,None),(8.5,10,None),(10,10,None),(16,13,'up'),(13,13,None),(4,9,'down')]
BASE = datetime(2026, 11, 9, 14, 30, tzinfo=timezone.utc)


def fixture(path):
    db = connect(path)
    try:
        # Eight ticks reveal seven ended one-minute candles; the last is forming.
        for index, price in enumerate(CLOSES + [1]):
            source = BASE + timedelta(minutes=index, seconds=1)
            record(db, {'id':'AAPL', 'time':str(int(source.timestamp()*1000)), 'price':price, 'day_volume':str(1000+index)}, (source+timedelta(seconds=1)).isoformat())
    finally:
        db.close()


def tuple_values(records):
    return [(entry['sma']['short'], entry['sma']['long'], entry['sma']['signal']) for entry in records]


class SMATests(unittest.TestCase):
    def test_reference_and_unfinished(self):
        entries = [{'candle': {'close': close}, 'ended': True, 'coverage': {'complete': True}} for close in CLOSES]
        self.assertEqual(tuple_values(add_sma(entries, 2, 3)), EXPECTED)
        forming = entries[:-1] + [{**entries[-1], 'ended': False}]
        self.assertEqual(tuple_values(add_sma(forming, 2, 3))[-1], (None,None,None))
        for pair in ((0,3), (3,3), (4,3), (-1,2), (1.5,3), (True,3)):
            with self.assertRaises(ValueError): validate_windows(*pair)

    def test_reference_pandas_rolling_means(self):
        # Independent pandas reference for both rolling windows, including warm-up.
        import pandas as pd
        entries = [{'candle': {'close': close}, 'ended': True, 'coverage': {'complete': True}} for close in CLOSES]
        calculated = add_sma(entries, 2, 3)
        series = pd.Series(CLOSES, dtype='float64')
        for window, key in ((2, 'short'), (3, 'long')):
            expected = series.rolling(window=window, min_periods=window).mean().tolist()
            for actual, reference in zip(calculated, expected):
                if pd.isna(reference): self.assertIsNone(actual['sma'][key])
                else: self.assertAlmostEqual(actual['sma'][key], reference)

    def test_full_precision_before_display_rounding(self):
        entries = [{'candle': {'close': close}, 'ended': True, 'coverage': {'complete': True}} for close in (1, 1, 1.00000001, 0.99999998)]
        result = add_sma(entries, 1, 2)
        self.assertEqual(result[-1]['sma']['signal'], 'down')
        self.assertEqual(f"{result[-1]['sma']['short']:.2f}", f"{result[-1]['sma']['long']:.2f}")

    def test_late_observation_recomputes_only_after_receipt(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'late.sqlite3'; fixture(path)
            db = connect(path)
            source = datetime(2026, 11, 9, 14, 31, 30, tzinfo=timezone.utc)
            record(db, {'id':'AAPL', 'time':str(int(source.timestamp()*1000)), 'price':11, 'day_volume':'1001'}, '2026-11-09T14:38:00+00:00')
            db.close()
            args = ('AAPL', '2026-11-09T14:30:00Z', '2026-11-09T14:40:00Z', '1m')
            before = add_sma(replay(*args, position=8, path=path)['records'], 2, 3)
            after = add_sma(replay(*args, position=9, path=path)['records'], 2, 3)
            self.assertEqual(before[1]['candle']['close'], 10)
            self.assertEqual(after[1]['candle']['close'], 11)
            self.assertEqual(before[2]['sma']['short'], 8.5)
            self.assertEqual(after[2]['sma']['short'], 9.0)

    def test_replay_each_reveal_matches_batch_and_no_future(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'sample.sqlite3'; fixture(path)
            start = '2026-11-09T14:30:00Z'; end = '2026-11-09T14:40:00Z'
            for position in range(1,9):
                result = replay('AAPL', start, end, '1m', position, path=path)
                calculated = add_sma(result['records'], 2, 3)
                completed = [x for x in calculated if x['ended']]
                self.assertEqual(tuple_values(completed), EXPECTED[:position-1])
                self.assertEqual(tuple_values(calculated)[-1], (None,None,None))
            self.assertEqual(tuple_values(completed), EXPECTED)


if __name__ == '__main__':
    unittest.main()
