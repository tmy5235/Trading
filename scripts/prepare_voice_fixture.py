"""Create a separate synthetic decision and skipped paper outcome for voice checks."""
import argparse
import json
from pathlib import Path
from backend.automation import init_automation
from backend.observations import connect


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('database',type=Path)
    path=parser.parse_args().database
    if path.exists(): parser.error('Choose a new database path')
    db=connect(path);init_automation(db)
    snapshot={'symbol':'AAPL','timeframe':'1m','source':'Synthetic voice test data','test_data':True,'candle_timestamp':'2026-11-09T14:34:00Z','decision_time':'2026-11-09T14:35:00+00:00','candle':{'close':100},'sma':{'short':105,'long':100,'signal':'up'},'account':{'cash':'1000','shares':0}}
    db.execute('INSERT INTO decision_snapshots VALUES (?,?,?,?,?)',('voice-snapshot','2026-11-09T14:35:00+00:00',json.dumps(snapshot),'ineligible','Synthetic test'))
    db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)',('voice-decision','voice-snapshot','2026-11-09T14:35:00+00:00','test double','mock-model','buy','Synthetic close 100, cash 1000, no shares.','reviewer_rejected'))
    db.execute('INSERT INTO decision_reviews VALUES (?,?,?,?,?,?,?,?,?)',('voice-decision','mock-model',json.dumps({'answers':[{'name':'action','choice':'buy'}]}),'mock-model','{}',json.dumps({'answers':[{'name':'verdict','choice':'reject_other'}]}),'valid','reject_other',json.dumps({'status':'reviewer_rejected'})))
    db.execute("INSERT INTO automation_events(id,account_id,strategy_id,symbol,timeframe,candle_timestamp,session_id,status,reason,decision_id,created_at,updated_at) VALUES ('voice-event',1,'fixture','AAPL','1m','2026-11-09T14:34:00Z','fixture','skipped','Reviewer rejected the proposal','voice-decision','time','time')")
    db.commit();db.close();print(f'Synthetic voice fixture: {path.resolve()}')


if __name__=='__main__':main()
