"""Long-only simulated account. All state transitions are SQLite transactions."""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from contextlib import closing
from backend.observations import DEFAULT_DB, connect
from backend.pnl import init_pnl, apply_cost, write_row, mark_completed_candles, history


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def initialize(db):
    existing_account = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_account'").fetchone() is not None
    db.executescript('''
    CREATE TABLE IF NOT EXISTS paper_account (id INTEGER PRIMARY KEY CHECK(id=1), cash TEXT NOT NULL);
    INSERT OR IGNORE INTO paper_account VALUES (1, '1000');
    CREATE TABLE IF NOT EXISTS paper_positions (symbol TEXT PRIMARY KEY, shares INTEGER NOT NULL CHECK(shares>=0));
    CREATE TABLE IF NOT EXISTS paper_orders (
      id INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, request_json TEXT NOT NULL,
      symbol TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER, submitted_at TEXT NOT NULL,
      status TEXT NOT NULL, reason TEXT, mode TEXT NOT NULL, scope TEXT, after_receipt_id INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS paper_no_order_requests (request_id TEXT PRIMARY KEY, request_json TEXT NOT NULL, status TEXT NOT NULL, reason TEXT);
    CREATE TABLE IF NOT EXISTS paper_fills (
      id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL UNIQUE REFERENCES paper_orders(id),
      price TEXT NOT NULL, observation_id INTEGER NOT NULL REFERENCES observations(id),
      receipt_id INTEGER NOT NULL REFERENCES receipts(id), executed_at TEXT NOT NULL
    );
    ''')
    db.execute("""CREATE TABLE IF NOT EXISTS replay_account_checkpoints (
        id TEXT PRIMARY KEY, scope TEXT NOT NULL, receipt_id INTEGER NOT NULL REFERENCES receipts(id),
        captured_at TEXT NOT NULL, cash TEXT NOT NULL, positions_json TEXT NOT NULL
    )""")
    db.execute('CREATE TABLE IF NOT EXISTS paper_account_metadata (id INTEGER PRIMARY KEY CHECK(id=1), created_at TEXT)')
    db.execute('INSERT OR IGNORE INTO paper_account_metadata(id,created_at) VALUES (1,?)', (None if existing_account else utc_now(),))
    db.commit()
    init_pnl(db)


def shares_for(db, symbol):
    row = db.execute('SELECT shares FROM paper_positions WHERE symbol=?', (symbol,)).fetchone()
    return row[0] if row else 0


def execute_action(db, *, request_id, symbol, action, quantity=None, mode='live', scope=None, cursor=0, manage_transaction=True):
    """Submit an idempotent action; execution occurs on a later receipt."""
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise ValueError('A stable request ID is required')
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError('Symbol is required')
    symbol = symbol.strip().upper()
    if mode not in ('live', 'replay') or (mode == 'replay' and (not isinstance(scope, str) or not scope or not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0)):
        raise ValueError('Replay requires a session ID and nonnegative receipt cursor')
    if action not in ('buy', 'sell', 'close', 'hold', 'wait'):
        raise ValueError('Unknown action')
    payload = json.dumps({'symbol':symbol,'action':action,'quantity':quantity,'mode':mode,'scope':scope,'cursor':cursor}, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if manage_transaction: db.execute('BEGIN IMMEDIATE')
    try:
        prior = db.execute('SELECT id,request_json,status,reason FROM paper_orders WHERE request_id=?', (request_id,)).fetchone()
        if prior:
            if prior[1] != payload:
                raise ValueError('Request ID is already used for a different action')
            if manage_transaction: db.commit()
            return {'order_id':prior[0], 'status':prior[2], 'reason':prior[3], 'duplicate':True}
        no_order = db.execute('SELECT request_json,status,reason FROM paper_no_order_requests WHERE request_id=?', (request_id,)).fetchone()
        if no_order:
            if no_order[0] != payload:
                raise ValueError('Request ID is already used for a different action')
            if manage_transaction: db.commit()
            return {'order_id':None, 'status':no_order[1], 'reason':no_order[2], 'duplicate':True}
        held = shares_for(db, symbol)
        if action in ('hold', 'wait'):
            any_held = db.execute('SELECT EXISTS(SELECT 1 FROM paper_positions WHERE shares>0)').fetchone()[0]
            reason = 'Hold requires a position' if action == 'hold' and held == 0 else ('Wait requires no position' if action == 'wait' and any_held else None)
            status = 'rejected' if reason else action
            db.execute('INSERT INTO paper_no_order_requests VALUES (?,?,?,?)', (request_id,payload,status,reason))
            if manage_transaction: db.commit()
            return {'order_id':None, 'status':status, 'reason':reason, 'duplicate':False}
        reason = None
        qty = quantity
        if action == 'close':
            qty = held
            if held == 0: reason = 'No shares to close'
        elif isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0 or qty > 2**63 - 1:
            reason = 'Quantity must be a positive whole number'
            qty = None
        elif action == 'sell' and qty > held:
            reason = 'Insufficient shares'
        threshold = cursor if mode == 'replay' else db.execute('SELECT COALESCE(MAX(id),0) FROM receipts').fetchone()[0]
        status = 'rejected' if reason else 'pending'
        order_id = db.execute('INSERT INTO paper_orders(request_id,request_json,symbol,side,quantity,submitted_at,status,reason,mode,scope,after_receipt_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                              (request_id,payload,symbol,action,qty,utc_now(),status,reason,mode,scope,threshold)).lastrowid
        if manage_transaction: db.commit()
        return {'order_id':order_id, 'status':status, 'reason':reason, 'duplicate':False}
    except Exception:
        if manage_transaction: db.rollback()
        raise


def apply_receipt(db, receipt_id, mode, scope=None, *, capture_checkpoint=False):
    """Fill eligible pending orders using a saved receipt, at most once each."""
    if capture_checkpoint and (mode != 'replay' or not scope):
        raise ValueError('Replay checkpoint requires a replay scope')
    db.execute('BEGIN IMMEDIATE')
    try:
        row = db.execute('SELECT o.id,o.ticker,o.message_json,r.received_at_utc FROM receipts r JOIN observations o ON o.id=r.observation_id WHERE r.id=?', (receipt_id,)).fetchone()
        if not row:
            raise ValueError('Unknown market receipt')
        observation_id, symbol, raw, received_at = row
        mark_completed_candles(db, receipt_id, mode, scope, symbol, received_at)
        try:
            price = Decimal(str(json.loads(raw).get('price')))
            if not price.is_finite() or price <= 0: raise ValueError('invalid price')
        except (InvalidOperation, ValueError, TypeError):
            result = finish_replay_step(db, receipt_id, scope, []) if capture_checkpoint else []
            db.commit()
            return result
        orders = db.execute("SELECT id,side,quantity FROM paper_orders WHERE status='pending' AND symbol=? AND mode=? AND after_receipt_id < ? AND (mode='live' OR scope=?) ORDER BY id", (symbol,mode,receipt_id,scope)).fetchall()
        results=[]
        for order_id, side, qty in orders:
            cash = Decimal(db.execute('SELECT cash FROM paper_account WHERE id=1').fetchone()[0])
            held = shares_for(db, symbol)
            cost = price * qty
            from backend.automation import check_fill, finish_fill
            reason = check_fill(db, order_id) or ('Insufficient cash at fill' if side == 'buy' and cash < cost else
                      'Position changed before close' if side == 'close' and held != qty else
                      'Insufficient shares at fill' if side != 'buy' and held < qty else None)
            if reason:
                db.execute("UPDATE paper_orders SET status='rejected', reason=? WHERE id=?", (reason,order_id))
                finish_fill(db, order_id, reason=reason)
                results.append({'order_id':order_id,'status':'rejected','reason':reason})
                continue
            apply_cost(db, symbol, side, qty, price, held)
            cash = cash - cost if side == 'buy' else cash + cost
            held = held + qty if side == 'buy' else held - qty
            db.execute('UPDATE paper_account SET cash=? WHERE id=1', (str(cash),))
            db.execute('INSERT INTO paper_positions(symbol,shares) VALUES (?,?) ON CONFLICT(symbol) DO UPDATE SET shares=excluded.shares', (symbol,held))
            executed_at = utc_now()
            fill_id = db.execute('INSERT INTO paper_fills(order_id,price,observation_id,receipt_id,executed_at) VALUES (?,?,?,?,?)', (order_id,str(price),observation_id,receipt_id,executed_at)).lastrowid
            source_raw = json.loads(raw).get('time')
            try:
                source_time = datetime.fromtimestamp(int(source_raw) / 1000, timezone.utc).isoformat()
            except (TypeError, ValueError, OverflowError, OSError):
                source_time = None
            write_row(db, event_key=f'fill:{fill_id}', symbol=symbol, event_type='fill', event_time=executed_at, price=price, price_time=source_time, price_source='fill observation', observation_id=observation_id, receipt_id=receipt_id, fill_id=fill_id)
            db.execute("UPDATE paper_orders SET status='filled' WHERE id=?", (order_id,))
            finish_fill(db, order_id, fill_id=fill_id)
            results.append({'order_id':order_id,'status':'filled'})
        result = finish_replay_step(db, receipt_id, scope, results) if capture_checkpoint else results
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def finish_replay_step(db, receipt_id, scope, results):
    """Capture the account atomically after processing a replay receipt."""
    checkpoint_id = str(uuid.uuid4())
    cash = db.execute('SELECT cash FROM paper_account WHERE id=1').fetchone()[0]
    positions = dict(db.execute('SELECT symbol,shares FROM paper_positions'))
    db.execute('INSERT INTO replay_account_checkpoints VALUES (?,?,?,?,?,?)',
               (checkpoint_id, scope, receipt_id, utc_now(), cash, json.dumps(positions, sort_keys=True)))
    return {'results': results, 'checkpoint_id': checkpoint_id}


def account(path=DEFAULT_DB):
    with closing(connect(path)) as db:
        initialize(db)
        cash = db.execute('SELECT cash FROM paper_account WHERE id=1').fetchone()[0]
        positions = [{'symbol': s, 'shares': n} for s,n in db.execute('SELECT symbol,shares FROM paper_positions ORDER BY symbol')]
        rows = db.execute('SELECT o.id,o.request_id,o.symbol,o.side,o.quantity,o.submitted_at,o.status,o.reason,f.price,f.observation_id,f.receipt_id,f.executed_at FROM paper_orders o LEFT JOIN paper_fills f ON f.order_id=o.id ORDER BY o.id DESC').fetchall()
        keys = ('id','request_id','symbol','side','quantity','submitted_at','status','reason','fill_price','observation_id','receipt_id','executed_at')
        pnl_rows = history(db)
        for position in positions:
            last = next((row for row in reversed(pnl_rows) if row['symbol'] == position['symbol'] and row['shares'] == position['shares']), None)
            position['valuation'] = last if last else None
        return {'cash':cash,'positions':positions,'orders':[dict(zip(keys,row)) for row in rows], 'pnl_history': pnl_rows, 'data_label': os.environ.get('TRADING_BOT_DATA_LABEL', 'Recorded market data')}


def drain_live(db):
    """Recover pending live orders against saved later receipts in arrival order."""
    oldest = db.execute("SELECT MIN(after_receipt_id) FROM paper_orders WHERE mode='live' AND status='pending'").fetchone()[0]
    if oldest is None:
        return
    receipt_ids = [row[0] for row in db.execute('SELECT id FROM receipts WHERE id>? ORDER BY id', (oldest,))]
    for receipt_id in receipt_ids:
        apply_receipt(db, receipt_id, 'live')
