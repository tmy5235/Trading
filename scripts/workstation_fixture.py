"""Offline workstation smoke fixture. Only run with its disposable database."""
import base64
import hashlib
import json
import os
import urllib.error
from datetime import datetime as RealDateTime, timedelta, timezone
from pathlib import Path

os.environ['OPENAI_API_KEY']='offline-test-double'
os.environ['TRADING_BOT_DATA_IS_TEST']='1'
os.environ['TRADING_BOT_AUTOMATION_TEST_MODE']='1'
os.environ['TRADING_BOT_DATA_LABEL']='Synthetic workstation test'
os.environ['PAPER_ORDER_SIZE']='4'
path=Path(os.environ['TRADING_BOT_DB_PATH']).resolve()
if not path.is_relative_to(Path('/private/tmp').resolve()) and not path.is_relative_to(Path('/tmp').resolve()):
    raise RuntimeError('Workstation fixture requires a temporary database')

import backend.automation as automation
import backend.chart_decisions as charts
import backend.decisions as decisions
import backend.live_history as live
import backend.paper as paper
import backend.review as review
from backend.observations import connect, record
from backend.main import app

clock={'now':RealDateTime(2026,11,9,14,35,10,tzinfo=timezone.utc),'stage':0,'failure':False}
class TestDateTime(RealDateTime):
    @classmethod
    def now(cls,tz=None):
        return clock['now'].astimezone(tz) if tz else clock['now'].replace(tzinfo=None)

automation.datetime=TestDateTime; decisions.datetime=TestDateTime; live.datetime=TestDateTime
fake_now=lambda:clock['now'].isoformat()
automation.utc_now=fake_now; paper.utc_now=fake_now

# This strategy override is restricted to this disposable test process. It
# exercises the requested partial sale without changing the real strategy.
def test_strategy(snapshot):
    return {1:'buy',2:'sell',3:'close',4:'hold',5:'hold'}.get(clock['stage'],'wait')
automation.expected_action=test_strategy; decisions.expected_action=test_strategy

def add_tick(price, at):
    db=connect(path)
    try:
        record(db,{'id':'AAPL','time':str(int(at.timestamp()*1000)),'price':price,'day_volume':str(1000+db.execute('SELECT count(*) FROM receipts').fetchone()[0])},at.isoformat())
        receipt=db.execute('SELECT MAX(id) FROM receipts').fetchone()[0]
        return receipt
    finally: db.close()

def initialize_fixture():
    db=connect(path)
    try:
        automation.init_automation(db)
        db.execute("UPDATE paper_account_metadata SET created_at='2026-11-09T14:00:00+00:00'")
        db.execute('CREATE TABLE disposable_test_account (id INTEGER PRIMARY KEY,label TEXT NOT NULL)')
        db.execute("INSERT INTO disposable_test_account VALUES (1,'synthetic disposable account')")
        db.commit()
        base=RealDateTime(2026,11,9,14,30,tzinfo=timezone.utc)
        for n,price in enumerate((13,10,7,13,19,7)):
            stamp=base+timedelta(minutes=n,seconds=1)
            record(db,{'id':'AAPL','time':str(int(stamp.timestamp()*1000)),'price':price,'day_volume':str(1000+n)},(stamp+timedelta(seconds=1)).isoformat())
    finally: db.close()
initialize_fixture()

def analyst(snapshot,image,key):
    assert key=='offline-test-double' and snapshot['test_data'] and snapshot['symbol']=='AAPL'
    assert snapshot['chart_records'][-1]['candle']['timestamp']==snapshot['candle_timestamp']
    assert charts.decode_png('data:image/png;base64,'+base64.b64encode(image).decode())==image
    with connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS workstation_calls (role TEXT, snapshot_id TEXT, digest TEXT)')
        db.execute('INSERT INTO workstation_calls VALUES (?,?,?)',('analyst',snapshot['id'],hashlib.sha256(image).hexdigest()))
    if clock['failure']:
        clock['failure']=False; raise urllib.error.URLError('controlled offline failure')
    return {'answers':[{'type':'choice','name':n,'choice':v} for n,v in [('action',test_strategy(snapshot)),('crossover',snapshot['sma']['signal'] or 'none'),('evidence','sufficient')]]}

def reviewer(snapshot,answers,facts,key,image):
    with connect(path) as db:
        db.execute('INSERT INTO workstation_calls VALUES (?,?,?)',('reviewer',snapshot['id'],hashlib.sha256(image).hexdigest()))
    verdict='reject_other' if clock['stage']==4 else 'accept'
    return {'answers':[{'type':'choice','name':'verdict','choice':verdict}]}
charts.provider_image_request=analyst; review.reviewer_request=reviewer

@app.post('/__test/provider-failure')
def provider_failure():
    clock['failure']=True; return {'ok':True}

@app.post('/__test/advance')
def advance():
    clock['stage']+=1
    clock['now']=RealDateTime(2026,11,9,14,35,tzinfo=timezone.utc)+timedelta(minutes=clock['stage'],seconds=1)
    os.environ['PAPER_ORDER_SIZE']='4' if clock['stage']==1 else '1'
    receipt=add_tick(90+clock['stage'],clock['now'])
    return {'stage':clock['stage'],'receipt_id':receipt}

@app.post('/__test/fill/{price}')
def fill(price:int):
    clock['now']+=timedelta(seconds=1)
    receipt=add_tick(price,clock['now']);db=connect(path)
    try: return {'results':paper.apply_receipt(db,receipt,'live')}
    finally: db.close()

@app.post('/__test/duplicate')
def duplicate():
    db=connect(path)
    try:
        row=db.execute('SELECT e.id,e.decision_id FROM automation_events e WHERE e.decision_id IS NOT NULL ORDER BY e.rowid DESC LIMIT 1').fetchone()
    finally: db.close()
    return automation.apply(row[0],row[1],path)

@app.get('/__test/state')
def test_state():
    db=connect(path)
    try:
        return {'fills':db.execute('SELECT count(*) FROM paper_fills').fetchone()[0],
                'cash':db.execute('SELECT cash FROM paper_account WHERE id=1').fetchone()[0],
                'shares':dict(db.execute('SELECT symbol,shares FROM paper_positions')),
                'pnl':db.execute('SELECT realized,unrealized,total FROM paper_pnl_history ORDER BY id DESC LIMIT 1').fetchone(),
                'events':db.execute('SELECT candle_timestamp,status,reason FROM automation_events ORDER BY rowid').fetchall(),
                'calls':db.execute("SELECT role,snapshot_id,digest FROM workstation_calls ORDER BY rowid").fetchall() if db.execute("SELECT 1 FROM sqlite_master WHERE name='workstation_calls'").fetchone() else []}
    finally: db.close()
