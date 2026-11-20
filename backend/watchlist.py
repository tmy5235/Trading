"""Read-only, frozen synthetic watchlist and bounded SDK coordination."""
import asyncio
import base64
import json
import os
import struct
import zlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from agents import Agent, ModelSettings, RunConfig, RunHooks, Runner, function_tool
from pydantic import BaseModel, Field
from backend import classroom_decisions as classroom

SCANNER_PROMPT = ('Call read_watchlist. Select ready rows whose second close rises strictly more than the threshold. '
                  'Rank by largest rise, then ticker alphabetically. Return at most three, with capture ID and observation. '
                  'Never select stale or missing evidence. Return an empty list if none qualify.')
DECISION_PROMPT = ('Call read_decision_rules first, then assess_chart exactly once. Use only these tools; '
                   'the chart is already bound to the selected capture.')

class Candidate(BaseModel):
    ticker: str
    snapshot_id: str
    observation: str

class Shortlist(BaseModel):
    candidates: list[Candidate]

class Settings(BaseModel):
    threshold: float = Field(default=0, ge=0, le=1000, allow_inf_nan=False)
    interval: float = Field(default=5, ge=1, le=300)
    max_cycles: int = Field(default=3, ge=1, le=3)
    call_limit: int = Field(default=20, ge=1, le=100)

# Complete synthetic minutes, deliberately separate from actual market recordings.
BARS = {
 'AAPL': [(354.80,354.65),(354.65,355.00),(355.00,355.10),(355.10,355.08)],
 'MSFT': [(410.10,410.00),(410.00,409.80),(409.80,409.85),(409.85,409.80)],
 'NVDA': [(140.00,140.00),(140.00,140.00),(140.00,140.00),(140.00,140.00)],
 'AMD': [(150.10,150.00),(150.00,150.20),(150.20,150.30),(150.30,150.40)],
}
START = datetime(2026, 11, 16, 14, 40, tzinfo=timezone.utc)

def iso(t): return t.strftime('%Y-%m-%dT%H:%M:%SZ')

def chart_png(bars):
    """Deterministic 320x180 RGB PNG of the exact frozen candles."""
    w, h = 320, 180
    pixels = bytearray([17,28,43] * w * h)
    def rect(x1,y1,x2,y2,color):
        for y in range(max(0,y1), min(h,y2)):
            for x in range(max(0,x1),min(w,x2)):
                i=(y*w+x)*3; pixels[i:i+3]=bytes(color)
    low=min(min(b['open'],b['close']) for b in bars)-0.1
    high=max(max(b['open'],b['close']) for b in bars)+0.1
    for y in (30,70,110,150): rect(0,y,w,y+1,(45,60,76))
    for i,b in enumerate(bars):
        x=95+i*120
        a=round(150-(b['open']-low)/(high-low)*120); c=round(150-(b['close']-low)/(high-low)*120)
        color=(39,208,162) if b['close']>b['open'] else (240,107,122) if b['close']<b['open'] else (180,180,180)
        rect(x-1,min(a,c)-5,x+2,max(a,c)+6,color); rect(x-12,min(a,c),x+13,max(a,c)+1,color)
    raw=b''.join(b'\0'+pixels[y*w*3:(y+1)*w*3] for y in range(h))
    def chunk(kind,data): return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')

def capture_cycle(index):
    cutoff=START+timedelta(minutes=2+index); rows=[]; captures={}
    for ticker in ('AAPL','MSFT','NVDA','AMD','SPY'):
        sid=f'{ticker}-{cutoff:%Y%m%dT%H%MZ}'
        status='missing' if ticker=='SPY' else 'stale' if ticker=='AMD' else 'ready'
        bars=[{'time':iso(START+timedelta(minutes=j)), 'open':BARS[ticker][j][0], 'close':BARS[ticker][j][1]} for j in (index,index+1)] if ticker in BARS else []
        row={'ticker':ticker,'snapshot_id':sid if bars else None,'cutoff':iso(cutoff),'status':status,'bars':bars,'source':'synthetic test data'}
        rows.append(row)
        if bars:
            image='data:image/png;base64,'+base64.b64encode(chart_png(bars)).decode('ascii')
            captures[sid]={'row':row,'image_url':image}
    return rows,captures

def validate_candidates(shortlist, rows, threshold):
    eligible=[r for r in rows if r['status']=='ready' and Decimal(str(r['bars'][1]['close']))-Decimal(str(r['bars'][0]['close']))>Decimal(str(threshold))]
    eligible.sort(key=lambda r: (-(Decimal(str(r['bars'][1]['close']))-Decimal(str(r['bars'][0]['close']))),r['ticker']))
    allowed={r['snapshot_id']:r for r in eligible[:3]}; seen=set(); accepted=[]; errors=[]
    for i,c in enumerate(shortlist.candidates):
        row=allowed.get(c.snapshot_id)
        if c.snapshot_id in seen: errors.append(f'Duplicate capture {c.snapshot_id} rejected'); continue
        seen.add(c.snapshot_id)
        if i>=3 or not row or row['ticker']!=c.ticker: errors.append(f'Invalid selection {c.ticker} / {c.snapshot_id} rejected'); continue
        accepted.append(c)
    order={r['snapshot_id']:i for i,r in enumerate(eligible[:3])}
    accepted.sort(key=lambda candidate: order[candidate.snapshot_id])
    return accepted,errors

class Stopped(Exception): pass
class LimitReached(Exception): pass

class Session:
    def __init__(self, settings, once):
        self.id=str(uuid4()); self.settings=settings; self.once=once; self.stopped=False; self.running=True
        self.calls=0; self.cycles=[]; self.events=[]; self.task=None; self.stop_event=asyncio.Event()
    def log(self,cycle,kind,message, **extra):
        self.events.append({'cycle':cycle,'kind':kind,'message':message,**extra})
    def reserve(self):
        if self.stopped: raise Stopped()
        if self.calls>=self.settings.call_limit: raise LimitReached()
        self.calls+=1
    def view(self):
        return {'id':self.id,'running':self.running,'stopped':self.stopped,'calls':self.calls,'settings':self.settings.model_dump(), 'cycles':self.cycles,'events':self.events}

class BudgetHooks(RunHooks):
    def __init__(self,session): self.session=session
    async def on_llm_start(self, context, agent, system_prompt, input_items): self.session.reserve()

sessions={}
active=None

async def cycle_run(s, index):
    rows,captures=capture_cycle(index); n=index+1
    cycle={'number':n,'cutoff':rows[0]['cutoff'],'rows':rows,'assessments':{},'candidates':[],'images':{key: value['image_url'] for key,value in captures.items()},'status':'running'}
    s.cycles.append(cycle); evidence_read=False
    @function_tool(failure_error_function=None)
    async def read_watchlist() -> str:
        """Read frozen synthetic bars, capture IDs, status, and threshold for this cycle."""
        nonlocal evidence_read
        if s.stopped: raise Stopped()
        evidence_read=True; s.log(n,'scanner evidence','read_watchlist returned frozen observations', rows=rows)
        return json.dumps({'rows':rows,'threshold':s.settings.threshold})
    scanner=Agent(name='Scanner',instructions=SCANNER_PROMPT,tools=[read_watchlist],output_type=Shortlist)
    result=await Runner.run(scanner,'Read the watchlist and return the shortlist.',max_turns=4,hooks=BudgetHooks(s),run_config=RunConfig(tracing_disabled=True))
    if s.stopped: raise Stopped()
    if not evidence_read: raise ValueError('Scanner did not read evidence')
    candidates,errors=validate_candidates(result.final_output,rows,s.settings.threshold)
    for error in errors: s.log(n,'rejected',error)
    cycle['candidates']=[c.model_dump() for c in candidates]
    if not candidates: s.log(n,'empty scan','No accepted candidates; no chart assessments.')
    for candidate in candidates:
        if s.stopped: raise Stopped()
        snap=captures[candidate.snapshot_id]; row=snap['row']; sid=candidate.snapshot_id
        s.log(n,'selection',candidate.observation,snapshot_id=sid,ticker=candidate.ticker)
        rules_read=False; assessment=None; claimed=False
        @function_tool(failure_error_function=None)
        def read_decision_rules() -> str:
            """Read shared chart signal rules before assessment."""
            nonlocal rules_read
            if s.stopped: raise Stopped()
            rules_read=True; s.log(n,'rules','Shared chart signal rules returned',snapshot_id=sid)
            return json.dumps(classroom.QUESTION)
        @function_tool(failure_error_function=None)
        async def assess_chart() -> str:
            """Assess the bound saved chart using the Decisions API."""
            nonlocal assessment, claimed
            if not rules_read: raise ValueError('Rules must be read first')
            if claimed:
                s.log(n,'duplicate','Repeated assessment tool call rejected',snapshot_id=sid)
                return json.dumps({'status':'duplicate'})
            s.reserve()
            claimed=True
            now=datetime.now(timezone.utc)
            context={'symbol':row['ticker'],'interval':'1m','mode':'replay','capture_time':iso(now),
                     'source_time':row['cutoff'],'replay_receipt_time':row['cutoff'],
                     'last_completed_candle':row['bars'][-1]['time'],'forming_candle':row['cutoff'],'feed_status':'unknown'}
            assessment=await asyncio.to_thread(classroom.decide,snap['image_url'],context, persist=False, data_source='Synthetic test data')
            if s.stopped:
                s.log(n,'late','Assessment returned after Stop; excluded from completed results',snapshot_id=sid)
                return json.dumps({'status':'late'})
            if (assessment.get('context',{}).get('symbol')!=row['ticker'] or
                assessment.get('context',{}).get('last_completed_candle')!=row['bars'][-1]['time'] or
                assessment.get('context',{}).get('forming_candle')!=row['cutoff']):
                raise ValueError('Assessment context mismatch')
            if assessment['status']!='ok' or not assessment.get('answer') or assessment['answer'].get('choice') not in ('long','short','wait'):
                s.log(n,'failed',f"Assessment failed: {assessment['status']}",snapshot_id=sid)
            else:
                cycle['assessments'][sid]=assessment
                s.log(n,'assessment','Completed assessment',snapshot_id=sid,result=assessment)
            return json.dumps(assessment)
        agent=Agent(name='Decision',instructions=DECISION_PROMPT,tools=[read_decision_rules,assess_chart],model_settings=ModelSettings(parallel_tool_calls=False),tool_use_behavior={'stop_at_tool_names':['assess_chart']})
        await Runner.run(agent,'Read the rules and assess the saved chart.',max_turns=5,hooks=BudgetHooks(s),run_config=RunConfig(tracing_disabled=True))
        if assessment is None: s.log(n,'failed','Decision agent did not assess the chart',snapshot_id=sid)
    cycle['status']='completed'

async def run_session(s):
    try:
        if not os.environ.get('OPENAI_API_KEY'): raise ValueError('Configure OPENAI_API_KEY in the backend environment')
        for index in range(1 if s.once else s.settings.max_cycles):
            if s.stopped: raise Stopped()
            await cycle_run(s,index)
            if not s.once and index+1<s.settings.max_cycles:
                try: await asyncio.wait_for(s.stop_event.wait(), timeout=s.settings.interval)
                except asyncio.TimeoutError: pass
    except Stopped: s.log(len(s.cycles),'stopped','Stopped; no further work will start')
    except LimitReached: s.log(len(s.cycles),'limit','Session model-call limit reached')
    except Exception as error: s.log(len(s.cycles),'failed',f'{type(error).__name__}: agent or tool failed')
    finally:
        if s.cycles and s.cycles[-1]['status']=='running': s.cycles[-1]['status']='stopped' if s.stopped else 'incomplete'
        s.running=False

def start(settings,once):
    global active
    if active and active.running: raise ValueError('A watchlist session is already running')
    s=Session(settings,once); sessions[s.id]=s; active=s; s.task=asyncio.create_task(run_session(s)); return s.view()
