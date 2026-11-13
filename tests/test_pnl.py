import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from backend.observations import connect, record
from backend.paper import initialize, execute_action, apply_receipt, account
from backend.pnl import mark_due_live


def fixture(path):
    db = connect(path); initialize(db)
    base = datetime(2026,11,9,14,30,tzinfo=timezone.utc)
    for offset, price in [(0,100),(30,110),(60,110),(70,110),(90,95),(120,95),(130,105)]:
        t = base + timedelta(seconds=offset)
        record(db, {'id':'AAPL','time':str(int(t.timestamp()*1000)),'price':price,'day_volume':'1000'}, (t+timedelta(seconds=1)).isoformat())
    db.close()


def run_sequence(path):
    db=connect(path); initialize(db)
    def action(req,side,qty,cursor):
        return execute_action(db,request_id=req,symbol='AAPL',action=side,quantity=qty,mode='replay',scope='sample',cursor=cursor)
    action('b','buy',4,0); apply_receipt(db,1,'replay','sample')
    apply_receipt(db,2,'replay','sample'); apply_receipt(db,3,'replay','sample'); apply_receipt(db,3,'replay','sample'); apply_receipt(db,3,'replay','different')
    action('s','sell',1,3); apply_receipt(db,4,'replay','sample')
    apply_receipt(db,5,'replay','sample'); apply_receipt(db,6,'replay','sample'); apply_receipt(db,6,'replay','sample')
    action('c','close',None,6); apply_receipt(db,7,'replay','sample')
    apply_receipt(db,7,'replay','sample')
    db.close()


class PnlTests(unittest.TestCase):
    def test_reference_and_idempotent_restart(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'pnl.sqlite3'; fixture(path); run_sequence(path)
            rows=account(path)['pnl_history']
            got=[(r['event_type'], Decimal(r['cash']),r['shares'],Decimal(r['realized']),Decimal(r['unrealized']),Decimal(r['total'])) for r in rows]
            self.assertEqual(got,[('fill',600,4,0,0,0),('candle',600,4,0,40,40),('fill',710,3,10,30,40),('candle',710,3,10,-15,-5),('fill',1025,0,25,0,25)])
            self.assertEqual(len(account(path)['pnl_history']),5)

    def test_live_completion_without_new_tick(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'pnl.sqlite3'; fixture(path); db=connect(path); initialize(db)
            execute_action(db,request_id='b',symbol='AAPL',action='buy',quantity=4,mode='replay',scope='sample',cursor=0)
            apply_receipt(db,1,'replay','sample')
            # Use a database containing only the first two receipts to mimic a live gap.
            db.execute('DELETE FROM receipts WHERE id>2'); db.execute('DELETE FROM observations WHERE id>2'); db.commit()
            mark_due_live(db,'AAPL',datetime(2026,11,9,14,31,1,tzinfo=timezone.utc))
            mark_due_live(db,'AAPL',datetime(2026,11,9,14,31,2,tzinfo=timezone.utc))
            rows=account(path)['pnl_history']
            self.assertEqual(len(rows),2)
            self.assertEqual((rows[-1]['event_type'],Decimal(rows[-1]['unrealized'])),('candle',40))
            db.close()

    def test_legacy_basis_without_prior_valuation_is_unavailable(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'pnl.sqlite3'; fixture(path); db=connect(path); initialize(db)
            execute_action(db,request_id='b',symbol='AAPL',action='buy',quantity=4,mode='replay',scope='sample',cursor=0)
            apply_receipt(db,1,'replay','sample')
            db.executescript('DROP TABLE paper_pnl_history; DROP TABLE paper_candle_marks; DROP TABLE paper_pnl_state;')
            initialize(db)
            self.assertEqual(db.execute('select cost_basis,realized from paper_pnl_state where symbol="AAPL"').fetchone(), ('400','0'))
            self.assertIsNone(account(path)['positions'][0]['valuation'])
            db.close()

    def test_weighted_cost(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'pnl.sqlite3'; db=connect(path); initialize(db)
            prices=[100,120,130]
            for index,price in enumerate(prices,1):
                t=datetime(2026,11,9,14,30,index,tzinfo=timezone.utc)
                record(db,{'id':'AAPL','time':str(int(t.timestamp()*1000)),'price':price},t.isoformat())
                side='buy' if index<3 else 'sell'
                qty=2 if index<3 else 1
                execute_action(db,request_id=str(index),symbol='AAPL',action=side,quantity=qty,mode='replay',scope='s',cursor=index-1)
                apply_receipt(db,index,'replay','s')
            row=account(path)['pnl_history'][-1]
            self.assertEqual((Decimal(row['average_cost']),Decimal(row['realized']),Decimal(row['unrealized'])),(110,20,60))
            db.close()


if __name__=='__main__': unittest.main()
