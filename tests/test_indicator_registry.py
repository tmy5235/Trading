import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from backend.indicators import indicator_values, add_instances
from backend.indicator_store import create_instance, list_instances, update_instance
from backend.observations import connect, record
from backend.replay import replay


def fixture(path):
    db = connect(path)
    try:
        base = datetime(2026, 11, 9, 14, 30, tzinfo=timezone.utc)
        tick = 0
        for minute, prices in enumerate(([9, 12, 11], [10, 15, 12], [11, 13, 12], [12])):
            for second, price in enumerate(prices, 1):
                source = base + timedelta(minutes=minute, seconds=second)
                record(db, {'id':'AAPL', 'time':str(int(source.timestamp()*1000)), 'price':price, 'day_volume':str(1000+tick)}, (source+timedelta(seconds=1)).isoformat())
                tick += 1
    finally:
        db.close()


class IndicatorTests(unittest.TestCase):
    def test_independent_instances_persistence_and_range(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'fixture.sqlite3'; fixture(path)
            one = create_instance('Range 2', 'price_range', {'window': 2}, path)
            two = create_instance('Range 3', 'price_range', {'window': 3}, path)
            self.assertEqual(list_instances(path), [one, two])
            with self.assertRaisesRegex(ValueError, 'positive whole number'):
                create_instance('Invalid', 'price_range', {'window': 0}, path)
            self.assertEqual(list_instances(path), [one, two])
            result = replay('AAPL','2026-11-09T14:30:00Z','2026-11-09T14:40:00Z','1m',10,path=path)
            records = add_instances(result['records'], list_instances(path))
            self.assertEqual(records[2]['indicators'], {str(one['id']):5.0, str(two['id']):6.0})
            self.assertIsNone(records[0]['indicators'][str(one['id'])])
            self.assertTrue(all(v is None for v in records[-1]['indicators'].values()))
            self.assertEqual([float(v) if v is not None else None for v in indicator_values(records,'sma',{'window':2})], [None, 11.5, 12.0, None])
            sma = create_instance('SMA 2', 'sma', {'window': 2}, path)
            with_sma = add_instances(result['records'], list_instances(path))
            self.assertEqual(with_sma[2]['indicators'][str(sma['id'])], 12.0)
            update_instance(sma['id'], {'window':3},path)
            self.assertEqual(add_instances(result['records'],list_instances(path))[2]['indicators'][str(sma['id'])], 35/3)
            with self.assertRaises(ValueError): update_instance(sma['id'], {'window':0},path)
            self.assertEqual(list_instances(path)[-1]['settings']['window'],3)


if __name__ == '__main__':
    unittest.main()
