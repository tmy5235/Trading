"""Continuous Yahoo receiver; stores observations and upstream connection state."""
import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf
from backend.observations import DEFAULT_DB, connect, record
from backend.paper import initialize as init_paper, drain_live, apply_receipt
from backend.pnl import mark_due_live
from backend.market_history import refresh as refresh_history


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def init_status(db):
    db.execute('CREATE TABLE IF NOT EXISTS feed_status (symbol TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT NOT NULL)')
    db.commit()


def set_status(db, symbol, state):
    with db:
        db.execute('INSERT INTO feed_status VALUES (?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at', (symbol, state, now_utc()))


async def run(symbol, database):
    db = connect(database)
    init_status(db)
    init_paper(db)
    drain_live(db)
    client = None
    async def history_loop():
        while True:
            try:
                count, error = await asyncio.to_thread(refresh_history, symbol, database)
                print(f'Yahoo history: {count} new or revised completed bars' if not error else f'Yahoo history unavailable: {error}', flush=True)
            except Exception as error:
                print(f'Yahoo history unavailable: {type(error).__name__}', flush=True)
            await asyncio.sleep(60)
    history_task = asyncio.create_task(history_loop())
    try:
        while True:
            set_status(db, symbol, 'connecting')
            client = yf.AsyncWebSocket(verbose=False)
            listener = None
            try:
                await client.subscribe(symbol)
                set_status(db, symbol, 'connected')
                print(f'Yahoo connected: {symbol}', flush=True)

                recording_error = None

                def on_message(message):
                    nonlocal recording_error
                    if message.get('id', '').upper() == symbol and 'price' in message and 'time' in message:
                        try:
                            observation_id = record(db, message, now_utc())
                            receipt_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
                            drain_live(db)
                            apply_receipt(db, receipt_id, 'live')
                            print(f'Recorded observation {observation_id}', flush=True)
                        except Exception as error:
                            recording_error = error

                listener = asyncio.create_task(client.listen(on_message))
                # yfinance's public listener may swallow disconnects; the underlying
                # socket's close future is monitored so status reflects upstream loss.
                closed = asyncio.create_task(client._ws.wait_closed())
                try:
                    while not closed.done() and not listener.done():
                        await asyncio.wait([closed, listener], timeout=2, return_when=asyncio.FIRST_COMPLETED)
                        if recording_error is not None:
                            raise recording_error
                        if not closed.done() and not listener.done():
                            mark_due_live(db, symbol, datetime.now(timezone.utc))
                            set_status(db, symbol, 'connected')
                finally:
                    closed.cancel()
                    await asyncio.gather(closed, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                print(f'Yahoo feed error: {type(error).__name__}: {error}', flush=True)
            finally:
                set_status(db, symbol, 'disconnected')
                if listener:
                    listener.cancel()
                    await asyncio.gather(listener, return_exceptions=True)
                await client.close()
            await asyncio.sleep(3)
    finally:
        history_task.cancel()
        await asyncio.gather(history_task, return_exceptions=True)
        set_status(db, symbol, 'disconnected')
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol', default='AAPL', choices=['AAPL'])
    parser.add_argument('--database', type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    try:
        asyncio.run(run(args.symbol, args.database))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
