"""Create a new synthetic replay database for decision smoke checks."""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from backend.observations import connect, record
from backend.paper import initialize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    path = parser.parse_args().database
    if path.exists():
        parser.error('database already exists')
    db = connect(path)
    initialize(db)
    start = datetime(2026, 11, 9, 14, 30, tzinfo=timezone.utc)
    for i, price in enumerate((13, 10, 7, 20)):
        time = start + timedelta(minutes=i, seconds=1)
        record(db, {'id': 'AAPL', 'time': str(int(time.timestamp()*1000)), 'price': price}, time.isoformat())
    late = start + timedelta(minutes=2, seconds=45)
    record(db, {'id': 'AAPL', 'time': str(int(late.timestamp()*1000)), 'price': 19}, (start + timedelta(minutes=3, seconds=2)).isoformat())
    db.close()
    print(f'Synthetic replay fixture: {path.resolve()}')


if __name__ == '__main__':
    main()
