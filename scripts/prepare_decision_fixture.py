"""Create a separate synthetic decision snapshot for a provider check."""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.decisions import create_snapshot
from backend.observations import connect, record
from backend.paper import initialize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path, help='new SQLite file; must not already exist')
    args = parser.parse_args()
    if args.database.exists():
        parser.error('database already exists; choose a new path')
    db = connect(args.database)
    try:
        initialize(db)
        # Synthetic account existed before the synthetic market event.
        db.execute("UPDATE paper_account_metadata SET created_at='2026-11-09T14:00:00+00:00'")
        db.commit()
        start = datetime(2026,11,9,14,30,tzinfo=timezone.utc)
        for index, price in enumerate((13,10,7)):
            t = start + timedelta(minutes=index,seconds=2)
            record(db, {'id':'AAPL','time':str(int(t.timestamp()*1000)),'price':price}, (t+timedelta(seconds=1)).isoformat())
    finally:
        db.close()
    os.environ['TRADING_BOT_DATA_IS_TEST'] = '1'
    os.environ['TRADING_BOT_DATA_LABEL'] = 'Synthetic decision fixture'
    snapshot = create_snapshot('AAPL','1m','2026-11-09T14:32:00Z',path=args.database)
    print(json.dumps({'database':str(args.database.resolve()),'snapshot_id':snapshot['id'],
                      'source':snapshot['source'],'eligible_for_paper_order':snapshot['eligible_for_paper_order']}))


if __name__ == '__main__':
    main()
