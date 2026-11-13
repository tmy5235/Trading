"""Second model review of a frozen analyst proposal; never executes orders."""
import base64
import hashlib
import json
import socket
import urllib.error
from decimal import Decimal
from backend.decisions import MODEL, post_decision
from backend.strategy import STRATEGY_ID, expected_action

REVIEW_QUESTION = {'type':'choice','name':'verdict',
    'instructions':'Reviewer: Independently assess the analyst proposal against the same frozen snapshot, provided backend facts, account rules and any explicitly supplied strategy. Do not invent strategy rules. Reject unsupported visual signals or insufficient funds. A hold requires a position and wait requires a flat account.',
    'choices':[{'value':'accept','description':'Accept the proposal for further backend checks.'},
               {'value':'reject_insufficient_cash','description':'Reject because cash is insufficient.'},
               {'value':'reject_unsupported_signal','description':'Reject because the claimed signal is unsupported.'},
               {'value':'reject_other','description':'Reject for another account, evidence, or rule issue.'}]}


def strategy_is_configured(snapshot):
    return snapshot.get('strategy_id') == STRATEGY_ID and expected_action(snapshot) is not None


def execution_eligible(snapshot, status):
    return bool(snapshot and snapshot.get('eligible_for_paper_order') and not snapshot.get('test_data')
                and status == 'valid' and strategy_is_configured(snapshot))


def reviewer_request(snapshot, analyst, facts, api_key, png=None, timeout=20):
    content=[{'type':'input_json','data':{'snapshot':snapshot,'analyst_answers':analyst,'backend_facts':facts}}]
    if png is not None:
        content.append({'type':'input_image','image_url':'data:image/png;base64,'+base64.b64encode(png).decode('ascii')})
    return post_decision({'model':MODEL,'input':[{'role':'user','content':content}],'questions':[REVIEW_QUESTION]},api_key,timeout)


def run_review(snapshot, analyst, explanation, analyst_status, api_key, *, png=None, reviewer=None):
    facts={'analyst_validation':analyst_status,'explanation':explanation}
    result={'analyst_model':MODEL,'reviewer_model':MODEL,'reviewer_status':'skipped','verdict':None,
            'reviewer_input':None,'reviewer_output':None,'question':REVIEW_QUESTION,
            'final_check':{'status':analyst_status,'explanation':explanation}}
    if snapshot is None or not api_key:
        return result
    account=snapshot['account']; candle=snapshot['candle']
    cost=Decimal(str(candle['close'])) * snapshot['order_size']
    facts.update({'cash':account['cash'],'shares':account['shares'],'closing_price':candle['close'],
                  'order_size':snapshot['order_size'],'buy_cost_at_close':str(cost),
                  'crossover':snapshot['sma']['signal'] or 'none'})
    result['reviewer_input']={'snapshot':snapshot,'analyst_answers':analyst,'backend_facts':facts,
                              'image_sha256':hashlib.sha256(png).hexdigest() if png is not None else None, 'question':REVIEW_QUESTION}
    try:
        raw=(reviewer or reviewer_request)(snapshot,analyst,facts,api_key,png)
        result['reviewer_output']=raw
        answers=raw.get('answers',[]) if isinstance(raw,dict) else []
        matches=[a for a in answers if isinstance(a,dict) and a.get('type')=='choice' and a.get('name')=='verdict']
        allowed={c['value'] for c in REVIEW_QUESTION['choices']}
        if len(matches)!=1 or not isinstance(matches[0].get('choice'),str) or matches[0]['choice'] not in allowed:
            result['reviewer_status']='malformed_answer'
        else:
            result['verdict']=matches[0]['choice']; result['reviewer_status']='valid'
    except (TimeoutError,socket.timeout): result['reviewer_status']='timeout'
    except urllib.error.HTTPError as error:
        result['reviewer_status']='provider_error'; error.close()
    except urllib.error.URLError as error:
        result['reviewer_status']='timeout' if isinstance(error.reason,(TimeoutError,socket.timeout)) else 'provider_error'
    except Exception: result['reviewer_status']='provider_error'
    # Always preserve a stricter analyst/account/image failure, even when the reviewer accepts.
    if analyst_status == 'valid':
        if result['reviewer_status'] != 'valid': result['final_check']['status']='reviewer_'+result['reviewer_status']
        elif result['verdict'] != 'accept': result['final_check']['status']='reviewer_rejected'
    outcome = result['verdict'] if result['verdict'] is not None else result['reviewer_status']
    result['final_check']['explanation'] = (f"Reviewer: {outcome}. Backend: {explanation} "
        f"Cash {facts['cash']}; configured buy cost at close {facts['buy_cost_at_close']}; "
        f"shares {facts['shares']}; crossover {facts['crossover']}.")
    return result


def save_review(db, decision_id, analyst_output, review):
    encode=lambda value: json.dumps(value,sort_keys=True,allow_nan=False) if value is not None else None
    db.execute('INSERT INTO decision_reviews VALUES (?,?,?,?,?,?,?,?,?)',
               (decision_id,review['analyst_model'],encode(analyst_output),review['reviewer_model'],
                encode(review['reviewer_input']),encode(review['reviewer_output']),review['reviewer_status'],
                review['verdict'],encode(review['final_check'])))
