import csv
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import exchange_calendars as xcals
from fastapi import FastAPI, HTTPException, Query, WebSocket
from backend.classroom_decisions import ClassroomRequest
from backend.chart_agent import ChartAgentRequest, run_chart_agent
from backend.indicators import add_instances, add_sma, types_metadata, validate_windows
from backend.indicator_store import create_instance, list_instances, update_instance
from pydantic import BaseModel
from typing import Any
from contextlib import closing, asynccontextmanager
from backend.observations import connect
from backend.paper import initialize as init_paper, execute_action, apply_receipt, account

from backend.timeframes import aggregate, parse_utc

@asynccontextmanager
async def lifespan(app):
    from backend.automation import stop
    stop(reason='Backend restarted; automation requires a new start')
    yield


app = FastAPI(lifespan=lifespan)


class IndicatorRequest(BaseModel):
    name: str
    type: str
    settings: dict


@app.get('/api/indicator-types')
def indicator_types():
    return types_metadata()


@app.get('/api/indicators')
def indicators():
    return list_instances()


@app.post('/api/indicators', status_code=201)
def create_indicator(request: IndicatorRequest):
    try:
        return create_instance(request.name, request.type, request.settings)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


class IndicatorSettingsRequest(BaseModel):
    settings: dict


@app.put('/api/indicators/{instance_id}')
def change_indicator(instance_id: int, request: IndicatorSettingsRequest):
    try:
        return update_instance(instance_id,request.settings)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

CANDLE_FILE = Path(__file__).parent / 'data' / 'sample-candles.csv'


def sample_history(timeframe, as_of):
    with CANDLE_FILE.open(newline='', encoding='utf-8') as file:
        candles = [
            {'timestamp': row['timestamp'], 'open': float(row['open']),
             'high': float(row['high']), 'low': float(row['low']),
             'close': float(row['close']), 'volume': int(row['volume']) if row['volume'].strip() else None}
            for row in csv.DictReader(file)
        ]
    if not candles:
        return []
    stamps = [parse_utc(candle['timestamp']) for candle in candles]
    if len(set(stamps)) != len(stamps):
        raise ValueError('CSV contains duplicate minute timestamps without revision availability')
    # This static sample has no receipt history. Model its synthetic availability
    # as the end of each minute; do not treat it as a historical live recording.
    minutes = [{'candle': candle, 'available_at': stamp + timedelta(minutes=1)}
               for candle, stamp in zip(candles, stamps)]
    first = (min(stamps) - timedelta(days=2)).date().isoformat()
    last = (max(stamps) + timedelta(days=2)).date().isoformat()
    schedule = xcals.get_calendar('XNAS', start=first, end=last).schedule
    sessions = [(row['open'].to_pydatetime(), row['close'].to_pydatetime())
                for _, row in schedule.iterrows()]
    return aggregate(minutes, sessions, timeframe, as_of)


@app.get('/api/health')
def health():
    return {'status': 'ok'}


@app.get('/api/history')
def history(symbol: str = 'AAPL', timeframe: Literal['1m', '5m', '15m', '1h', '1d'] = '1m',
            as_of: datetime | None = None):
    if symbol.upper() != 'AAPL':
        raise HTTPException(status_code=404, detail='Only the synthetic AAPL sample is available')
    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
        raise HTTPException(status_code=422, detail='as_of must include a timezone')
    records = sample_history(timeframe, as_of or datetime.now(timezone.utc))
    return {'symbol': 'AAPL', 'timeframe': timeframe,
            'candles': [entry['candle'] for entry in records],
            'status': [{'timestamp': entry['candle']['timestamp'],
                        'coverage': entry['coverage'], 'ended': entry['ended']}
                       for entry in records]}


@app.get('/api/live-history')
def live_history(symbol: str = 'AAPL', timeframe: Literal['1m', '5m', '15m', '1h', '1d'] = '1m', short: int = Query(2, gt=0), long: int = Query(3, gt=0)):
    from backend.live_history import snapshot
    if symbol.upper() != 'AAPL':
        raise HTTPException(status_code=404, detail='Only AAPL is configured')
    try:
        validate_windows(short, long)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    result = snapshot('AAPL', timeframe)
    result['records'] = add_instances(add_sma(result['records'], short, long), list_instances())
    return result


@app.websocket('/api/live')
async def live_socket(websocket: WebSocket):
    import asyncio
    from fastapi import WebSocketDisconnect
    from backend.live_history import snapshot
    symbol = websocket.query_params.get('symbol', 'AAPL').upper()
    timeframe = websocket.query_params.get('timeframe', '1m')
    try:
        short, long = int(websocket.query_params.get('short', '2')), int(websocket.query_params.get('long', '3'))
        validate_windows(short, long)
    except ValueError:
        await websocket.close(code=1008)
        return
    if symbol != 'AAPL' or timeframe not in ('1m', '5m', '15m', '1h', '1d'):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        while True:
            state = await asyncio.to_thread(snapshot, symbol, timeframe)
            state['records'] = add_instances(add_sma(state['records'], short, long), list_instances())
            latest = state['records'][-1] if state['records'] else None
            state['candle'] = latest['candle'] if latest else None
            state['completion'] = {'ended': latest['ended'], 'coverage': latest['coverage']} if latest else None
            await websocket.send_json(state)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    except (RuntimeError, OSError):
        pass


@app.get('/api/replay/options')
def replay_options():
    from backend.replay import options
    return options()


@app.get('/api/chart-snapshot')
def saved_chart_snapshot(symbol: str, start: str, end: str,
                         timeframe: Literal['1m', '5m', '15m', '1h', '1d'] = '1m',
                         short: int = Query(2, gt=0), long: int = Query(3, gt=0)):
    from backend.replay import chart_snapshot
    try:
        validate_windows(short, long)
        return chart_snapshot(symbol, start, end, timeframe, short=short, long=long)
    except (ValueError, OverflowError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/api/replay')
def replay_history(symbol: str, start: str, end: str,
                   timeframe: Literal['1m', '5m', '15m', '1h', '1d'] = '1m',
                   position: int = 0, cutoff: int | None = None, short: int = Query(2, gt=0), long: int = Query(3, gt=0)):
    from backend.replay import replay
    try:
        if cutoff is not None and cutoff < 0:
            raise ValueError('cutoff must be non-negative')
        validate_windows(short, long)
        result = replay(symbol.upper(), start, end, timeframe, position, cutoff)
        result['records'] = add_instances(add_sma(result['records'], short, long), list_instances())
        result['source'] = os.environ.get('TRADING_BOT_DATA_LABEL', 'recorded Yahoo Finance')
        return result
    except (ValueError, OverflowError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


class PaperAction(BaseModel):
    request_id: str
    symbol: str
    action: str
    quantity: Any = None
    mode: str = 'live'
    scope: str | None = None
    cursor: int = 0


class ReplayStep(BaseModel):
    receipt_id: int
    scope: str


@app.get('/api/paper/account')
def paper_account():
    return account()


@app.post('/api/paper/action')
def paper_action(request: PaperAction):
    try:
        with closing(connect()) as db:
            init_paper(db)
            return execute_action(db, **request.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/api/paper/replay-step')
def paper_replay_step(request: ReplayStep):
    if not request.scope or request.receipt_id <= 0:
        raise HTTPException(status_code=422, detail='Replay session and receipt are required')
    try:
        with closing(connect()) as db:
            init_paper(db)
            return apply_receipt(db, request.receipt_id, 'replay', request.scope, capture_checkpoint=True)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


class SnapshotRequest(BaseModel):
    symbol: str = 'AAPL'
    timeframe: Literal['1m','5m','15m','1h','1d'] = '1m'
    candle_timestamp: str
    short: int = 2
    long: int = 3
    mode: Literal['live','replay'] = 'live'
    replay_start: str | None = None
    replay_end: str | None = None
    replay_position: int | None = None
    replay_cutoff: int | None = None
    replay_checkpoint_id: str | None = None


@app.post('/api/chart-agent')
async def chart_agent_request(request: ChartAgentRequest):
    return await run_chart_agent(request)


@app.post('/api/classroom-decisions')
def classroom_decision(request: ClassroomRequest):
    from backend.classroom_decisions import decide
    try:
        return decide(request.image_url, request.context.model_dump())
    except (ValueError, OverflowError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/api/classroom-decisions')
def classroom_decision_history():
    from backend.classroom_decisions import history
    return history()


@app.get('/api/classroom-decisions/{decision_id}/chart.png')
def classroom_image(decision_id: str):
    from fastapi.responses import Response
    from backend.classroom_decisions import init
    with closing(connect()) as db:
        init(db)
        row = db.execute('SELECT png FROM classroom_chart_decisions WHERE id=?', (decision_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail='Chart capture not found')
        return Response(content=row[0], media_type='image/png', headers={'Cache-Control':'no-store'})


class DecisionRequest(BaseModel):
    snapshot_id: str
    image_url: str | None = None
    capture: dict | None = None


@app.post('/api/decision-snapshots', status_code=201)
def make_decision_snapshot(request: SnapshotRequest):
    from backend.decisions import create_snapshot
    try:
        return create_snapshot(request.symbol,request.timeframe,request.candle_timestamp,request.short,request.long,request.mode, replay_start=request.replay_start, replay_end=request.replay_end, replay_position=request.replay_position, replay_cutoff=request.replay_cutoff, replay_checkpoint_id=request.replay_checkpoint_id)
    except (ValueError, OverflowError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get('/api/decision-snapshots/{snapshot_id}')
def get_decision_snapshot(snapshot_id: str):
    from backend.decisions import init
    import json
    with closing(connect()) as db:
        init(db)
        row=db.execute('SELECT snapshot_json FROM decision_snapshots WHERE id=?',(snapshot_id,)).fetchone()
        if not row: raise HTTPException(status_code=404, detail='Snapshot not found')
        return json.loads(row[0])


@app.post('/api/decisions')
def create_decision(request: DecisionRequest):
    from backend.decisions import decide
    if request.image_url is not None or request.capture is not None:
        from backend.chart_decisions import decide_chart
        try:
            return decide_chart(request.snapshot_id, request.image_url, request.capture)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    return decide(request.snapshot_id)


@app.get('/api/decisions/{decision_id}')
def get_decision(decision_id: str):
    from backend.decisions import init
    with closing(connect()) as db:
        init(db)
        row=db.execute('SELECT id,snapshot_id,created_at,provider,model,proposal,explanation,status FROM decisions WHERE id=?',(decision_id,)).fetchone()
        if not row: raise HTTPException(status_code=404, detail='Decision not found')
        keys=('decision_id','snapshot_id','created_at','provider','model','proposal','explanation','status')
        result = dict(zip(keys,row))
        review_row = db.execute('SELECT analyst_model,analyst_output_json,reviewer_model,reviewer_input_json,reviewer_output_json,reviewer_status,verdict,final_check_json FROM decision_reviews WHERE decision_id=?',(decision_id,)).fetchone()
        if review_row:
            import json
            result['review']={'analyst_model':review_row[0], 'analyst_output':json.loads(review_row[1]) if review_row[1] else None,
                              'reviewer_model':review_row[2], 'reviewer_input':json.loads(review_row[3]) if review_row[3] else None,
                              'reviewer_output':json.loads(review_row[4]) if review_row[4] else None, 'reviewer_status':review_row[5],
                              'verdict':review_row[6], 'final_check':json.loads(review_row[7])}
        snapshot_row = db.execute('SELECT eligibility,snapshot_json FROM decision_snapshots WHERE id=?', (result['snapshot_id'],)).fetchone()
        import json
        from backend.review import execution_eligible
        result['eligible_for_paper_order'] = bool(snapshot_row and review_row and review_row[5] == 'valid' and review_row[6] == 'accept' and execution_eligible(json.loads(snapshot_row[1]), result['status']))
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chart_decisions'").fetchone():
            chart = db.execute('SELECT sha256,capture_json,questions_json,answers_json,comparison,input_json FROM chart_decisions WHERE decision_id=?',(decision_id,)).fetchone()
            if chart:
                import json
                snap = json.loads(db.execute('SELECT snapshot_json FROM decision_snapshots WHERE id=?',(result['snapshot_id'],)).fetchone()[0])
                result.update({'png_sha256':chart[0], 'capture':json.loads(chart[1]), 'questions':json.loads(chart[2]), 'answers':json.loads(chart[3]), 'comparison':chart[4], 'calculated_crossover':(snap.get('sma') or {}).get('signal') or 'none', 'image_url':f'/api/decisions/{decision_id}/chart.png', 'input_snapshot':json.loads(chart[5]) if chart[5] else None})
        return result


@app.get('/api/decisions/{decision_id}/chart.png')
def decision_chart_image(decision_id: str):
    from fastapi.responses import Response
    from backend.chart_decisions import init_chart
    with closing(connect()) as db:
        init_chart(db)
        row=db.execute('SELECT png FROM chart_decisions WHERE decision_id=?',(decision_id,)).fetchone()
        if not row: raise HTTPException(status_code=404, detail='Chart capture not found')
        return Response(content=row[0], media_type='image/png', headers={'Cache-Control':'no-store'})


class AutomationStart(BaseModel):
    symbol: str = 'AAPL'
    timeframe: Literal['1m','5m','15m','1h','1d'] = '1m'


class AutomationClaim(BaseModel):
    session_id: str
    candle_timestamp: str


class AutomationBind(BaseModel):
    snapshot_id: str


class AutomationApply(BaseModel):
    decision_id: str


class AutomationSkip(BaseModel):
    reason: Literal['chart_unavailable','image_unavailable','snapshot_unavailable','decision_unavailable']


@app.get('/api/automation')
def automation_status():
    from backend.automation import history
    return history()


@app.post('/api/automation/start')
def start_automation(request: AutomationStart):
    from backend.automation import start
    try: return start(symbol=request.symbol, timeframe=request.timeframe)
    except ValueError as error: raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/api/automation/stop')
def stop_automation():
    from backend.automation import stop
    return stop()


@app.post('/api/automation/claim')
def claim_automation(request: AutomationClaim):
    from backend.automation import claim
    try: return claim(request.session_id,request.candle_timestamp)
    except ValueError as error: raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/api/automation/{event_id}/snapshot')
def bind_automation_snapshot(event_id: str, request: AutomationBind):
    from backend.automation import bind
    try: return bind(event_id,request.snapshot_id)
    except ValueError as error: raise HTTPException(status_code=422, detail=str(error)) from error


@app.post('/api/automation/{event_id}/skip')
def skip_automation(event_id: str, request: AutomationSkip):
    from backend.automation import skip
    skip(event_id,request.reason)
    return {'id':event_id,'status':'skipped'}


@app.post('/api/automation/{event_id}/apply')
def apply_automation(event_id: str, request: AutomationApply):
    from backend.automation import apply
    try: return apply(event_id,request.decision_id)
    except ValueError as error: raise HTTPException(status_code=422, detail=str(error)) from error


class VoiceSessionRequest(BaseModel):
    decision_id: str
    sdp: str


@app.get('/api/voice/decisions')
def voice_decisions():
    from backend.voice import list_decisions
    return list_decisions()


@app.get('/api/voice/decisions/{decision_id}')
def voice_decision_summary(decision_id: str):
    from backend.voice import decision_summary
    try: return decision_summary(decision_id)
    except ValueError as error: raise HTTPException(status_code=404, detail=str(error)) from error


@app.post('/api/voice/session')
def voice_session(request: VoiceSessionRequest):
    from backend.voice import create_session
    try: return create_session(request.decision_id, request.sdp)
    except ValueError as error: raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error: raise HTTPException(status_code=502, detail=str(error)) from error

# Read-only synthetic watchlist coordination.
from backend import watchlist

@app.get('/api/watchlist/evidence')
def watchlist_evidence():
    rows, captures = watchlist.capture_cycle(0)
    return {'rows': rows, 'images': {key: value['image_url'] for key, value in captures.items()}}

@app.post('/api/watchlist/start')
async def start_watchlist(settings: watchlist.Settings, once: bool = True):
    try: return watchlist.start(settings, once)
    except ValueError as error: raise HTTPException(status_code=409, detail=str(error)) from error

@app.get('/api/watchlist/{session_id}')
def get_watchlist(session_id: str):
    session = watchlist.sessions.get(session_id)
    if not session: raise HTTPException(status_code=404, detail='Session not found')
    return session.view()

@app.post('/api/watchlist/{session_id}/stop')
async def stop_watchlist(session_id: str):
    session = watchlist.sessions.get(session_id)
    if not session: raise HTTPException(status_code=404, detail='Session not found')
    session.stopped = True
    session.stop_event.set()
    session.log(len(session.cycles), 'stop requested', 'Stop requested; in-flight work may finish')
    return session.view()

class BacktestRequest(BaseModel):
    start: str
    end: str
    short: int = 2
    long: int = 3
    initial_cash: str = '10000'
    quantity: int = 1

@app.post('/api/backtest')
def run_backtest(request: BacktestRequest):
    from backend.backtest import run
    try: return run(**request.model_dump())
    except (ValueError, OverflowError) as error: raise HTTPException(status_code=422, detail=str(error)) from error

@app.get('/api/backtest/range')
def backtest_range():
    from backend.replay import read_only
    from backend.observations import DEFAULT_DB
    if not DEFAULT_DB.exists(): return {'start': None, 'end': None}
    with closing(read_only(DEFAULT_DB)) as db:
        rows = db.execute('SELECT r.received_at_utc FROM receipts r JOIN observations o ON o.id=r.observation_id WHERE o.ticker=? ORDER BY r.id DESC LIMIT 200', ('AAPL',)).fetchall()
    if not rows: return {'start': None, 'end': None}
    stamps = [parse_utc(row[0]) for row in rows]
    return {'start': min(stamps).isoformat(), 'end': (max(stamps) + timedelta(microseconds=1)).isoformat()}
