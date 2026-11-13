"""Validate, store and evaluate a chart capture alongside a frozen snapshot."""
import base64
import binascii
import hashlib
import json
import os
import socket
import ssl
import struct
import urllib.error
import urllib.request
import uuid
import zlib
from contextlib import closing
from datetime import datetime, timezone

from backend.observations import DEFAULT_DB, connect
from backend.decisions import CHOICES, MODEL, evaluate, init, post_decision, snapshot_has_inputs
from backend.timeframes import parse_utc

QUESTIONS = [
    {'type':'choice', 'name':'action', 'instructions':'Which action fits this long-only paper position and any strategy rules supplied? Do not invent strategy rules. Use the configured order size for buy and sell.', 'choices':[
        {'value':'buy','description':'Add the configured number of shares.'}, {'value':'sell','description':'Reduce the position by the configured number of shares.'}, {'value':'hold','description':'Keep an open position.'}, {'value':'wait','description':'Stay flat in cash.'}, {'value':'close','description':'Exit the entire position.'}]},
    {'type':'choice', 'name':'crossover', 'instructions':'At the selected candle and cutoff in the image, what crossover is visible between the blue short SMA and orange long SMA? Choose unreadable if you cannot assess it.', 'choices':[
        {'value':'up','description':'Upward crossover.'},{'value':'down','description':'Downward crossover.'},{'value':'none','description':'No crossover.'},{'value':'unreadable','description':'The chart or crossover cannot be read.'}]},
    {'type':'choice', 'name':'evidence', 'instructions':'Is the visible chart sufficient to assess the signal at the selected candle cutoff?', 'choices':[
        {'value':'sufficient','description':'The chart is sufficient.'},{'value':'insufficient','description':'The chart is insufficient or unreadable.'}]},
]


def init_chart(db):
    init(db)
    db.execute('''CREATE TABLE IF NOT EXISTS chart_decisions (
        decision_id TEXT PRIMARY KEY REFERENCES decisions(id), png BLOB NOT NULL,
        sha256 TEXT NOT NULL, capture_json TEXT NOT NULL, questions_json TEXT NOT NULL,
        answers_json TEXT NOT NULL, comparison TEXT NOT NULL
    )''')
    columns = {row[1] for row in db.execute('PRAGMA table_info(chart_decisions)')}
    if 'input_json' not in columns:
        db.execute('ALTER TABLE chart_decisions ADD COLUMN input_json TEXT')
    db.commit()


def decode_png(data_url):
    """Check PNG structure, checksums and complete decoded scanlines without third-party packages."""
    prefix = 'data:image/png;base64,'
    if not isinstance(data_url, str) or not data_url.startswith(prefix) or len(data_url) > 14_000_000:
        raise ValueError('Unreadable PNG; recapture the chart.')
    try:
        raw = base64.b64decode(data_url[len(prefix):], validate=True)
        if len(raw) > 10_000_000 or not raw.startswith(b'\x89PNG\r\n\x1a\n'): raise ValueError()
        offset=8; chunks=[]; image_data=[]; width=height=channels=None
        while offset < len(raw):
            if offset+12 > len(raw): raise ValueError()
            length=struct.unpack('>I',raw[offset:offset+4])[0]
            if length > len(raw)-offset-12: raise ValueError()
            kind=raw[offset+4:offset+8]; payload=raw[offset+8:offset+8+length]
            crc=struct.unpack('>I',raw[offset+8+length:offset+12+length])[0]
            if zlib.crc32(kind+payload)&0xffffffff != crc: raise ValueError()
            chunks.append(kind); offset+=12+length
            if kind == b'IHDR':
                if len(chunks)!=1 or length!=13: raise ValueError()
                width,height,depth,color,compression,filter_method,interlace=struct.unpack('>IIBBBBB',payload)
                if not (320 <= width <= 4096 and 180 <= height <= 4096 and depth == 8 and color in (2,6) and compression == filter_method == interlace == 0): raise ValueError()
                channels=3 if color==2 else 4
            elif kind == b'IDAT': image_data.append(payload)
            elif kind == b'IEND':
                if length or offset != len(raw): raise ValueError()
                break
        if not width or not image_data or chunks[-1:] != [b'IEND']: raise ValueError()
        row_size=1+width*channels; expected=row_size*height
        decoder=zlib.decompressobj(); decoded=decoder.decompress(b''.join(image_data),expected+1)
        if len(decoded)!=expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail: raise ValueError()
        if any(decoded[i*row_size] > 4 for i in range(height)): raise ValueError()
        return raw
    except (ValueError, binascii.Error, struct.error, zlib.error, OverflowError):
        raise ValueError('Unreadable PNG; recapture the chart.') from None


def validate_capture(snapshot, capture):
    try:
        if not isinstance(capture, dict) or capture['snapshot_id'] != snapshot['id'] or capture['symbol'] != snapshot['symbol'] or capture['timeframe'] != snapshot['timeframe'] or capture['cutoff'] != snapshot['candle_timestamp']:
            raise ValueError()
        lower=parse_utc(capture['range_start']); upper=parse_utc(capture['cutoff'])
        records=snapshot['chart_records']
        selected=next(r for r in records if r['candle']['timestamp']==capture['cutoff'])
        if lower > upper or not selected['ended'] or not any(parse_utc(r['candle']['timestamp']) == lower for r in records): raise ValueError()
    except (KeyError, TypeError, ValueError, StopIteration):
        raise ValueError('Chart capture does not match this snapshot and completed candle; recapture the chart.') from None


def provider_image_request(snapshot, png, api_key, timeout=30):
    body={'model':MODEL, 'input':[{'role':'user','content':[{'type':'input_json','data':snapshot}, {'type':'input_image','image_url':'data:image/png;base64,'+base64.b64encode(png).decode('ascii')}]}], 'questions':QUESTIONS}
    return post_decision(body, api_key, timeout)



def decide_chart(snapshot_id, image_url, capture, *, path=DEFAULT_DB, provider=None, reviewer=None, api_key=None):
    png=decode_png(image_url)
    with closing(connect(path)) as db:
        init_chart(db)
        row=db.execute('SELECT snapshot_json FROM decision_snapshots WHERE id=?',(snapshot_id,)).fetchone()
        if not row: raise ValueError('Snapshot not found; recapture the chart.')
        snapshot=json.loads(row[0]); validate_capture(snapshot,capture)
        lower, upper = parse_utc(capture['range_start']), parse_utc(capture['cutoff'])
        model_snapshot = {**snapshot, 'chart_records': [r for r in snapshot['chart_records'] if lower <= parse_utc(r['candle']['timestamp']) <= upper]}
        # Replay's advancing receipt can be a later candle. It establishes the
        # account checkpoint but must not expose that later price to the model.
        if model_snapshot.get('replay'):
            model_snapshot['replay'] = {k:v for k,v in model_snapshot['replay'].items() if k != 'event'}
        answers={}; comparison='unavailable'; proposal=None; analyst_output=None; key=None
        status='missing_input'; explanation='Snapshot is missing a completed candle, warmed-up indicators, account state, or order size.'
        if snapshot_has_inputs(snapshot):
            key=api_key if api_key is not None else os.environ.get('OPENAI_API_KEY')
            if not key: explanation='OPENAI_API_KEY is not present in the backend environment.'
            else:
                try:
                    result=(provider or provider_image_request)(model_snapshot,png,key)
                    analyst_output=result
                    received=result.get('answers',[]) if isinstance(result,dict) else []
                    for question in QUESTIONS:
                        matches=[a for a in received if isinstance(a,dict) and a.get('type')=='choice' and a.get('name')==question['name']]
                        allowed={c['value'] for c in question['choices']}
                        if len(matches)!=1 or matches[0].get('choice') not in allowed:
                            raise ValueError('missing or invalid answer')
                        answers[question['name']]=matches[0]['choice']
                    proposal=answers['action']; status,explanation=evaluate(snapshot,proposal)
                    expected=snapshot['sma']['signal'] or 'none'; claimed=answers['crossover']
                    if claimed == 'unreadable': comparison='unreadable'; status='unreadable_image'
                    elif claimed != expected: comparison='conflict'; status='conflicting_signal'
                    else: comparison='matches'
                    if answers['evidence']=='insufficient' and status in ('valid','invalid_proposal'):
                        status='insufficient_evidence'
                except (TimeoutError,socket.timeout): status='timeout'; explanation='Provider request timed out.'
                except urllib.error.HTTPError as error:
                    status='provider_error'; explanation=f'Provider returned HTTP {error.code}.'; error.close()
                except urllib.error.URLError as error:
                    status='timeout' if isinstance(error.reason,(TimeoutError,socket.timeout)) else 'provider_error'; explanation='Provider connection or request failed.'
                except (ValueError,TypeError,KeyError): status='malformed_answer'; explanation='Provider did not return one valid answer to each named question.'
                except Exception: status='provider_error'; explanation='Provider request failed.'
        from backend.review import execution_eligible, run_review, save_review, strategy_is_configured
        if proposal in CHOICES and key and len(answers)==3:
            review=run_review(model_snapshot,answers,explanation,status,key,png=png,reviewer=reviewer)
            status=review['final_check']['status']
        else:
            review=run_review(None,{},explanation,status,None)
        review['final_check']['eligible_for_paper_order'] = execution_eligible(snapshot if row else None, status)
        review['final_check']['snapshot_ineligibility_reasons'] = snapshot.get('ineligibility_reasons', []) if row else ['Snapshot not found']
        review['final_check']['strategy_status'] = 'configured' if row and strategy_is_configured(snapshot) else 'not_configured'
        if review['final_check']['strategy_status'] == 'not_configured':
            review['final_check']['explanation'] += ' No executable strategy rules are configured.'
        review['analyst_output'] = analyst_output
        decision_id=str(uuid.uuid4()); sha=hashlib.sha256(png).hexdigest()
        db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)',(decision_id,snapshot_id,datetime.now(timezone.utc).isoformat(),'OpenAI',MODEL,proposal,explanation,status))
        db.execute('INSERT INTO chart_decisions(decision_id,png,sha256,capture_json,questions_json,answers_json,comparison,input_json) VALUES (?,?,?,?,?,?,?,?)',(decision_id,png,sha,json.dumps(capture,sort_keys=True),json.dumps(QUESTIONS),json.dumps(answers,sort_keys=True),comparison,json.dumps(model_snapshot,sort_keys=True)))
        save_review(db,decision_id,analyst_output,review)
        db.commit()
        return {'decision_id':decision_id,'snapshot_id':snapshot_id,'provider':'OpenAI','model':MODEL,'proposal':proposal,'explanation':explanation,'status':status,'eligible_for_paper_order':execution_eligible(snapshot, status), 'questions':QUESTIONS,'answers':answers,'calculated_crossover':(snapshot.get('sma') or {}).get('signal') or 'none','comparison':comparison,'capture':capture,'png_sha256':sha,'image_url':f'/api/decisions/{decision_id}/chart.png','review':review}
