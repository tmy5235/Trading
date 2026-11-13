"""Run an isolated synthetic candle-completion and model-double smoke server."""
import hashlib
import os
import sqlite3
from datetime import datetime, timezone
import uvicorn
import backend.automation as automation
import backend.chart_decisions as charts
import backend.decisions as decisions
import backend.live_history as live
import backend.review as review
from backend.indicators import add_sma, add_instances
from backend.observations import connect
from backend.replay import replay

os.environ['OPENAI_API_KEY']='synthetic-provider-double'
path=os.environ['TRADING_BOT_DB_PATH']
start='2026-11-09T14:30:00Z'; end='2026-11-09T14:40:00Z'


def records(position):
    return replay('AAPL',start,end,'1m',position,path=path)['records']


def synthetic_feed(symbol,timeframe,now=None):
    with sqlite3.connect(path) as db:
        enabled=db.execute('SELECT enabled FROM automation_state WHERE account_id=1').fetchone()[0]
    return {'symbol':symbol,'timeframe':timeframe,'source':'Synthetic automation fixture',
            'feed_status':'connected','source_time':None,'observation_id':None,
            'records':records(6 if enabled else 5)}


def synthetic_snapshot(symbol,timeframe,timestamp,short=2,long=3,mode='live',**kwargs):
    entries=add_instances(add_sma(records(6),short,long),[])
    selected=next(r for r in entries if r['candle']['timestamp']==timestamp)
    data={'source':'synthetic automation fixture','symbol':symbol,'timeframe':timeframe,
          'mode':'live','candle_timestamp':timestamp,'decision_time':'2026-11-09T14:35:02+00:00',
          'candle':selected['candle'],'sma':selected['sma'],'short_window':short,'long_window':long,
          'account':{'cash':'1000','shares':0,'other_open_positions':0},'order_size':1,
          'chart_records':entries,'indicator_instances':[],'ineligibility_reasons':['synthetic test data']}
    db=connect(path)
    try:
        decisions.init(db)
        return decisions.save_test_snapshot(db,data)
    finally: db.close()


def analyst(snapshot,image,key):
    assert snapshot['test_data'] and charts.decode_png('data:image/png;base64,'+__import__('base64').b64encode(image).decode())==image
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS synthetic_calls (role TEXT, snapshot_id TEXT, image_hash TEXT)')
        db.execute('INSERT INTO synthetic_calls VALUES (?,?,?)',('analyst',snapshot['id'],hashlib.sha256(image).hexdigest()))
    return {'answers':[{'type':'choice','name':n,'choice':v} for n,v in [('action','wait'),('crossover','up'),('evidence','sufficient')]]}


def reviewer(snapshot,answers,facts,key,image):
    assert snapshot['test_data'] and answers['action']=='wait'
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO synthetic_calls VALUES (?,?,?)',('reviewer',snapshot['id'],hashlib.sha256(image).hexdigest()))
    return {'answers':[{'type':'choice','name':'verdict','choice':'accept'}]}


live.snapshot=synthetic_feed
decisions.create_snapshot=synthetic_snapshot
charts.provider_image_request=analyst
review.reviewer_request=reviewer
# The fixture uses fixed historical candles; this clock adapter is confined to
# the synthetic process and makes its 14:34 candle complete after Start.
automation.candle_end=lambda start,timeframe:(datetime.now(timezone.utc),())
uvicorn.run('backend.main:app',host='127.0.0.1',port=8000,log_level='warning')
