"""Isolated event-driven SMA backtest over receipt-ordered recorded quotes."""
from decimal import Decimal, InvalidOperation
from datetime import timedelta
from pathlib import Path
from contextlib import closing

from backend.indicators import add_sma, validate_windows
from backend.observations import DEFAULT_DB
from backend.replay import read_only, replay
from backend.strategy import expected_action, STRATEGY_ID
from backend.timeframes import parse_utc

MAX_EVENTS = 500

def run(start, end, short=2, long=3, initial_cash='10000', quantity=1, path=DEFAULT_DB):
    validate_windows(short, long)
    if long > 100 or isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 10000:
        raise ValueError('Use SMA windows up to 100 and a whole share quantity from 1 to 10000')
    try: cash = Decimal(str(initial_cash))
    except (InvalidOperation, ValueError): raise ValueError('Initial cash must be a positive amount') from None
    if not cash.is_finite() or cash <= 0 or cash > Decimal('100000000'):
        raise ValueError('Initial cash must be positive and at most 100000000')
    initial = cash
    begin, finish = parse_utc(start), parse_utc(end)
    if finish <= begin: raise ValueError('End must be after start')
    if not Path(path).exists(): raise ValueError('No recorded observations are available')
    with closing(read_only(path)) as db:
        db.execute('BEGIN')
        cutoff=db.execute('SELECT COALESCE(MAX(id),0) FROM receipts').fetchone()[0]
        rows=db.execute('SELECT r.received_at_utc FROM receipts r JOIN observations o ON o.id=r.observation_id WHERE o.ticker=? AND r.id<=? ORDER BY r.id',('AAPL',cutoff)).fetchall()
    count=sum(begin <= parse_utc(row[0]) < finish for row in rows)
    if count == 0: raise ValueError('No recorded AAPL receipts in this range')
    if count > MAX_EVENTS: raise ValueError(f'Range contains {count} receipts; choose at most {MAX_EVENTS}')
    shares=0; basis=Decimal(0); realized=Decimal(0); pending=None; trades=[]; decisions=[]
    last_bar=None; last_receipt=None; last_price=None; last_price_time=None
    for pos in range(1,count+1):
        state=replay('AAPL',start,end,'1m',pos,cutoff,path=path)
        event=state['event']; received=parse_utc(event['received_at']); source=parse_utc(event['source_time_utc'])
        if last_receipt is not None and received < last_receipt: raise ValueError('Receipt timestamps are out of order; this range cannot be backtested')
        last_receipt=received
        price=Decimal(str(event['price']))
        if not price.is_finite() or price <= 0: raise ValueError('Recorded price must be positive and finite')
        # A delayed quote cannot fill an order or replace a newer valuation.
        valid_quote=source <= received and (last_price_time is None or source >= last_price_time)
        if valid_quote:
            last_price=price; last_price_time=source
        if pending and valid_quote and source > parse_utc(pending['decision_time']) and received > parse_utc(pending['decision_time']):
            side=pending['action']; qty=quantity if side=='buy' else shares
            reason=None
            if side=='buy' and price*qty > cash: reason='insufficient cash at fill'
            elif side=='close' and qty == 0: reason='position no longer open'
            if reason:
                trades.append({**pending,'status':'rejected','reason':reason,'fill_time':event['received_at'],'price':str(price)})
            else:
                if side=='buy': cash-=price*qty; shares+=qty; basis+=price*qty
                else: cash+=price*qty; realized+=price*qty-basis; shares=0; basis=Decimal(0)
                trades.append({**pending,'status':'filled','fill_time':event['received_at'],'source_time':event['source_time_utc'],'receipt_id':event['receipt_id'],'price':str(price),'quantity':qty,'cash':str(cash),'shares':shares,'realized':str(realized)})
            pending=None
        computed=add_sma(state['records'],short,long)
        completed=[r for r in computed if r['ended'] and r['coverage']['complete']]
        if not completed: continue
        bar=completed[-1]; stamp=bar['candle']['timestamp']
        if last_bar is not None and parse_utc(stamp) <= parse_utc(last_bar): continue
        last_bar=stamp
        signal=bar['sma']['signal']
        action=expected_action({'account':{'shares':shares},'sma':{'signal':signal}})
        row={'candle_time':stamp,'decision_time':event['received_at'],'receipt_id':event['receipt_id'],'signal':signal,'short_sma':bar['sma']['short'],'long_sma':bar['sma']['long'],'action':action,'status':'observed'}
        # If a quote arrives late, an already-old bar is not a fresh actionable signal.
        bar_end=parse_utc(stamp)+timedelta(minutes=1)
        if action in ('buy','close') and not pending:
            if not valid_quote or (received-bar_end).total_seconds()>60:
                row['status']='skipped: stale decision evidence'
            else:
                row['status']='pending'; pending={**row}
        elif pending: row['status']='skipped: order pending'
        decisions.append(row)
    if pending: trades.append({**pending,'status':'unfilled','reason':'no later eligible quote'})
    unrealized=(last_price*shares-basis) if shares and last_price is not None else Decimal(0)
    equity=cash+(last_price*shares if last_price is not None else Decimal(0))
    return {'symbol':'AAPL','timeframe':'1m','strategy':STRATEGY_ID,'start':start,'end':end,'cutoff_receipt_id':cutoff,'receipts':count,
            'short':short,'long':long,'quantity':quantity,'initial_cash':str(initial),'cash':str(cash),'shares':shares,
            'last_price':str(last_price) if last_price is not None else None,'last_price_time':last_price_time.isoformat() if last_price_time else None,
            'realized':str(realized),'unrealized':str(unrealized),'equity':str(equity),'total_pnl':str(equity-initial),
            'decisions':decisions,'trades':trades,'source':'recorded observations (not backfilled history)',
            'assumptions':'Separate long-only simulated account; one-minute completed bars; next later non-stale source quote fills; no fees or slippage; no future revisions; no order affects the live paper account.'}
