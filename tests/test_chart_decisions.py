import base64
import hashlib
import io
import json
import struct
import unittest
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.chart_decisions import decode_png, decide_chart, provider_image_request
from backend.decisions import init, save_test_snapshot
from backend.observations import connect
from backend.paper import initialize, account


def png():
    def chunk(kind, data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',320,180,8,6,0,0,0))+chunk(b'IDAT',zlib.compress((b'\x00'+b'\xff\x00\x00\xff'*320)*180))+chunk(b'IEND',b'')


def answer(action='wait', crossover='up', evidence='sufficient'):
    return {'answers':[{'type':'choice','name':k,'choice':v} for k,v in [('action',action),('crossover',crossover),('evidence',evidence)]]}


class ChartDecisionTests(unittest.TestCase):
    def setUp(self):
        mocked = patch('backend.review.reviewer_request', return_value={'answers':[{'type':'choice','name':'verdict','choice':'accept'}]})
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_image_request_and_validation(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'test.sqlite3'; db=connect(path); initialize(db); init(db)
            stamp='2026-11-09T14:34:00Z'
            snapshot=save_test_snapshot(db, {'symbol':'AAPL','timeframe':'1m','candle_timestamp':stamp,'candle':{'close':19},'sma':{'short':16,'long':13,'signal':'up'},'account':{'cash':'1000','shares':0,'other_open_positions':0},'order_size':1,'chart_records':[{'candle':{'timestamp':stamp},'ended':True}, {'candle':{'timestamp':'2026-11-09T14:35:00Z','close':999},'ended':False}], 'replay':{'position':6,'event':{'price':999}}})
            capture={'snapshot_id':snapshot['id'],'symbol':'AAPL','timeframe':'1m','range_start':stamp,'cutoff':stamp}
            url='data:image/png;base64,'+base64.b64encode(png()).decode()
            received=[]; before=account(path)
            def provider(s, image, key):
                received.append((s,image)); return answer()
            result=decide_chart(snapshot['id'],url,capture,path=path,provider=provider,api_key='test')
            self.assertEqual((result['status'],result['comparison'],result['answers']['action']),('valid','matches','wait'))
            self.assertEqual(received[0][1],png()); self.assertEqual(len(received[0][0]['chart_records']),1); self.assertNotIn('event',received[0][0]['replay']); self.assertFalse(result['eligible_for_paper_order']); self.assertEqual(account(path),before)
            stored=db.execute('select png,sha256,questions_json,answers_json from chart_decisions where decision_id=?',(result['decision_id'],)).fetchone()
            self.assertEqual(stored[0],png()); self.assertEqual(stored[1],hashlib.sha256(png()).hexdigest()); self.assertEqual(json.loads(stored[3]),result['answers'])
            for value,status in [('down','conflicting_signal'),('unreadable','unreadable_image')]:
                r=decide_chart(snapshot['id'],url,capture,path=path,provider=lambda *_:answer(crossover=value),api_key='test')
                self.assertEqual(r['status'],status); self.assertFalse(r['eligible_for_paper_order'])
            r=decide_chart(snapshot['id'],url,capture,path=path,provider=lambda *_:answer(evidence='insufficient'),api_key='test')
            self.assertEqual(r['status'],'insufficient_evidence')
            r=decide_chart(snapshot['id'],url,capture,path=path,provider=lambda *_:{'answers':[]},api_key='test')
            self.assertEqual(r['status'],'malformed_answer'); self.assertFalse(r['eligible_for_paper_order'])
            with self.assertRaisesRegex(ValueError,'recapture'):
                decide_chart(snapshot['id'],url,{**capture,'symbol':'MSFT'},path=path,api_key='test')
            with self.assertRaisesRegex(ValueError,'recapture'):
                decide_chart(snapshot['id'],'data:image/png;base64,YWJj',capture,path=path,api_key='test')
            db.close()

    def test_provider_payload_contains_real_image_and_named_questions(self):
        captured={}
        def fake(request,**kwargs):
            captured.update(json.loads(request.data)); return io.BytesIO(json.dumps(answer()).encode())
        with patch('backend.chart_decisions.urllib.request.urlopen',fake):
            provider_image_request({'id':'fixture'},png(),'fixture-only')
        parts=captured['input'][0]['content']
        self.assertEqual(parts[0],{'type':'input_json','data':{'id':'fixture'}})
        self.assertEqual(decode_png(parts[1]['image_url']),png())
        self.assertEqual([q['name'] for q in captured['questions']],['action','crossover','evidence'])


if __name__ == '__main__': unittest.main()
