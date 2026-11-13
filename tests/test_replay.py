import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from backend.observations import connect, record
from backend.replay import replay


class ReplayTests(unittest.TestCase):
    def test_arrival_order_late_data_repeat_and_read_only(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'test.sqlite3'
            db = connect(path)
            messages = [
                {'id':'AAPL','time':'1794236399000','price':99,'day_volume':'1000'},
                {'id':'AAPL','time':'1794236405000','price':100,'day_volume':'1004'},
                {'id':'AAPL','time':'1794236450000','price':101,'day_volume':'1011'},
                {'id':'AAPL','time':'1794236420000','price':103,'day_volume':'1009'},
            ]
            for n, message in enumerate(messages):
                record(db, message, f'2026-11-09T15:01:0{n}+00:00')
            record(db, messages[-1], '2026-11-09T15:01:04+00:00')
            counts = (db.execute('select count(*) from observations').fetchone()[0], db.execute('select count(*) from receipts').fetchone()[0])
            db.close()
            args = ('AAPL', '2026-11-09T15:01:00Z', '2026-11-09T15:01:10Z', '1m')
            first = replay(*args, position=3, path=path)
            self.assertEqual(first['records'][-1]['candle']['high'], 101)
            final = replay(*args, position=5, cutoff=first['cutoff'], path=path)
            self.assertEqual(final['count'], 5)
            self.assertEqual(final['event']['source_time_utc'], '2026-11-09T15:00:20+00:00')
            self.assertEqual(final['records'][-1]['candle'], {'timestamp':'2026-11-09T15:00:00Z','open':100,'high':103,'low':100,'close':101,'volume':11})
            self.assertEqual(final, replay(*args, position=5, cutoff=first['cutoff'], path=path))
            db = connect(path)
            self.assertEqual(counts, (db.execute('select count(*) from observations').fetchone()[0], db.execute('select count(*) from receipts').fetchone()[0]))
            db.close()
            self.assertEqual(replay('AAPL', '2026-11-10T00:00:00Z', '2026-11-10T01:00:00Z', '1m', path=path)['count'], 0)


if __name__ == '__main__':
    unittest.main()
