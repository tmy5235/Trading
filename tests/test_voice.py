import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from backend.observations import connect
from backend.paper import initialize
from backend.automation import init_automation
from backend.voice import decision_summary, list_decisions, create_session


class VoiceTests(unittest.TestCase):
    def test_summary_and_provider_exchange(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'voice.sqlite3'; db=connect(path); initialize(db); init_automation(db)
            snap={'symbol':'AAPL','timeframe':'1m','source':'Synthetic fixture','test_data':True,'candle_timestamp':'2026-11-09T14:34:00Z','candle':{'close':100},'sma':{'short':105,'long':100,'signal':'up'},'account':{'cash':'1000','shares':0}}
            db.execute('INSERT INTO decision_snapshots VALUES (?,?,?,?,?)',('s','time',json.dumps(snap),'ineligible','synthetic'))
            db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)',('d','s','time','mock','mock-model','buy','Synthetic facts','valid'))
            db.execute('INSERT INTO decision_reviews VALUES (?,?,?,?,?,?,?,?,?)',('d','mock','{"answers":[]}','mock','{}','{"answers":[]}','valid','accept','{"status":"valid"}'))
            db.commit()
            before=db.execute('SELECT count(*) FROM paper_orders').fetchone()[0]
            summary=decision_summary('d',path)
            self.assertEqual((summary['symbol'],summary['analyst']['proposal'],summary['reviewer']['verdict']),('AAPL','buy','accept'))
            self.assertEqual(summary['paper_outcome']['status'],'no linked paper outcome')
            self.assertEqual(list_decisions(path)[0]['id'],'d')
            captured={}
            def opener(request,**kwargs):
                captured.update(json.loads(request.data));captured['timeout']=kwargs['timeout']
                return io.BytesIO(b'{"session":{"id":"test-session"},"transport":{"type":"webrtc","sdp":"test-answer"}}')
            result=create_session('d','test-offer',path,api_key='fake-test-key',opener=opener)
            self.assertEqual(result['sdp'],'test-answer');self.assertEqual(captured['session']['model'],'gpt-live-1')
            self.assertEqual(captured['transport'],{'type':'webrtc','sdp':'test-offer'})
            self.assertIn('Synthetic fixture',captured['session']['instructions']);self.assertIn('No paper outcome',captured['session']['instructions'])
            self.assertEqual(captured['timeout'],20);self.assertEqual(db.execute('SELECT count(*) FROM paper_orders').fetchone()[0],before)
            with self.assertRaisesRegex(ValueError,'not found'):create_session('missing','test-offer',path,api_key='fake',opener=opener)
            with self.assertRaisesRegex(RuntimeError,'invalid session'):create_session('d','test-offer',path,api_key='fake',opener=lambda *a,**k:io.BytesIO(b'{}'))
            db.close()

    def test_linked_paper_outcome(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'test.sqlite3'; db=connect(path); initialize(db); init_automation(db)
            db.execute('INSERT INTO decision_snapshots VALUES (?,?,?,?,?)',('s','time',json.dumps({'symbol':'AAPL'}),'ineligible','test'))
            db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)',('d','s','time','mock','mock','wait','facts','valid'))
            db.execute("INSERT INTO automation_events(id,account_id,strategy_id,symbol,timeframe,candle_timestamp,session_id,status,reason,decision_id,created_at,updated_at) VALUES ('e',1,'test','AAPL','1m','time','session','skipped','review veto','d','time','time')")
            db.commit(); db.close()
            outcome=decision_summary('d',path)['paper_outcome']
            self.assertEqual((outcome['status'],outcome['reason'],outcome['fill_id']),('skipped','review veto',None))


if __name__=='__main__':unittest.main()
