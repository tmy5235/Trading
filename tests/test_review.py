"""Fixed replies for two-role paper decisions; all data is synthetic."""
import base64
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.decisions import decide, init
from backend.chart_decisions import decide_chart
from backend.observations import connect
from backend.paper import initialize, account
from tests.test_chart_decisions import png, answer


def analyst(action):
    return {'answers':[{'name':'paper_action','type':'choice','choice':action}]}


def review(verdict):
    return {'answers':[{'name':'verdict','type':'choice','choice':verdict}]}


class ReviewTests(unittest.TestCase):
    @patch('backend.review.strategy_is_configured', return_value=True)
    def test_three_cases_and_saved_roles(self, configured_strategy):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'review.sqlite3'; db=connect(path); initialize(db); init(db)
            before=account(path)
            scenarios=[('agreed','1000','buy','accept','valid',True),('rejected','1000','buy','reject_unsupported_signal','reviewer_rejected',False),('unaffordable','10','buy','accept','invalid_proposal',False)]
            for sid,cash,action,verdict,status,eligible in scenarios:
                snapshot={'id':sid,'source':'constructed fixture simulating an eligible application snapshot','test_data':False,
                          'eligible_for_paper_order':True,'symbol':'AAPL','timeframe':'1m',
                          'candle_timestamp':'2026-11-09T14:34:00Z','candle':{'close':100},
                          'sma':{'short':105,'long':100,'signal':'up'},'account':{'cash':cash,'shares':0,'other_open_positions':0},'order_size':1}
                db.execute('INSERT INTO decision_snapshots VALUES (?,?,?,?,?)',(sid,'fixture',json.dumps(snapshot),'eligible',None));db.commit()
                calls=[]
                def analyst_call(s,key): calls.append(('analyst',s)); return analyst(action)
                def reviewer_call(s,a,f,key,image): calls.append(('reviewer',s,a,f,image)); return review(verdict)
                result=decide(sid,path=path,provider=analyst_call,reviewer=reviewer_call,api_key='fixture')
                self.assertEqual((result['status'],result['eligible_for_paper_order']),(status,eligible))
                self.assertEqual(len(calls),2); self.assertEqual(calls[0][1],calls[1][1]); self.assertEqual(calls[1][2],{'paper_action':action})
                self.assertEqual(calls[1][3]['buy_cost_at_close'],'100'); self.assertIsNone(calls[1][4])
                row=db.execute('SELECT analyst_output_json,reviewer_input_json,reviewer_output_json,verdict,final_check_json FROM decision_reviews WHERE decision_id=?',(result['decision_id'],)).fetchone()
                self.assertEqual(json.loads(row[0]),analyst(action)); self.assertEqual(json.loads(row[2]),review(verdict)); self.assertEqual(row[3],verdict)
                self.assertEqual(json.loads(row[1])['snapshot'],snapshot); self.assertEqual(json.loads(row[4])['status'],status)
            self.assertEqual(before,account(path)); db.close()

    def test_synthetic_and_legacy_decisions_cannot_authorize_orders(self):
        from backend.main import get_decision
        with TemporaryDirectory() as folder:
            path=Path(folder)/'legacy.sqlite3'; db=connect(path); initialize(db); init(db)
            snapshot={'id':'test','test_data':True,'eligible_for_paper_order':True,'candle':{'close':100},'sma':{'short':105,'long':100,'signal':'up'},'account':{'cash':'1000','shares':0},'order_size':1}
            db.execute('INSERT INTO decision_snapshots VALUES (?,?,?,?,?)',('test','fixture',json.dumps(snapshot),'eligible',None)); db.commit()
            result=decide('test',path=path,provider=lambda *_:analyst('buy'),reviewer=lambda *_:review('accept'),api_key='fixture')
            self.assertFalse(result['eligible_for_paper_order'])
            snapshot['test_data']=False; db.execute('UPDATE decision_snapshots SET snapshot_json=? WHERE id=?',(json.dumps(snapshot),'test'))
            db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)',('legacy','test','fixture','OpenAI','model','buy','old','valid'));db.commit();db.close()
            with patch('backend.main.connect',side_effect=lambda:connect(path)):
                self.assertFalse(get_decision('legacy')['eligible_for_paper_order'])

    def test_reviewer_failure_and_image_same_evidence(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'chart.sqlite3'; db=connect(path); initialize(db); init(db)
            stamp='2026-11-09T14:34:00Z'
            snapshot={'id':'chart','source':'synthetic','test_data':True,'eligible_for_paper_order':False,'symbol':'AAPL','timeframe':'1m','candle_timestamp':stamp,'candle':{'close':19},'sma':{'short':16,'long':13,'signal':'up'},'account':{'cash':'1000','shares':0,'other_open_positions':0},'order_size':1,'chart_records':[{'candle':{'timestamp':stamp},'ended':True}]}
            db.execute('INSERT INTO decision_snapshots VALUES (?,?,?,?,?)',('chart','fixture',json.dumps(snapshot),'ineligible','synthetic'));db.commit()
            capture={'snapshot_id':'chart','symbol':'AAPL','timeframe':'1m','range_start':stamp,'cutoff':stamp}; image=png();url='data:image/png;base64,'+base64.b64encode(image).decode()
            calls=[]
            def a(s,img,key): calls.append((s,img)); return answer()
            def r(s,answers,f,key,img): calls.append((s,img,answers)); return review('accept')
            result=decide_chart('chart',url,capture,path=path,provider=a,reviewer=r,api_key='fixture')
            self.assertEqual(calls[0],calls[1][:2]);self.assertEqual(calls[1][2],result['answers'])
            self.assertEqual((result['review']['reviewer_status'],result['review']['verdict']),('valid','accept'))
            for fn,status in [(lambda *_: (_ for _ in ()).throw(TimeoutError()),'reviewer_timeout'),(lambda *_:{'answers':[]},'reviewer_malformed_answer')]:
                result=decide_chart('chart',url,capture,path=path,provider=a,reviewer=fn,api_key='fixture')
                self.assertEqual(result['status'],status);self.assertFalse(result['eligible_for_paper_order'])
            db.close()


if __name__=='__main__': unittest.main()
