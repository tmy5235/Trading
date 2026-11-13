import json
import io
import urllib.error
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from backend.decisions import init,save_test_snapshot,decide,create_snapshot,provider_request
from backend.observations import connect,record
from backend.paper import initialize,account,execute_action,apply_receipt
from backend.replay import replay
from backend.indicators import add_sma, add_instances


def choice(value):
    return {'answers':[{'name':'paper_action','type':'choice','choice':value}]}


class DecisionTests(unittest.TestCase):
    def setUp(self):
        mocked = patch('backend.review.reviewer_request', return_value={'answers':[{'type':'choice','name':'verdict','choice':'accept'}]})
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_controlled_decision_failures_and_account_unchanged(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'test.sqlite3'; db=connect(path); initialize(db); init(db)
            snap=save_test_snapshot(db, {'symbol':'AAPL','timeframe':'1m','candle_timestamp':'2026-11-09T14:32:00Z','candle':{'close':100},'sma':{'short':8.5,'long':10,'signal':None},'account':{'cash':'600','shares':4,'other_open_positions':0},'order_size':1})
            before=account(path)
            valid=decide(snap['id'],path=path,provider=lambda *_:choice('hold'),api_key='test-key')
            self.assertEqual(valid['status'],'valid'); self.assertEqual(valid['proposal'],'hold'); self.assertFalse(valid['eligible_for_paper_order'])
            fail=decide(snap['id'],path=path,provider=lambda *_: (_ for _ in ()).throw(TimeoutError()),api_key='test-key')
            self.assertEqual(fail['status'],'timeout')
            malformed=decide(snap['id'],path=path,provider=lambda *_:{'answers':[{'name':'wrong','type':'choice','choice':'buy'}]},api_key='test-key')
            self.assertEqual(malformed['status'],'malformed_answer')
            invalid=decide(snap['id'],path=path,provider=lambda *_:choice('wait'),api_key='test-key')
            self.assertEqual(invalid['status'],'invalid_proposal')
            failed=decide(snap['id'],path=path,provider=lambda *_: (_ for _ in ()).throw(ConnectionError()),api_key='test-key')
            self.assertEqual(failed['status'],'provider_error')
            unauthorized=decide(snap['id'],path=path,provider=lambda *_: (_ for _ in ()).throw(urllib.error.HTTPError('https://api.openai.com/v1/decisions', 401, 'unauthorized', {}, None)),api_key='test-key')
            self.assertEqual((unauthorized['status'],unauthorized['explanation']),('provider_error','Provider returned HTTP 401.'))
            missing=decide('missing',path=path,api_key='test-key')
            self.assertEqual(missing['status'],'missing_input')
            self.assertEqual(db.execute('SELECT count(*) FROM decisions').fetchone()[0],7)
            self.assertEqual(account(path),before)
            self.assertEqual(db.execute('SELECT status,proposal FROM decisions WHERE id=?',(valid['decision_id'],)).fetchone(),('valid','hold'))
            db.close()

    def test_provider_request_uses_named_choice_protocol(self):
        captured = {}
        def fake_urlopen(request, **kwargs):
            captured['url'] = request.full_url
            captured['body'] = json.loads(request.data)
            return io.BytesIO(b'{"answers":[{"name":"paper_action","type":"choice","choice":"hold"}]}')
        with patch('backend.decisions.urllib.request.urlopen', fake_urlopen):
            response = provider_request({'test_data':True}, 'fixture-only')
        question = captured['body']['questions'][0]
        self.assertEqual(captured['url'], 'https://api.openai.com/v1/decisions')
        self.assertEqual(captured['body']['model'], 'gpt-6-luna')
        self.assertEqual((question['name'],question['type']),('paper_action','choice'))
        self.assertEqual([item['value'] for item in question['choices']], ['buy','sell','hold','wait','close'])
        self.assertEqual(response['answers'][0]['choice'],'hold')

    def test_snapshot_freezes_at_close_without_future_data(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'test.sqlite3'; db=connect(path); initialize(db)
            db.execute("UPDATE paper_account_metadata SET created_at='2026-11-09T14:00:00+00:00'");db.commit()
            base=datetime(2026,11,9,14,30,tzinfo=timezone.utc)
            for i,price in enumerate((13,10,7)):
                t=base+timedelta(minutes=i,seconds=2)
                record(db,{'id':'AAPL','time':str(int(t.timestamp()*1000)),'price':price},(t+timedelta(seconds=1)).isoformat())
            t=base+timedelta(minutes=2,seconds=45)
            record(db,{'id':'AAPL','time':str(int(t.timestamp()*1000)),'price':999},'2026-11-09T14:33:01+00:00')
            db.close()
            with patch.dict('os.environ', {'TRADING_BOT_DATA_IS_TEST':'1', 'TRADING_BOT_DATA_LABEL':'Synthetic fixture'}):
                snap=create_snapshot('AAPL','1m','2026-11-09T14:32:00Z',2,3,path=path)
            self.assertFalse(snap['eligible_for_paper_order']); self.assertTrue(snap['test_data'])
            self.assertEqual(snap['candle']['close'],7)
            self.assertEqual((snap['sma']['short'],snap['sma']['long']),(8.5,10))
            self.assertEqual(snap['account']['cash'],'1000')
            with self.assertRaisesRegex(ValueError, 'Replay requires'):
                create_snapshot('AAPL','1m','2026-11-09T14:32:00Z',2,3,mode='replay',path=path)
            db=connect(path); frozen=json.loads(db.execute('select snapshot_json from decision_snapshots where id=?',(snap['id'],)).fetchone()[0]); self.assertEqual(frozen,snap); db.close()

    def test_replay_snapshot_freezes_selected_point_and_account(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'replay.sqlite3'; db=connect(path); initialize(db)
            base=datetime(2026,11,9,14,30,tzinfo=timezone.utc)
            for i,price in enumerate((13,10,7,20)):
                t=base+timedelta(minutes=i,seconds=1)
                record(db,{'id':'AAPL','time':str(int(t.timestamp()*1000)),'price':price},t.isoformat())
            late=base+timedelta(minutes=2,seconds=45)
            record(db,{'id':'AAPL','time':str(int(late.timestamp()*1000)),'price':19},(base+timedelta(minutes=3,seconds=2)).isoformat())
            start='2026-11-09T14:30:00Z'; end='2026-11-09T14:35:00Z'
            scope=json.dumps(['AAPL',start,end],separators=(',',':'))
            execute_action(db,request_id='buy',symbol='AAPL',action='buy',quantity=4,mode='replay',scope=scope,cursor=0)
            apply_receipt(db,1,'replay',scope,capture_checkpoint=True)
            checkpoint=apply_receipt(db,4,'replay',scope,capture_checkpoint=True)['checkpoint_id']
            execute_action(db,request_id='sell',symbol='AAPL',action='sell',quantity=1,mode='replay',scope=scope,cursor=4)
            later=apply_receipt(db,5,'replay',scope,capture_checkpoint=True)['checkpoint_id']
            db.close()
            args=dict(mode='replay',path=path,replay_start=start,replay_end=end,replay_cutoff=5)
            with patch.dict('os.environ', {'TRADING_BOT_DATA_IS_TEST':'1','TRADING_BOT_DATA_LABEL':'Synthetic fixture'}):
                first=create_snapshot('AAPL','1m','2026-11-09T14:32:00Z',2,3,replay_position=4,replay_checkpoint_id=checkpoint,**args)
                second=create_snapshot('AAPL','1m','2026-11-09T14:32:00Z',2,3,replay_position=5,replay_checkpoint_id=later,**args)
            self.assertEqual(first['chart_records'],add_instances(add_sma(replay('AAPL',start,end,'1m',4,5,path=path)['records'],2,3), []))
            self.assertEqual((first['candle']['close'],first['account']['cash'],first['account']['shares']),(7,'948',4))
            self.assertEqual((second['candle']['close'],second['account']['cash'],second['account']['shares']),(19,'967',3))
            self.assertFalse(first['eligible_for_paper_order'])
            supplied=[]
            def controlled_provider(snapshot, key):
                supplied.append(snapshot)
                return choice('hold')
            before=account(path)
            decision=decide(first['id'],path=path,provider=controlled_provider,api_key='test-key')
            self.assertEqual((decision['status'],decision['proposal']),('valid','hold'))
            self.assertEqual(supplied,[first])
            self.assertEqual(account(path),before)
            with self.assertRaisesRegex(ValueError,'does not match'):
                create_snapshot('AAPL','1m','2026-11-09T14:32:00Z',2,3,replay_position=5,replay_checkpoint_id=checkpoint,**args)
            with self.assertRaisesRegex(ValueError,'has not closed'):
                create_snapshot('AAPL','1m','2026-11-09T14:33:00Z',2,3,replay_position=4,replay_checkpoint_id=checkpoint,**args)
            db=connect(path)
            saved=json.loads(db.execute('select snapshot_json from decision_snapshots where id=?',(first['id'],)).fetchone()[0])
            self.assertEqual(saved,first)
            db.close()


if __name__=='__main__': unittest.main()
