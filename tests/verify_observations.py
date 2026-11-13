"""Exercise the receiver with synthetic data in a disposable SQLite database."""
import asyncio
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

from backend.yahoo_stream_check import check

MESSAGE = {'id': 'TEST', 'time': '1794234660000', 'price': 101, 'day_volume': '850'}
CHANGED = {**MESSAGE, 'price': 102}


class FakeSocket:
    messages = []

    def __init__(self, verbose=False):
        pass

    async def subscribe(self, symbol):
        assert symbol == 'TEST'

    async def listen(self, callback):
        callback(self.messages.pop(0))
        await asyncio.Event().wait()

    async def close(self):
        pass


def inspect(path):
    with sqlite3.connect(path) as db:
        rows = db.execute('SELECT id, ticker, source_time, message_json FROM observations ORDER BY id').fetchall()
        receipts = db.execute('SELECT id, observation_id, received_at_utc FROM receipts ORDER BY id').fetchall()
    assert len(rows) == 2 and len(receipts) == 3, (rows, receipts)
    assert [json.loads(row[3]) for row in rows] == [MESSAGE, CHANGED]
    assert [row[1] for row in receipts] == [rows[0][0], rows[0][0], rows[1][0]]
    print('TEST DATA observations:', rows)
    print('TEST DATA receipts:', receipts)
    print('PASS: two distinct messages, three ordered receipts; repeat links to first observation')


def main():
    mode, path = sys.argv[1], Path(sys.argv[2])
    if mode == 'write':
        FakeSocket.messages = [MESSAGE, MESSAGE.copy(), CHANGED]
        with patch('backend.yahoo_stream_check.yf.AsyncWebSocket', FakeSocket):
            for _ in range(3):
                with redirect_stdout(io.StringIO()):
                    assert asyncio.run(check('TEST', 2, path)) == 0
    elif mode != 'read':
        raise ValueError('expected write or read')
    inspect(path)


if __name__ == '__main__':
    main()
