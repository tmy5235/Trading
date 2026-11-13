import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from backend.observations import connect, record
from backend.paper import initialize, execute_action, apply_receipt, drain_live, account


def fixture(path):
    db = connect(path)
    initialize(db)
    start = datetime(2026, 11, 9, 14, 30, tzinfo=timezone.utc)
    for n, price in enumerate((100,110,105)):
        t = start + timedelta(minutes=n)
        record(db, {'id':'AAPL','time':str(int(t.timestamp()*1000)),'price':price,'day_volume':str(1000+n)}, (t+timedelta(seconds=1)).isoformat())
    db.close()


class PaperTests(unittest.TestCase):
    def test_replay_sequence_retry_and_restart(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'paper.sqlite3'; fixture(path); db=connect(path); initialize(db)
            def submit(req, side, qty=None, cursor=0):
                return execute_action(db, request_id=req,symbol='AAPL',action=side,quantity=qty,mode='replay',scope='session',cursor=cursor)
            buy=submit('buy-1','buy',4)
            self.assertEqual(buy['status'],'pending')
            self.assertTrue(submit('buy-1','buy',4)['duplicate'])
            self.assertEqual(apply_receipt(db,1,'replay','session')[0]['status'],'filled')
            self.assertEqual(apply_receipt(db,1,'replay','session'),[])
            self.assertEqual((account(path)['cash'],account(path)['positions'][0]['shares']),('600',4))
            self.assertEqual(submit('sell-1','sell',1,1)['status'],'pending')
            apply_receipt(db,2,'replay','session')
            self.assertEqual((account(path)['cash'],account(path)['positions'][0]['shares']),('710',3))
            self.assertEqual(submit('close-1','close',cursor=2)['status'],'pending')
            apply_receipt(db,3,'replay','session')
            state=account(path)
            self.assertEqual((state['cash'],state['positions'][0]['shares']),('1025',0))
            self.assertEqual(submit('bad-sale','sell',1,3)['reason'],'Insufficient shares')
            self.assertIsNone(submit('wait','wait')['order_id'])
            self.assertTrue(submit('wait','wait')['duplicate'])
            with self.assertRaisesRegex(ValueError, 'different action'):
                submit('wait','buy',1)
            self.assertIsNone(submit('hold','hold')['order_id'])
            self.assertEqual(db.execute('SELECT count(*) FROM paper_fills').fetchone()[0],3)
            db.close(); db=connect(path)
            self.assertEqual(db.execute('SELECT count(*) FROM paper_fills').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT count(*) FROM paper_orders').fetchone()[0],4)
            db.close()

    def test_live_recovery_and_fill_time_share_recheck(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'paper.sqlite3'; fixture(path); db=connect(path); initialize(db)
            execute_action(db,request_id='b',symbol='AAPL',action='buy',quantity=4)
            drain_live(db)
            self.assertEqual(account(path)['orders'][0]['status'], 'pending')
            t = datetime(2026,11,9,14,33,tzinfo=timezone.utc)
            record(db, {'id':'AAPL','time':str(int(t.timestamp()*1000)),'price':100}, t.isoformat())
            db.close()
            db=connect(path); initialize(db); drain_live(db)
            self.assertEqual(account(path)['cash'], '600')
            execute_action(db,request_id='s1',symbol='AAPL',action='sell',quantity=3)
            execute_action(db,request_id='s2',symbol='AAPL',action='sell',quantity=3)
            t += timedelta(minutes=1)
            record(db, {'id':'AAPL','time':str(int(t.timestamp()*1000)),'price':110}, t.isoformat())
            drain_live(db)
            state=account(path)
            self.assertEqual(state['cash'], '930')
            self.assertEqual(state['positions'][0]['shares'], 1)
            self.assertEqual(state['orders'][0]['reason'], 'Insufficient shares at fill')
            self.assertEqual(db.execute('select count(*) from paper_fills').fetchone()[0], 2)
            db.close()

    def test_fill_failure_rolls_back_account_and_order(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'paper.sqlite3'; fixture(path); db=connect(path); initialize(db)
            execute_action(db,request_id='b',symbol='AAPL',action='buy',quantity=4,mode='replay',scope='s',cursor=0)
            db.execute("CREATE TRIGGER fail_fill BEFORE INSERT ON paper_fills BEGIN SELECT RAISE(ABORT, 'test failure'); END")
            db.commit()
            with self.assertRaises(Exception): apply_receipt(db,1,'replay','s')
            state=account(path)
            self.assertEqual(state['cash'], '1000')
            self.assertEqual(state['positions'], [])
            self.assertEqual(state['orders'][0]['status'], 'pending')
            self.assertEqual(db.execute('select count(*) from paper_fills').fetchone()[0], 0)
            db.close()

    def test_fill_rechecks_and_mode_scope(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'paper.sqlite3'; fixture(path); db=connect(path); initialize(db)
            self.assertEqual(execute_action(db,request_id='expensive',symbol='AAPL',action='buy',quantity=11,mode='replay',scope='s',cursor=0)['status'],'pending')
            self.assertEqual(apply_receipt(db,1,'replay','other'),[])
            self.assertEqual(apply_receipt(db,1,'live'),[])
            self.assertEqual(apply_receipt(db,1,'replay','s')[0]['reason'],'Insufficient cash at fill')
            self.assertEqual(account(path)['cash'],'1000')
            with self.assertRaisesRegex(ValueError,'different action'):
                execute_action(db,request_id='expensive',symbol='AAPL',action='buy',quantity=1,mode='replay',scope='s',cursor=0)
            self.assertEqual(execute_action(db,request_id='invalid',symbol='AAPL',action='buy',quantity=1.5,mode='replay',scope='s',cursor=0)['status'],'rejected')
            db.close()


if __name__ == '__main__': unittest.main()
