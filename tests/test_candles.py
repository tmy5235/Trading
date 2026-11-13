import unittest
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from pathlib import Path

from backend.candles import one_minute_candles
from backend.observations import connect, record


class CandleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = connect(Path(self.tmp.name) / 'test.sqlite3')
        self.addCleanup(self.db.close)

    def add(self, time, price, total, ticker='TEST'):
        stamp = datetime.fromisoformat(f'2026-11-09T{time}+00:00')
        message = {'id': ticker, 'time': str(int(stamp.timestamp() * 1000)), 'price': price}
        if total is not None:
            message['day_volume'] = str(total)
        record(self.db, message, '2026-11-09T15:02:00+00:00')

    def candle(self, now='2026-11-09T15:00:55+00:00'):
        return one_minute_candles(self.db, 'TEST', now=datetime.fromisoformat(now))[-1]

    def test_late_observation_and_end_status(self):
        self.add('14:59:59', 99, 1000)
        self.add('15:00:05', 100, 1004)
        self.add('15:00:20', 103, 1009)
        self.add('15:00:50', 101, 1011)
        expected = {'timestamp': '2026-11-09T15:00:00Z', 'open': 100, 'high': 103, 'low': 100, 'close': 101, 'volume': 11}
        self.assertEqual(self.candle(), {'candle': expected, 'ended': False})
        self.assertEqual(self.candle('2026-11-09T15:01:00+00:00'), {'candle': expected, 'ended': True})
        self.db.execute('DELETE FROM receipts'); self.db.execute('DELETE FROM observations'); self.db.commit()
        self.add('14:59:59', 99, 1000)
        self.add('15:00:05', 100, 1004)
        self.add('15:00:50', 101, 1011)
        self.assertEqual(self.candle()['candle']['high'], 101)
        self.add('15:00:20', 103, 1009)
        self.assertEqual(self.candle('2026-11-09T15:01:00+00:00'), {'candle': expected, 'ended': True})

    def test_unknown_volume_missing_baseline_reset_and_missing_total(self):
        self.add('15:00:05', 100, 1004)
        self.assertIsNone(self.candle()['candle']['volume'])
        self.add('14:59:59', 99, 1000)
        self.add('15:00:20', 103, 5)
        self.assertIsNone(self.candle()['candle']['volume'])
        self.add('15:00:30', 102, None)
        self.assertIsNone(self.candle()['candle']['volume'])

    def test_repeat_and_same_timestamp_distinct_messages(self):
        self.add('14:59:59', 99, 1000)
        self.add('15:00:05', 100, 1004)
        self.add('15:00:05', 100, 1004)
        self.add('15:00:05', 103, 1005)
        candle = self.candle()['candle']
        self.assertEqual((candle['open'], candle['high'], candle['close']), (100, 103, 103))
        self.assertIsNone(candle['volume'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM receipts').fetchone()[0], 4)
        self.assertEqual(self.db.execute('SELECT count(*) FROM observations').fetchone()[0], 3)


if __name__ == '__main__':
    unittest.main()
