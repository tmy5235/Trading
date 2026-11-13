"""Freeze decision evidence and evaluate it without executing any trades."""
import json
import os
import socket
import sqlite3
import ssl
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from backend.observations import DEFAULT_DB, connect
from backend.candles import one_minute_candles
from backend.live_history import schedule
from backend.timeframes import DURATIONS, aggregate, parse_utc
from backend.indicators import add_sma, add_instances, validate_windows
from backend.replay import replay
from backend.strategy import STRATEGY_ID, RULES, expected_action

MODEL = 'gpt-6-luna'
CHOICES = ('buy', 'sell', 'hold', 'wait', 'close')


def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS decision_snapshots (
      id TEXT PRIMARY KEY, created_at TEXT NOT NULL, snapshot_json TEXT NOT NULL,
      eligibility TEXT NOT NULL, reason TEXT
    );
    CREATE TABLE IF NOT EXISTS decision_reviews (
      decision_id TEXT PRIMARY KEY REFERENCES decisions(id), analyst_model TEXT NOT NULL,
      analyst_output_json TEXT, reviewer_model TEXT NOT NULL, reviewer_input_json TEXT,
      reviewer_output_json TEXT, reviewer_status TEXT NOT NULL, verdict TEXT,
      final_check_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS decisions (
      id TEXT PRIMARY KEY, snapshot_id TEXT, created_at TEXT NOT NULL, provider TEXT NOT NULL,
      model TEXT NOT NULL, proposal TEXT, explanation TEXT NOT NULL, status TEXT NOT NULL,
      FOREIGN KEY(snapshot_id) REFERENCES decision_snapshots(id)
    );
    ''')
    db.commit()


def candle_end(start, timeframe):
    sessions = schedule((start-timedelta(days=2)).date().isoformat(), (start+timedelta(days=2)).date().isoformat())
    session = next(((op,cl) for op,cl in sessions if op <= start < cl), None)
    if not session:
        raise ValueError('Candle is outside the configured regular session')
    op, cl = session
    size = DURATIONS[timeframe]
    if size is None:
        if start != op: raise ValueError('Daily candle must begin at session open')
        return cl, sessions
    offset = int((start-op).total_seconds())
    if offset < 0 or offset % (size*60) != 0 or start.second or start.microsecond:
        raise ValueError('Candle timestamp is not aligned to the interval')
    return min(start+timedelta(minutes=size),cl), sessions


def ledger_as_of(db, symbol, timestamp):
    metadata = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_account_metadata'").fetchone()
    if not metadata:
        return None, 'Paper account creation time is unavailable'
    created = db.execute('SELECT created_at FROM paper_account_metadata WHERE id=1').fetchone()
    if not created or not created[0] or parse_utc(created[0]) > timestamp:
        return None, 'Paper account was not verifiably available at candle close'
    cash = Decimal('1000'); positions = {}; past_cash = cash; past_positions = {}
    rows = db.execute('SELECT o.symbol,o.side,o.quantity,f.price,f.executed_at FROM paper_fills f JOIN paper_orders o ON o.id=f.order_id ORDER BY f.id').fetchall()
    for ticker, side, quantity, raw_price, executed_at in rows:
        price = Decimal(raw_price)
        delta = price * quantity * (-1 if side == 'buy' else 1)
        share_delta = quantity if side == 'buy' else -quantity
        cash += delta; positions[ticker] = positions.get(ticker,0)+share_delta
        if parse_utc(executed_at) <= timestamp:
            past_cash += delta; past_positions[ticker] = past_positions.get(ticker,0)+share_delta
    current_cash = Decimal(db.execute('SELECT cash FROM paper_account WHERE id=1').fetchone()[0])
    current_positions = dict(db.execute('SELECT symbol,shares FROM paper_positions'))
    if cash != current_cash or any(positions.get(s,0) != current_positions.get(s,0) for s in set(positions)|set(current_positions)):
        return None, 'Paper account does not reconcile with its recorded fills'
    return {'cash': str(past_cash), 'shares': past_positions.get(symbol,0),
            'other_open_positions': sum(n for s,n in past_positions.items() if s != symbol)}, None


def create_snapshot(symbol, timeframe, timestamp, short=2, long=3, mode='live', path=DEFAULT_DB, *, replay_start=None, replay_end=None, replay_position=None, replay_cutoff=None, replay_checkpoint_id=None):
    validate_windows(short,long)
    if timeframe not in DURATIONS: raise ValueError('Unsupported interval')
    if not isinstance(symbol,str) or not symbol.strip(): raise ValueError('Symbol is required')
    if mode not in ('live','replay'): raise ValueError('Unsupported mode')
    symbol = symbol.strip().upper()
    if symbol != 'AAPL': raise ValueError('Only AAPL is configured with the XNAS calendar')
    start = parse_utc(timestamp)
    close, sessions = candle_end(start,timeframe)
    if mode == 'live' and close > datetime.now(timezone.utc): raise ValueError('Candle has not closed')
    snapshot_id = str(uuid.uuid4())
    with closing(connect(path)) as db:
        init(db)
        db.execute('BEGIN IMMEDIATE')
        try:
            replay_detail = None
            if mode == 'replay':
                if (not replay_start or not replay_end or not replay_checkpoint_id or
                    isinstance(replay_position, bool) or not isinstance(replay_position, int) or replay_position <= 0 or
                    isinstance(replay_cutoff, bool) or not isinstance(replay_cutoff, int) or replay_cutoff < 0):
                    raise ValueError('Replay requires a range, positive position, cutoff, and account checkpoint')
                point = replay(symbol, replay_start, replay_end, timeframe, replay_position, replay_cutoff, path=path)
                event = point['event']
                if event is None or parse_utc(event['received_at']) < close:
                    raise ValueError('Selected candle has not closed at the replay point')
                table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='replay_account_checkpoints'").fetchone()
                checkpoint = db.execute('SELECT scope,receipt_id,captured_at,cash,positions_json FROM replay_account_checkpoints WHERE id=?', (replay_checkpoint_id,)).fetchone() if table else None
                if not checkpoint:
                    raise ValueError('Replay account checkpoint not found; replay the observation again')
                try:
                    scope = json.loads(checkpoint[0])
                except (TypeError, ValueError):
                    raise ValueError('Replay checkpoint has an invalid selection')
                if (not isinstance(scope, list) or len(scope) != 3 or scope[0] != symbol or
                    parse_utc(scope[1]) != parse_utc(replay_start) or parse_utc(scope[2]) != parse_utc(replay_end) or
                    checkpoint[1] != event['receipt_id']):
                    raise ValueError('Replay checkpoint does not match the selected range and position')
                positions = json.loads(checkpoint[4])
                ledger = {'cash': checkpoint[3], 'shares': positions.get(symbol, 0),
                          'other_open_positions': sum(n for s,n in positions.items() if s != symbol)}
                indicator_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='indicator_instances'").fetchone()
                instances = [{'id': i, 'name': name, 'type': kind, 'settings': json.loads(settings)}
                             for i, name, kind, settings in db.execute('SELECT id,name,type,settings_json FROM indicator_instances ORDER BY id')] if indicator_table else []
                records = add_instances(add_sma(point['records'], short, long), instances)
                decision_time = event['received_at']
                replay_detail = {'start': replay_start, 'end': replay_end, 'position': replay_position,
                                 'cutoff': replay_cutoff, 'event': event, 'checkpoint_id': replay_checkpoint_id,
                                 'account_captured_at': checkpoint[2]}
            else:
                minutes = one_minute_candles(db, symbol, now=close, as_of_receipt=True)
                if minutes:
                    first=parse_utc(minutes[0]['candle']['timestamp'])
                    sessions=schedule((first-timedelta(days=2)).date().isoformat(),(start+timedelta(days=2)).date().isoformat())
                records=add_sma(aggregate([{'candle': x['candle'], 'available_at':close} for x in minutes],sessions,timeframe,close),short,long)
                indicator_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='indicator_instances'").fetchone()
                instances = [{'id': i, 'name': name, 'type': kind, 'settings': json.loads(settings)}
                             for i, name, kind, settings in db.execute('SELECT id,name,type,settings_json FROM indicator_instances ORDER BY id')] if indicator_table else []
                records = add_instances(records, instances)
                ledger, reason=ledger_as_of(db,symbol,close)
                decision_time = close.isoformat()
            selected=next((r for r in records if parse_utc(r['candle']['timestamp'])==start),None)
            reasons=[]
            if selected is None: reasons.append('No candle was available at the decision point')
            elif not selected['ended'] or not selected['coverage']['complete']: reasons.append('Candle coverage is incomplete')
            if selected and (selected['sma']['short'] is None or selected['sma']['long'] is None): reasons.append('SMA warm-up is incomplete')
            if mode == 'replay':
                reasons.append('Replay decisions cannot authorize paper orders')
            elif reason:
                reasons.append(reason)
            try:
                size=int(os.environ.get('PAPER_ORDER_SIZE','1'))
                if size<=0: raise ValueError
            except ValueError:
                size=None; reasons.append('Configured order size must be a positive whole number')
            is_test = os.environ.get('TRADING_BOT_DATA_IS_TEST') == '1'
            if is_test: reasons.append('Synthetic test data is ineligible for paper orders')
            data={'id':snapshot_id,'source':os.environ.get('TRADING_BOT_DATA_LABEL','recorded Yahoo observations'),'mode':mode,'symbol':symbol,'timeframe':timeframe,
                  'candle_timestamp':start.isoformat().replace('+00:00','Z'),'decision_time':decision_time, 'replay':replay_detail, 'strategy_id':STRATEGY_ID, 'strategy_rules':RULES,
                  'candle':selected['candle'] if selected else None,'sma':selected['sma'] if selected else None,
                  'short_window':short,'long_window':long,'account':ledger,'order_size':size,
                  'chart_records':records,'indicator_instances':instances,'test_data':is_test,'eligible_for_paper_order':not reasons,
                  'ineligibility_reasons':reasons}
            db.execute('INSERT INTO decision_snapshots VALUES (?,?,?,?,?)',(snapshot_id,datetime.now(timezone.utc).isoformat(),json.dumps(data,sort_keys=True,allow_nan=False),'eligible' if not reasons else 'ineligible','; '.join(reasons) or None))
            db.commit()
            return data
        except Exception:
            db.rollback(); raise


def save_test_snapshot(db, data):
    """For isolated tests only; never eligible for paper orders."""
    data={**data,'id':str(uuid.uuid4()),'test_data':True,'eligible_for_paper_order':False}
    db.execute('INSERT INTO decision_snapshots VALUES (?,?,?,?,?)',(data['id'],datetime.now(timezone.utc).isoformat(),json.dumps(data,sort_keys=True),'ineligible','Synthetic test snapshot'))
    db.commit()
    return data


def post_decision(body, api_key, timeout):
    request=urllib.request.Request('https://api.openai.com/v1/decisions', data=json.dumps(body,allow_nan=False).encode(), headers={'Authorization':f'Bearer {api_key}','Content-Type':'application/json'},method='POST')
    import certifi
    with urllib.request.urlopen(request,timeout=timeout,context=ssl.create_default_context(cafile=certifi.where())) as response:
        return json.load(response)


def provider_request(snapshot, api_key, timeout=20):
    descriptions={'buy':'Add the configured number of shares.', 'sell':'Reduce the position by the configured number of shares.', 'hold':'Keep an open position.', 'wait':'Stay flat in cash.', 'close':'Exit the entire open position.'}
    body={'model':MODEL,'input':[{'role':'user','content':[{'type':'input_json','data':snapshot}]}],
          'questions':[{'type':'choice','name':'paper_action','instructions':'Analyst: Given the supplied evidence, choose one action for this long-only simulated account. Do not assume missing facts.', 'choices':[{'value':key,'description':value} for key,value in descriptions.items()]}]}
    return post_decision(body, api_key, timeout)


def snapshot_has_inputs(snapshot):
    try:
        candle, sma, account = snapshot['candle'], snapshot['sma'], snapshot['account']
        cash, price = Decimal(str(account['cash'])), Decimal(str(candle['close']))
        short, long = Decimal(str(sma['short'])), Decimal(str(sma['long']))
        shares, other, size = account['shares'], account.get('other_open_positions', 0), snapshot['order_size']
        return (cash.is_finite() and cash >= 0 and price.is_finite() and price > 0
                and short.is_finite() and long.is_finite() and 'signal' in sma
                and all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in (shares, other))
                and isinstance(size, int) and not isinstance(size, bool) and size > 0)
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return False


def evaluate(snapshot, answer):
    account=snapshot.get('account') or {}
    if answer not in CHOICES: return 'malformed_answer', 'The provider did not return an allowed action.'
    try:
        cash=Decimal(account['cash']); shares=account['shares']; other=account.get('other_open_positions',0)
        price=Decimal(str(snapshot['candle']['close'])); size=snapshot['order_size']
        if not cash.is_finite() or cash < 0 or not price.is_finite() or price<=0 or isinstance(shares,bool) or not isinstance(shares,int) or shares<0 or isinstance(size,bool) or not isinstance(size,int) or size<=0: raise ValueError
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return 'missing_input', 'The frozen account, closing price, or order size is missing or invalid.'
    reason=None
    if answer=='buy' and cash < price*size: reason='insufficient cash for the configured order size at the frozen close'
    elif answer=='sell' and shares < size: reason='insufficient shares for the configured order size'
    elif answer in ('hold','close') and shares == 0: reason='no open position in this symbol'
    elif answer=='wait' and (shares > 0 or other > 0): reason='the account is not flat'
    if not reason and snapshot.get('strategy_id') == STRATEGY_ID and answer != expected_action(snapshot):
        reason=f"strategy requires {expected_action(snapshot) or 'valid inputs'} for this crossover and position"
    factual=f"Frozen close {price}; short SMA {snapshot['sma']['short']}; long SMA {snapshot['sma']['long']}; crossover {snapshot['sma']['signal'] or 'none'}; cash {cash}; shares {shares}; order size {size}."
    return ('invalid_proposal' if reason else 'valid'), factual + (f' Account rule: {reason}.' if reason else ' Account rules permit the proposed action at the frozen values.')


def decide(snapshot_id, *, path=DEFAULT_DB, provider=provider_request, reviewer=None, api_key=None):
    with closing(connect(path)) as db:
        init(db)
        row=db.execute('SELECT snapshot_json FROM decision_snapshots WHERE id=?',(snapshot_id,)).fetchone()
        status='missing_input'; proposal=None; analyst_output=None; review=None; key=None
        explanation='Snapshot was not found.'
        if row:
            snapshot=json.loads(row[0])
            if not snapshot_has_inputs(snapshot):
                explanation='Snapshot is missing a completed candle, warmed-up indicators, account state, or order size.'
            else:
                key=api_key if api_key is not None else os.environ.get('OPENAI_API_KEY')
                if not key:
                    status='missing_input'; explanation='OPENAI_API_KEY is not present in the backend environment.'
                else:
                    try:
                        result=provider(snapshot,key)
                        analyst_output=result
                        answers=result.get('answers',[]) if isinstance(result,dict) else []
                        matches=[a for a in answers if isinstance(a,dict) and a.get('name')=='paper_action' and a.get('type')=='choice']
                        if len(matches)!=1 or not isinstance(matches[0].get('choice'),str):
                            status='malformed_answer'; explanation='Provider response lacked exactly one named choice answer.'
                        else:
                            proposal=matches[0]['choice']; status,explanation=evaluate(snapshot,proposal)
                    except (TimeoutError, socket.timeout):
                        status='timeout'; explanation='Provider request timed out.'
                    except urllib.error.HTTPError as error:
                        status='provider_error'; explanation=f'Provider returned HTTP {error.code}.'
                        error.close()
                    except urllib.error.URLError as error:
                        if isinstance(error.reason, (TimeoutError,socket.timeout)):
                            status='timeout'; explanation='Provider request timed out.'
                        else:
                            status='provider_error'; explanation='Provider connection or HTTP request failed.'
                    except (ValueError, TypeError, KeyError):
                        status='malformed_answer'; explanation='Provider response was malformed.'
                    except Exception:
                        status='provider_error'; explanation='Provider request failed.'
        from backend.review import execution_eligible, run_review, save_review, strategy_is_configured
        if row and proposal in CHOICES and key:
            review = run_review(snapshot, {'paper_action':proposal}, explanation, status, key, reviewer=reviewer)
            status = review['final_check']['status']
        else:
            review = run_review(None, {}, explanation, status, None)
        review['final_check']['eligible_for_paper_order'] = execution_eligible(snapshot if row else None, status)
        review['final_check']['snapshot_ineligibility_reasons'] = snapshot.get('ineligibility_reasons', []) if row else ['Snapshot not found']
        review['final_check']['strategy_status'] = 'configured' if row and strategy_is_configured(snapshot) else 'not_configured'
        if review['final_check']['strategy_status'] == 'not_configured':
            review['final_check']['explanation'] += ' No executable strategy rules are configured.'
        review['analyst_output'] = analyst_output
        decision_id=str(uuid.uuid4())
        db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)',(decision_id,snapshot_id if row else None,datetime.now(timezone.utc).isoformat(),'OpenAI',MODEL,proposal,explanation,status))
        save_review(db,decision_id,analyst_output,review)
        db.commit()
        return {'decision_id':decision_id,'snapshot_id':snapshot_id if row else None,'provider':'OpenAI','model':MODEL,'proposal':proposal,'explanation':explanation,'status':status,'eligible_for_paper_order': execution_eligible(snapshot if row else None, status), 'review':review}
