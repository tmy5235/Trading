"""Store decoded market messages and their receipt history in SQLite."""

import argparse
import json
import os
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(os.environ.get('TRADING_BOT_DB_PATH', Path(__file__).resolve().parent / 'data' / 'observations.sqlite3'))


def connect(path=DEFAULT_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute('PRAGMA foreign_keys = ON')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            source_time TEXT,
            message_json TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY,
            observation_id INTEGER NOT NULL REFERENCES observations(id),
            received_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS observations_ticker_time
            ON observations(ticker, source_time);
    ''')
    return db


def record(db, message, received_at_utc):
    """Atomically save a decoded message and every arrival, returning its ID."""
    ticker = message.get('id')
    if not isinstance(ticker, str) or not ticker:
        raise ValueError('message must contain a ticker in id')
    source_time = message.get('time')
    canonical = json.dumps(message, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    with db:
        db.execute(
            'INSERT INTO observations(ticker, source_time, message_json) VALUES (?, ?, ?) '
            'ON CONFLICT(message_json) DO NOTHING',
            (ticker, None if source_time is None else str(source_time), canonical),
        )
        observation_id = db.execute(
            'SELECT id FROM observations WHERE message_json = ?', (canonical,)
        ).fetchone()[0]
        db.execute('INSERT INTO receipts(observation_id, received_at_utc) VALUES (?, ?)',
                   (observation_id, received_at_utc))
    return observation_id


def main():
    parser = argparse.ArgumentParser(description='Initialize the observation database.')
    parser.add_argument('--database', type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    db = connect(args.database)
    db.close()
    print(f'Initialized {args.database.resolve()}')


if __name__ == '__main__':
    main()
