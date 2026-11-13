"""Durable, fail-closed orchestration for reviewed paper decisions."""
import json
import os
import tempfile
import uuid
from pathlib import Path
from contextlib import closing
from datetime import datetime, timezone
from backend.observations import DEFAULT_DB, connect
from backend.paper import initialize as init_paper, execute_action, shares_for, utc_now
from backend.decisions import init as init_decisions, candle_end, snapshot_has_inputs
from backend.timeframes import DURATIONS, parse_utc
from backend.review import execution_eligible
from backend.strategy import STRATEGY_ID, MAX_AGE_SECONDS, expected_action


def init_automation(db):
    from backend.chart_decisions import init_chart
    init_paper(db); init_decisions(db); init_chart(db)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS automation_state (
      account_id INTEGER PRIMARY KEY REFERENCES paper_account(id), enabled INTEGER NOT NULL,
      session_id TEXT, symbol TEXT, timeframe TEXT, strategy_id TEXT, started_at TEXT,
      max_age_seconds INTEGER
    );
    INSERT OR IGNORE INTO automation_state(account_id,enabled) VALUES (1,0);
    CREATE TABLE IF NOT EXISTS automation_events (
      id TEXT PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES paper_account(id),
      strategy_id TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
      candle_timestamp TEXT NOT NULL, session_id TEXT NOT NULL, status TEXT NOT NULL,
      reason TEXT, snapshot_id TEXT REFERENCES decision_snapshots(id),
      decision_id TEXT UNIQUE REFERENCES decisions(id), order_id INTEGER UNIQUE REFERENCES paper_orders(id),
      fill_id INTEGER UNIQUE REFERENCES paper_fills(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      UNIQUE(account_id,strategy_id,symbol,timeframe,candle_timestamp)
    );
    '''); db.commit()


def state(db):
    r=db.execute('SELECT enabled,session_id,symbol,timeframe,strategy_id,started_at,max_age_seconds FROM automation_state WHERE account_id=1').fetchone()
    return dict(zip(('enabled','session_id','symbol','timeframe','strategy_id','started_at','max_age_seconds'),r))


def start(path=DEFAULT_DB, *, symbol='AAPL', timeframe='1m'):
    if symbol != 'AAPL' or timeframe not in DURATIONS: raise ValueError('Unsupported symbol or timeframe')
    with closing(connect(path)) as db:
        init_automation(db); db.execute('BEGIN IMMEDIATE')
        try:
            existing=state(db)
            if existing['enabled']: raise ValueError('Paper automation is already running')
            db.execute("UPDATE automation_state SET enabled=1,session_id=?,symbol=?,timeframe=?,strategy_id=?,started_at=?,max_age_seconds=? WHERE account_id=1", (str(uuid.uuid4()),symbol,timeframe,STRATEGY_ID,utc_now(),MAX_AGE_SECONDS))
            result=state(db); db.commit(); return result
        except Exception: db.rollback(); raise


def stop(path=DEFAULT_DB, reason='Automation stopped'):
    with closing(connect(path)) as db:
        init_automation(db); db.execute('BEGIN IMMEDIATE')
        try:
            db.execute('UPDATE automation_state SET enabled=0 WHERE account_id=1')
            db.execute("UPDATE paper_orders SET status='rejected',reason=? WHERE id IN (SELECT order_id FROM automation_events WHERE status='pending') AND status='pending'",(reason,))
            db.execute("UPDATE automation_events SET status='skipped',reason=?,updated_at=? WHERE status IN ('claimed','pending')",(reason,utc_now()))
            db.commit(); return state(db)
        except Exception: db.rollback(); raise


def claim(session_id, timestamp, path=DEFAULT_DB):
    with closing(connect(path)) as db:
        init_automation(db); db.execute('BEGIN IMMEDIATE')
        try:
            settings=state(db)
            if not settings['enabled'] or settings['session_id'] != session_id: raise ValueError('Automation has stopped or restarted')
            stamp=parse_utc(timestamp); close,_=candle_end(stamp,settings['timeframe']); now=datetime.now(timezone.utc)
            if close > now or close <= parse_utc(settings['started_at']): raise ValueError('Candle did not newly complete during this session')
            event_id=str(uuid.uuid4()); stamp=stamp.isoformat().replace('+00:00','Z'); now_text=utc_now()
            existing=db.execute('SELECT id,status,reason,decision_id,order_id,fill_id FROM automation_events WHERE account_id=1 AND strategy_id=? AND symbol=? AND timeframe=? AND candle_timestamp=?',(settings['strategy_id'],settings['symbol'],settings['timeframe'],stamp)).fetchone()
            if existing:
                db.commit(); return {'id':existing[0], 'status':existing[1], 'reason':existing[2], 'decision_id':existing[3], 'order_id':existing[4], 'fill_id':existing[5], 'duplicate':True}
            db.execute('INSERT INTO automation_events(id,account_id,strategy_id,symbol,timeframe,candle_timestamp,session_id,status,created_at,updated_at) VALUES (?,1,?,?,?,?,?,\'claimed\',?,?)',(event_id,settings['strategy_id'],settings['symbol'],settings['timeframe'],stamp,session_id,now_text,now_text))
            db.commit(); return {'id':event_id,'status':'claimed','duplicate':False}
        except Exception: db.rollback(); raise


def bind(event_id, snapshot_id, path=DEFAULT_DB):
    with closing(connect(path)) as db:
        init_automation(db); db.execute('BEGIN IMMEDIATE')
        try:
            row=db.execute('SELECT e.symbol,e.timeframe,e.candle_timestamp,e.session_id,e.status,s.snapshot_json FROM automation_events e JOIN decision_snapshots s ON s.id=? WHERE e.id=?',(snapshot_id,event_id)).fetchone()
            settings=state(db)
            if not row or row[4]!='claimed' or not settings['enabled'] or row[3]!=settings['session_id']: raise ValueError('Automation claim stopped or not found')
            snap=json.loads(row[5])
            if (snap.get('symbol'),snap.get('timeframe'),snap.get('candle_timestamp')) != tuple(row[:3]) or snap.get('mode')!='live': raise ValueError('Snapshot does not match automation claim')
            db.execute('UPDATE automation_events SET snapshot_id=?,updated_at=? WHERE id=? AND snapshot_id IS NULL',(snapshot_id,utc_now(),event_id))
            if db.execute('SELECT snapshot_id FROM automation_events WHERE id=?',(event_id,)).fetchone()[0] != snapshot_id: raise ValueError('Claim already has a different snapshot')
            db.commit(); return {'id':event_id,'snapshot_id':snapshot_id}
        except Exception: db.rollback(); raise


def skip(event_id, reason, path=DEFAULT_DB):
    # Never persist external error bodies or secrets.
    allowed={'chart_unavailable','image_unavailable','snapshot_unavailable','decision_unavailable'}
    if reason not in allowed: raise ValueError('Unsupported skip reason')
    with closing(connect(path)) as db:
        init_automation(db)
        with db: db.execute("UPDATE automation_events SET status='skipped',reason=?,updated_at=? WHERE id=? AND status='claimed'",(reason,utc_now(),event_id))


def eligible_for_account(db, snapshot, status):
    if execution_eligible(snapshot, status): return True
    # Explicitly marked synthetic tests can run only in a disposable temp DB.
    if os.environ.get('TRADING_BOT_AUTOMATION_TEST_MODE') != '1' or not snapshot.get('test_data') or status != 'valid': return False
    db_path = Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve()
    roots = (Path(tempfile.gettempdir()).resolve(), Path('/private/tmp').resolve())
    if not any(db_path.is_relative_to(root) for root in roots): return False
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='disposable_test_account'").fetchone(): return False
    if not db.execute("SELECT 1 FROM disposable_test_account WHERE id=1 AND label='synthetic disposable account'").fetchone(): return False
    candle = next((r for r in snapshot.get('chart_records',[]) if r.get('candle',{}).get('timestamp')==snapshot.get('candle_timestamp')),None)
    return bool(snapshot_has_inputs(snapshot) and candle and candle.get('ended') and candle.get('coverage',{}).get('complete'))


def apply(event_id, decision_id, path=DEFAULT_DB):
    with closing(connect(path)) as db:
        init_automation(db); db.execute('BEGIN IMMEDIATE')
        try:
            ev=db.execute('SELECT session_id,status,snapshot_id,symbol,timeframe,candle_timestamp,order_id,decision_id FROM automation_events WHERE id=?',(event_id,)).fetchone()
            if not ev: raise ValueError('Automation claim not found')
            if ev[1]!='claimed':
                # A model reply can arrive after Stop. Preserve its audit link,
                # but never revive the stopped claim or create an order.
                if ev[1]=='skipped' and not ev[7] and ev[2]:
                    late=db.execute('SELECT snapshot_id FROM decisions WHERE id=?',(decision_id,)).fetchone()
                    if late and late[0]==ev[2]:
                        db.execute('UPDATE automation_events SET decision_id=?,updated_at=? WHERE id=?',(decision_id,utc_now(),event_id))
                        ev=(*ev[:7],decision_id)
                db.commit(); return {'id':event_id,'status':ev[1],'order_id':ev[6],'decision_id':ev[7],'duplicate':True}
            settings=state(db)
            row=db.execute('SELECT d.snapshot_id,d.proposal,d.status,d.created_at,s.snapshot_json,r.reviewer_status,r.verdict,c.decision_id FROM decisions d JOIN decision_snapshots s ON s.id=d.snapshot_id LEFT JOIN decision_reviews r ON r.decision_id=d.id LEFT JOIN chart_decisions c ON c.decision_id=d.id WHERE d.id=?',(decision_id,)).fetchone()
            reason=None; snap=json.loads(row[4]) if row else None
            if not settings['enabled'] or settings['session_id'] != ev[0]: reason='Automation stopped or restarted'
            elif not row or not ev[2] or row[0]!=ev[2]: reason='Decision does not match the claimed snapshot'
            elif row[7] is None: reason='Chart image analysis is required'
            elif (snap.get('symbol'),snap.get('timeframe'),snap.get('candle_timestamp')) != (ev[3],ev[4],ev[5]): reason='Decision symbol or candle does not match'
            elif row[5]!='valid' or row[6]!='accept' or not eligible_for_account(db,snap,row[2]): reason='Decision is not eligible or reviewer did not accept'
            elif snap.get('strategy_id') != settings['strategy_id'] or row[1] != expected_action(snap): reason='Proposal does not match the configured strategy'
            elif row[1] != expected_action({**snap,'account':{**snap['account'],'shares':shares_for(db,ev[3])}}): reason='Position changed since the snapshot'
            elif settings['max_age_seconds'] is None: reason='Decision age limit is not configured'
            elif not snap.get('decision_time') or not 0 <= (datetime.now(timezone.utc)-parse_utc(snap['decision_time'])).total_seconds() <= settings['max_age_seconds']: reason='Decision has expired'
            if reason:
                db.execute("UPDATE automation_events SET status='skipped',reason=?,decision_id=?,updated_at=? WHERE id=?",(reason,decision_id if row else None,utc_now(),event_id)); db.commit()
                return {'id':event_id,'decision_id':decision_id if row else None,'status':'skipped','reason':reason,'duplicate':False}
            action=row[1]
            result=execute_action(db,request_id='automation:'+event_id,symbol=ev[3],action=action,quantity=snap['order_size'] if action in ('buy','sell') else None,mode='live',manage_transaction=False)
            status='pending' if result['status']=='pending' else ('no_order' if result['status'] in ('hold','wait') else 'skipped')
            db.execute('UPDATE automation_events SET status=?,reason=?,decision_id=?,order_id=?,updated_at=? WHERE id=?',(status,result.get('reason'),decision_id,result['order_id'],utc_now(),event_id));db.commit()
            return {'id':event_id,'decision_id':decision_id,'status':status,'reason':result.get('reason'),'order_id':result['order_id'],'duplicate':False}
        except Exception: db.rollback(); raise


def check_fill(db, order_id):
    """Called inside the simulator transaction immediately before a fill."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='automation_events'").fetchone(): return None
    event=db.execute('SELECT session_id,status,decision_id,fill_id,symbol,timeframe FROM automation_events WHERE order_id=?',(order_id,)).fetchone()
    if not event: return None
    settings=state(db)
    if event[1]!='pending' or event[3] is not None: return 'Decision already handled'
    if not settings['enabled'] or settings['session_id'] != event[0] or (settings['symbol'],settings['timeframe']) != (event[4],event[5]): return 'Automation stopped or restarted'
    row=db.execute('SELECT d.status,d.proposal,s.snapshot_json,r.reviewer_status,r.verdict FROM decisions d JOIN decision_snapshots s ON s.id=d.snapshot_id JOIN decision_reviews r ON r.decision_id=d.id WHERE d.id=?',(event[2],)).fetchone()
    if not row or row[3]!='valid' or row[4]!='accept' or not eligible_for_account(db,json.loads(row[2]),row[0]): return 'Decision is no longer eligible'
    snap=json.loads(row[2])
    if snap.get('strategy_id') != settings['strategy_id'] or row[1] != expected_action(snap): return 'Proposal does not match the configured strategy'
    if row[1] != expected_action({**snap,'account':{**snap['account'],'shares':shares_for(db,event[4])}}): return 'Position changed since the snapshot'
    if settings['max_age_seconds'] is None: return 'Decision age limit is not configured'
    if not snap.get('decision_time') or not 0 <= (datetime.now(timezone.utc)-parse_utc(snap['decision_time'])).total_seconds() <= settings['max_age_seconds']: return 'Decision has expired'
    if db.execute('SELECT 1 FROM paper_fills WHERE order_id=?',(order_id,)).fetchone(): return 'Decision already filled'
    return None


def finish_fill(db, order_id, fill_id=None, reason=None):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='automation_events'").fetchone():
        db.execute("UPDATE automation_events SET status=?,reason=?,fill_id=?,updated_at=? WHERE order_id=? AND status='pending'",
                   ('filled' if fill_id is not None else 'skipped',reason,fill_id,utc_now(),order_id))


def history(path=DEFAULT_DB):
    with closing(connect(path)) as db:
        init_automation(db)
        keys=('id','symbol','timeframe','candle_timestamp','status','reason','snapshot_id','decision_id','order_id','fill_id','created_at','updated_at','proposal','reviewer_verdict','price','cash','shares','crossover')
        rows=db.execute('''SELECT e.id,e.symbol,e.timeframe,e.candle_timestamp,e.status,e.reason,e.snapshot_id,e.decision_id,e.order_id,e.fill_id,e.created_at,e.updated_at,d.proposal,r.verdict,f.price,p.cash,p.shares,json_extract(s.snapshot_json,'$.sma.signal') FROM automation_events e LEFT JOIN decisions d ON d.id=e.decision_id LEFT JOIN decision_snapshots s ON s.id=e.snapshot_id LEFT JOIN decision_reviews r ON r.decision_id=e.decision_id LEFT JOIN paper_fills f ON f.id=e.fill_id LEFT JOIN paper_pnl_history p ON p.fill_id=e.fill_id ORDER BY e.rowid DESC LIMIT 100''').fetchall()
        return {'state':state(db),'events':[dict(zip(keys,r)) for r in rows]}
