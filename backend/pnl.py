"""Decimal paper-account cost basis and immutable event-valued P&L history."""
from decimal import Decimal, InvalidOperation
import json
from datetime import datetime, timedelta, timezone


def init_pnl(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS paper_pnl_state (
      symbol TEXT PRIMARY KEY, cost_basis TEXT NOT NULL, realized TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS paper_pnl_history (
      id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES paper_account(id),
      event_key TEXT NOT NULL UNIQUE, symbol TEXT NOT NULL, event_type TEXT NOT NULL,
      event_time TEXT NOT NULL, price TEXT NOT NULL, price_time TEXT, price_source TEXT NOT NULL,
      observation_id INTEGER NOT NULL REFERENCES observations(id), receipt_id INTEGER NOT NULL REFERENCES receipts(id),
      fill_id INTEGER REFERENCES paper_fills(id), cash TEXT NOT NULL, shares INTEGER NOT NULL,
      average_cost TEXT, realized TEXT NOT NULL, unrealized TEXT, total TEXT
    );
    CREATE TABLE IF NOT EXISTS paper_candle_marks (
      mode TEXT NOT NULL, scope TEXT NOT NULL, symbol TEXT NOT NULL, candle_start TEXT NOT NULL,
      observation_id INTEGER NOT NULL, price TEXT NOT NULL,
      PRIMARY KEY(mode, scope, symbol, candle_start)
    );
    ''')
    db.commit()
    db.execute('BEGIN IMMEDIATE')
    try:
        # Recover weighted basis and realized amounts from legacy fills exactly once.
        # Historical valuation rows cannot be reconstructed from fills alone.
        for symbol, in db.execute('SELECT symbol FROM paper_positions WHERE shares > 0 UNION SELECT DISTINCT symbol FROM paper_orders WHERE status="filled"'):
            if db.execute('SELECT 1 FROM paper_pnl_state WHERE symbol=?', (symbol,)).fetchone():
                continue
            qty, basis, realized = 0, Decimal(0), Decimal(0)
            for side, n, raw_price in db.execute('SELECT o.side,o.quantity,f.price FROM paper_fills f JOIN paper_orders o ON o.id=f.order_id WHERE o.symbol=? ORDER BY f.id', (symbol,)):
                price = Decimal(raw_price)
                if side == 'buy':
                    qty += n; basis += price * n
                elif qty >= n and qty > 0:
                    allocated = basis * n / qty
                    realized += price * n - allocated
                    basis -= allocated; qty -= n
                else:
                    raise ValueError('Cannot reconstruct paper cost basis from prior fills')
            actual = db.execute('SELECT shares FROM paper_positions WHERE symbol=?', (symbol,)).fetchone()
            if (actual[0] if actual else 0) != qty:
                raise ValueError('Paper position does not match historical fills; cost basis cannot be reconstructed')
            db.execute('INSERT INTO paper_pnl_state VALUES (?,?,?)', (symbol, str(basis), str(realized)))
        db.commit()
    except Exception:
        db.rollback()
        raise


def state(db, symbol):
    row = db.execute('SELECT cost_basis,realized FROM paper_pnl_state WHERE symbol=?', (symbol,)).fetchone()
    return (Decimal(row[0]), Decimal(row[1])) if row else (Decimal(0), Decimal(0))


def apply_cost(db, symbol, side, quantity, price, old_shares):
    basis, realized = state(db, symbol)
    if side == 'buy':
        basis += price * quantity
    else:
        allocation = basis * quantity / old_shares
        realized += price * quantity - allocation
        basis -= allocation
        if old_shares == quantity:
            basis = Decimal(0)
    db.execute('INSERT INTO paper_pnl_state VALUES (?,?,?) ON CONFLICT(symbol) DO UPDATE SET cost_basis=excluded.cost_basis, realized=excluded.realized', (symbol, str(basis), str(realized)))


def write_row(db, *, event_key, symbol, event_type, event_time, price, price_time, price_source, observation_id, receipt_id, fill_id=None):
    if db.execute('SELECT 1 FROM paper_pnl_history WHERE event_key=?', (event_key,)).fetchone():
        return
    cash = db.execute('SELECT cash FROM paper_account WHERE id=1').fetchone()[0]
    position = db.execute('SELECT shares FROM paper_positions WHERE symbol=?', (symbol,)).fetchone()
    shares = position[0] if position else 0
    basis, realized = state(db, symbol)
    avg = basis / shares if shares else None
    unrealized = price * shares - basis if shares else Decimal(0)
    total = realized + unrealized
    db.execute('''INSERT INTO paper_pnl_history(account_id,event_key,symbol,event_type,event_time,price,price_time,price_source,observation_id,receipt_id,fill_id,cash,shares,average_cost,realized,unrealized,total)
                  VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
               (event_key,symbol,event_type,event_time,str(price),price_time,price_source,observation_id,receipt_id,fill_id,cash,shares,str(avg) if avg is not None else None,str(realized),str(unrealized),str(total)))


def mark_completed_candles(db, receipt_id, mode, scope, symbol, received_at):
    """Record a new/revised completed 1m close as it becomes observable."""
    from backend.timeframes import parse_utc
    now = parse_utc(received_at)
    start = end = None
    if mode == 'replay':
        try:
            chosen = json.loads(scope)
            if isinstance(chosen, list) and len(chosen) == 3 and chosen[0] == symbol:
                start, end = parse_utc(chosen[1]), parse_utc(chosen[2])
        except (ValueError, TypeError):
            pass
    rows = db.execute('SELECT r.id,r.received_at_utc,o.id,o.source_time,o.message_json FROM receipts r JOIN observations o ON o.id=r.observation_id WHERE r.id<=? AND o.ticker=? ORDER BY r.id', (receipt_id,symbol)).fetchall()
    latest_by_minute = {}
    current_source_minute = None
    for rid, receipt_time, oid, raw_time, raw in rows:
        t = parse_utc(receipt_time)
        if start is not None and not (start <= t < end):
            continue
        try:
            ms = int(raw_time)
            timestamp = datetime.fromtimestamp(ms / 1000, timezone.utc)
            val = Decimal(str(json.loads(raw).get('price')))
            if not val.is_finite() or val <= 0: continue
        except (InvalidOperation, ValueError, TypeError, OverflowError, OSError):
            continue
        minute = timestamp.replace(second=0,microsecond=0)
        if rid == receipt_id: current_source_minute = minute
        if minute + timedelta(minutes=1) > now: continue
        key = (ms,oid)
        if minute not in latest_by_minute or key > latest_by_minute[minute][0]:
            latest_by_minute[minute] = (key,oid,val,timestamp)
    if not latest_by_minute:
        return
    # Process the newest completed minute, and the incoming minute if this is
    # a late observation. Other historical minutes are not replayed as new marks.
    candidates = {max(latest_by_minute)}
    if current_source_minute in latest_by_minute: candidates.add(current_source_minute)
    scope_key = scope or ''
    from backend.live_history import schedule
    for minute in sorted(candidates):
        first = (minute - timedelta(days=2)).date().isoformat()
        last = (minute + timedelta(days=2)).date().isoformat()
        if not any(op <= minute < cl for op, cl in schedule(first, last)):
            continue
        _, oid, price, source_time = latest_by_minute[minute]
        stamp = minute.isoformat().replace('+00:00','Z')
        old = db.execute('SELECT observation_id,price FROM paper_candle_marks WHERE mode=? AND scope=? AND symbol=? AND candle_start=?', (mode,scope_key,symbol,stamp)).fetchone()
        if old and old[0] == oid and Decimal(old[1]) == price:
            continue
        db.execute('INSERT INTO paper_candle_marks VALUES (?,?,?,?,?,?) ON CONFLICT(mode,scope,symbol,candle_start) DO UPDATE SET observation_id=excluded.observation_id, price=excluded.price', (mode,scope_key,symbol,stamp,oid,str(price)))
        shares = db.execute('SELECT shares FROM paper_positions WHERE symbol=?', (symbol,)).fetchone()
        if shares and shares[0] > 0 and (not old or Decimal(old[1]) != price):
            write_row(db, event_key=f'candle:{symbol}:{stamp}:{oid}', symbol=symbol, event_type='candle', event_time=received_at, price=price, price_time=source_time.isoformat(), price_source=f'completed 1m candle {stamp}', observation_id=oid, receipt_id=receipt_id)


def history(db):
    keys = ('id','account_id','event_key','symbol','event_type','event_time','price','price_time','price_source','observation_id','receipt_id','fill_id','cash','shares','average_cost','realized','unrealized','total')
    return [dict(zip(keys,row)) for row in db.execute('SELECT id,account_id,event_key,symbol,event_type,event_time,price,price_time,price_source,observation_id,receipt_id,fill_id,cash,shares,average_cost,realized,unrealized,total FROM paper_pnl_history ORDER BY id')]


def mark_due_live(db, symbol, now):
    """Mark a recently observed one-minute candle at its end without a new tick."""
    from backend.timeframes import parse_utc
    row = db.execute('SELECT r.id,r.received_at_utc FROM receipts r JOIN observations o ON o.id=r.observation_id WHERE o.ticker=? ORDER BY r.id DESC LIMIT 1', (symbol,)).fetchone()
    if not row or not 0 <= (now - parse_utc(row[1])).total_seconds() <= 120:
        return
    db.execute('BEGIN IMMEDIATE')
    try:
        mark_completed_candles(db, row[0], 'live', None, symbol, now.isoformat())
        db.commit()
    except Exception:
        db.rollback()
        raise
