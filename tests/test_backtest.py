import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from backend.backtest import run
from backend.observations import connect, record
from tests.test_sma import BASE, CLOSES

class BacktestTests(unittest.TestCase):
    def test_known_trades_and_no_future_revision(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'test.sqlite3'; db=connect(path)
            for index,price in enumerate(CLOSES+[1,2]):
                source=BASE+timedelta(minutes=index,seconds=1)
                record(db,{'id':'AAPL','time':str(int(source.timestamp()*1000)),'price':price,'day_volume':str(1000+index)},(source+timedelta(seconds=1)).isoformat())
            # A correction arriving after the backtest range cannot affect its decisions.
            source=BASE+timedelta(minutes=1,seconds=20)
            record(db,{'id':'AAPL','time':str(int(source.timestamp()*1000)),'price':999},(BASE+timedelta(minutes=12)).isoformat())
            db.close()
            result=run('2026-11-09T14:30:00Z','2026-11-09T14:39:00Z',path=path)
            self.assertEqual([(t['action'],t['status'],t.get('price')) for t in result['trades']],[('buy','filled','1'),('close','filled','2')])
            self.assertEqual((result['cash'],result['shares'],result['realized'],result['total_pnl']),('10001',0,'1','1'))
            self.assertTrue(all(t['fill_time']>t['decision_time'] for t in result['trades']))
            self.assertEqual(result['receipts'],9)
            prefix=run('2026-11-09T14:30:00Z','2026-11-09T14:35:30Z',path=path)
            self.assertEqual(prefix['trades'][0]['status'],'unfilled')
            self.assertEqual(prefix['decisions'],result['decisions'][:len(prefix['decisions'])])

    def test_reject_bad_inputs(self):
        with self.assertRaises(ValueError): run('2026-11-09T14:30:00Z','2026-11-09T14:40:00Z',short=3,long=2)
        with self.assertRaises(ValueError): run('2026-11-09T14:30:00Z','2026-11-09T14:40:00Z',initial_cash='NaN')
