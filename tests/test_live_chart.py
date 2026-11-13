import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.observations import connect, record
from backend.live_feed import init_status
from backend.live_history import snapshot


class LiveChartTests(unittest.TestCase):
    def test_forming_update_next_minute_and_timeframe(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'live.sqlite3'
            db = connect(path)
            init_status(db)
            def add(second, price, total):
                stamp = datetime(2026, 11, 9, 15, 0, second, tzinfo=timezone.utc)
                record(db, {'id': 'AAPL', 'time': str(int(stamp.timestamp() * 1000)), 'price': price, 'day_volume': str(total)}, stamp.isoformat())
            add(5, 100, 1004)
            with patch('backend.live_history.connect', lambda: connect(path)):
                a = snapshot('AAPL', '1m', datetime(2026,11,9,15,0,10,tzinfo=timezone.utc))
                self.assertEqual(a['records'][-1]['candle']['close'], 100)
                self.assertFalse(a['records'][-1]['ended'])
                add(20, 103, 1009)
                b = snapshot('AAPL', '1m', datetime(2026,11,9,15,0,25,tzinfo=timezone.utc))
                self.assertEqual(len(b['records']), 1)
                self.assertEqual(b['records'][-1]['candle']['close'], 103)
                self.assertEqual((b['latest_price'], b['observation_session']), (103, 'regular'))
                stamp = datetime(2026,11,9,15,1,3,tzinfo=timezone.utc)
                record(db, {'id':'AAPL', 'time':str(int(stamp.timestamp()*1000)), 'price':101, 'day_volume':'1011'}, stamp.isoformat())
                now = datetime(2026,11,9,15,1,4,tzinfo=timezone.utc)
                c = snapshot('AAPL', '1m', now)
                self.assertEqual(len(c['records']), 2)
                self.assertTrue(c['records'][0]['ended'])
                self.assertEqual(c['records'][1]['candle']['close'], 101)
                five = snapshot('AAPL', '5m', now)
                self.assertEqual(len(five['records']), 1)
                self.assertEqual(five['records'][0]['candle']['high'], 103)
                self.assertFalse(five['records'][0]['ended'])
                self.assertEqual(five['feed_status'], 'disconnected')
                outside=datetime(2026,11,10,2,0,tzinfo=timezone.utc)
                record(db, {'id':'AAPL','time':str(int(outside.timestamp()*1000)),'price':110},outside.isoformat())
                after=snapshot('AAPL','1m',datetime(2026,11,10,2,0,1,tzinfo=timezone.utc))
                self.assertEqual((after['latest_price'],after['observation_session']),(110,'outside regular session'))
                self.assertEqual(after['records'][-1]['candle']['close'],101)
            db.close()


if __name__ == '__main__':
    unittest.main()
