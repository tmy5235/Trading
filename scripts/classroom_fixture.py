"""Isolated synthetic market data and provider for classroom browser verification."""
import base64
import hashlib
import os
import tempfile
from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path

path = Path(os.environ['TRADING_BOT_DB_PATH']).resolve()
if not any(path.is_relative_to(root) for root in (Path(tempfile.gettempdir()).resolve(), Path('/private/tmp').resolve())):
    raise RuntimeError('A temporary SQLite database is required')
os.environ['OPENAI_API_KEY'] = 'offline-fixture-only'
os.environ['TRADING_BOT_DATA_LABEL'] = 'Synthetic classroom test data'

from backend.observations import connect, record
from backend.paper import initialize
from backend.chart_decisions import decode_png
from backend.market_history import save_history
import backend.classroom_decisions as lesson
from backend.chart_agent import chart_agent
from backend.main import app
from tests.test_chart_agent import FixedToolModel

chart_agent.model = FixedToolModel()

with closing(connect(path)) as db:
    initialize(db)
    base = datetime(2026, 11, 9, 14, 30, tzinfo=timezone.utc)
    for index, price in enumerate((13, 10, 7, 13, 19, 7)):
        t = base + timedelta(minutes=index, seconds=2)
        record(db, {'id':'AAPL','time':str(int(t.timestamp()*1000)),'price':price,'day_volume':str(1000+index*10)}, t.isoformat())
    save_history(db, 'AAPL', [
        {'timestamp':'2026-11-09T14:30:00Z','open':13,'high':15,'low':12,'close':14,'volume':1200},
        {'timestamp':'2026-11-09T14:31:00Z','open':14,'high':15,'low':11,'close':12,'volume':850},
    ], '2026-11-09T14:35:00+00:00')
    db.execute('CREATE TABLE classroom_fixture_calls (sha TEXT, metadata TEXT)')
    db.commit()

state = {'choice':'wait', 'failure':False}

def fake_provider(body, key, timeout):
    assert key == 'offline-fixture-only' and timeout == 30
    parts = body['input'][0]['content']
    assert [p['type'] for p in parts] == ['input_image','input_json','input_text']
    metadata = parts[1]['data']
    assert metadata['symbol'] == 'AAPL' and metadata['interval'] == '1m'
    assert not any(k in metadata for k in ('candle','chart_records','prices','volume','account'))
    image = decode_png(parts[0]['image_url'])
    with closing(connect(path)) as db:
        import json
        db.execute('INSERT INTO classroom_fixture_calls VALUES (?,?)',(hashlib.sha256(image).hexdigest(),json.dumps(metadata)))
        db.commit()
    if state['failure']:
        raise OSError('controlled fixture failure')
    return {'answers':[{'type':'choice','name':'chart_signal','choice':state['choice'],
                        'probabilities':[{'value':'short','probability':0.35}, {'value':'wait','probability':0.0}, {'value':'long','probability':0.65}]}]}
lesson.post_decision = fake_provider

@app.post('/__fixture/{choice}')
def set_choice(choice: str):
    if choice == 'failure': state['failure'] = True
    elif choice in ('long','short','wait'): state.update(choice=choice, failure=False)
    else: return {'error':'unknown fixture choice'}
    return {'ok':True}

@app.post('/__fixture/tool/{tool}')
def route_fixture(tool: str):
    if tool == 'rules':
        chart_agent.model = FixedToolModel('lookup_workstation_rules', {'rule_set':'chart_signal'})
    elif tool == 'assessment':
        chart_agent.model = FixedToolModel()
    else:
        raise ValueError('Unknown fixture tool')
    return {'ok':True}

@app.get('/__fixture/state')
def fixture_state():
    with closing(connect(path)) as db:
        calls = db.execute('SELECT sha FROM classroom_fixture_calls ORDER BY rowid').fetchall()
        lessons = db.execute('SELECT png_sha256,status FROM classroom_chart_decisions ORDER BY rowid').fetchall()
        orders = db.execute('SELECT count(*) FROM paper_orders').fetchone()[0]
        fills = db.execute('SELECT count(*) FROM paper_fills').fetchone()[0]
        cash = db.execute('SELECT cash FROM paper_account WHERE id=1').fetchone()[0]
        positions = db.execute('SELECT symbol,shares FROM paper_positions ORDER BY symbol').fetchall()
    return {'calls':calls, 'lessons':lessons, 'orders':orders, 'fills':fills, 'cash':cash, 'positions':positions}
