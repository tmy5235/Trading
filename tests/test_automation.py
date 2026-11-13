"""All account and provider inputs here are constructed disposable test data."""
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from backend import automation
from backend.strategy import STRATEGY_ID
from backend.observations import connect, record
from backend.paper import account, apply_receipt


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'test.sqlite3'
        self.state=automation.start(self.path)
        self.db=connect(self.path)
        # Simulate a configured strategy and a clock immediately after a session candle.
        self.db.execute("UPDATE automation_state SET started_at='2026-11-09T14:30:00+00:00',strategy_id='sma-crossover-flat-v1',max_age_seconds=60")
        self.db.commit()
        self.allowed=patch('backend.automation.execution_eligible',return_value=True);self.allowed.start();self.addCleanup(self.allowed.stop)
        self.addCleanup(lambda:self.db.close())

    def build(self, timestamp, decision_id, verdict='accept', status='valid', action='buy'):
        sid='s-'+decision_id
        snapshot={'id':sid,'symbol':'AAPL','timeframe':'1m','candle_timestamp':timestamp,'mode':'live','source':'synthetic test fixture','test_data':True,'order_size':4,'strategy_id':STRATEGY_ID,'decision_time':datetime.now(timezone.utc).isoformat(),'account':{'shares':0,'cash':'1000','other_open_positions':0},'candle':{'timestamp':timestamp,'close':100},'sma':{'signal':'up','short':105,'long':100},'chart_records':[{'candle':{'timestamp':timestamp},'ended':True,'coverage':{'complete':True}}]}
        self.db.execute('INSERT INTO decision_snapshots VALUES (?,?,?,?,?)',(sid,datetime.now(timezone.utc).isoformat(),json.dumps(snapshot),'ineligible','synthetic fixture'))
        self.db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)',(decision_id,sid,datetime.now(timezone.utc).isoformat(),'mock','mock',action,'constructed facts',status))
        self.db.execute('INSERT INTO decision_reviews VALUES (?,?,?,?,?,?,?,?,?)',(decision_id,'mock','{}','mock','{}','{}','valid',verdict,'{}'))
        self.db.execute('INSERT INTO chart_decisions(decision_id,png,sha256,capture_json,questions_json,answers_json,comparison) VALUES (?,?,?,?,?,?,?)',(decision_id,b'fixture','fixture','{}','[]','{}','matches'))
        self.db.commit()
        claim=automation.claim(self.state['session_id'],timestamp,self.path)
        automation.bind(claim['id'],sid,self.path)
        return claim['id']

    def tick(self,price):
        now=datetime.now(timezone.utc)
        record(self.db,{'id':'AAPL','time':str(int(now.timestamp()*1000)),'price':price},now.isoformat())
        return self.db.execute('SELECT MAX(id) FROM receipts').fetchone()[0]

    def test_approved_twice_then_veto_and_restart(self):
        before=account(self.path)
        event=self.build('2026-11-09T14:34:00Z','approved')
        first=automation.apply(event,'approved',self.path)
        self.assertEqual(first['status'],'pending')
        again=automation.apply(event,'approved',self.path);self.assertTrue(again['duplicate'])
        rid=self.tick(100);self.assertEqual(apply_receipt(self.db,rid,'live')[0]['status'],'filled')
        state=account(self.path)
        self.assertEqual((state['cash'],state['positions'][0]['shares']),('600',4))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM paper_fills').fetchone()[0],1)
        linked=self.db.execute('SELECT status,decision_id,order_id,fill_id FROM automation_events WHERE id=?',(event,)).fetchone()
        self.assertEqual(linked[0:2],('filled','approved'));self.assertIsNotNone(linked[3])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM paper_pnl_history WHERE fill_id=?',(linked[3],)).fetchone()[0],1)
        self.assertEqual(automation.claim(self.state['session_id'],'2026-11-09T14:34:00Z',self.path)['id'],event)
        veto=self.build('2026-11-09T14:35:00Z','veto','reject_other','reviewer_rejected')
        self.assertEqual(automation.apply(veto,'veto',self.path)['status'],'skipped')
        self.assertEqual(account(self.path)['cash'],'600');self.assertEqual(self.db.execute('SELECT COUNT(*) FROM paper_fills').fetchone()[0],1)
        self.db.close();self.db=connect(self.path)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM automation_events WHERE decision_id=\'approved\'').fetchone()[0],1)
        automation.stop(self.path); self.state=automation.start(self.path)
        self.db.execute("UPDATE automation_state SET started_at='2026-11-09T14:30:00+00:00',strategy_id='sma-crossover-flat-v1',max_age_seconds=60"); self.db.commit()
        self.assertTrue(automation.claim(self.state['session_id'],'2026-11-09T14:34:00Z',self.path)['duplicate'])
        self.assertEqual(account(self.path)['positions'][0]['shares'],4)

    def test_test_data_and_missing_strategy_fail_closed(self):
        self.allowed.stop()
        event=self.build('2026-11-09T14:34:00Z','synthetic')
        result=automation.apply(event,'synthetic',self.path)
        self.assertEqual(result['status'],'skipped')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM paper_orders').fetchone()[0],0)
        self.assertEqual(account(self.path)['cash'],'1000')

    def test_disposable_synthetic_account_routes_without_mocked_eligibility(self):
        self.allowed.stop()
        self.db.execute('CREATE TABLE disposable_test_account(id INTEGER PRIMARY KEY, label TEXT NOT NULL)')
        self.db.execute("INSERT INTO disposable_test_account VALUES (1,'synthetic disposable account')")
        self.db.commit()
        with patch.dict('os.environ', {'TRADING_BOT_AUTOMATION_TEST_MODE':'1'}):
            event=self.build('2026-11-09T14:34:00Z','synthetic-approved')
            self.assertEqual(automation.apply(event,'synthetic-approved',self.path)['status'],'pending')
            self.assertTrue(automation.apply(event,'synthetic-approved',self.path)['duplicate'])
            self.assertEqual(apply_receipt(self.db,self.tick(100),'live')[0]['status'],'filled')
            self.assertEqual((account(self.path)['cash'],account(self.path)['positions'][0]['shares']),('600',4))
            veto=self.build('2026-11-09T14:35:00Z','synthetic-veto','reject_other','reviewer_rejected')
            self.assertEqual(automation.apply(veto,'synthetic-veto',self.path)['status'],'skipped')
            self.assertEqual(self.db.execute('SELECT count(*) FROM paper_fills').fetchone()[0],1)

    def test_stop_pending_inflight_and_expiry(self):
        event=self.build('2026-11-09T14:34:00Z','late')
        automation.stop(self.path)
        self.assertEqual(automation.apply(event,'late',self.path)['status'],'skipped')
        self.assertEqual(self.db.execute('SELECT decision_id FROM automation_events WHERE id=?',(event,)).fetchone()[0],'late')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM paper_orders').fetchone()[0],0)
        self.state=automation.start(self.path)
        self.db.execute("UPDATE automation_state SET started_at='2026-11-09T14:30:00+00:00',strategy_id='sma-crossover-flat-v1',max_age_seconds=60");self.db.commit()
        pending=self.build('2026-11-09T14:35:00Z','pending'); self.assertEqual(automation.apply(pending,'pending',self.path)['status'],'pending')
        automation.stop(self.path)
        self.assertEqual(apply_receipt(self.db,self.tick(100),'live'),[])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM paper_fills').fetchone()[0],0)
        self.state=automation.start(self.path)
        self.db.execute("UPDATE automation_state SET started_at='2026-11-09T14:30:00+00:00',strategy_id='sma-crossover-flat-v1',max_age_seconds=60");self.db.commit()
        expired=self.build('2026-11-09T14:36:00Z','expired'); automation.apply(expired,'expired',self.path)
        stale=json.loads(self.db.execute("SELECT snapshot_json FROM decision_snapshots WHERE id='s-expired'").fetchone()[0]); stale['decision_time']='2020-01-01T00:00:00+00:00'
        self.db.execute("UPDATE decision_snapshots SET snapshot_json=? WHERE id='s-expired'",(json.dumps(stale),));self.db.commit()
        self.assertEqual(apply_receipt(self.db,self.tick(100),'live')[0]['reason'],'Decision has expired')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM paper_fills').fetchone()[0],0)


if __name__=='__main__': unittest.main()
