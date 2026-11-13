"""Receive at most one Yahoo Finance update; no credentials are required."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from backend.observations import DEFAULT_DB, connect, record

import yfinance as yf


async def check(symbol: str, timeout: float, database: Path = DEFAULT_DB) -> int:
    db = connect(database)
    client = yf.AsyncWebSocket(verbose=False)
    opened = False
    loop = asyncio.get_running_loop()
    received = loop.create_future()
    listener = None

    def on_message(message):
        if not received.done() and message.get('id', '').upper() == symbol:
            receipt = datetime.now(timezone.utc).isoformat()
            try:
                observation_id = record(db, message, receipt)
                received.set_result((message, receipt, observation_id))
            except Exception as error:
                received.set_exception(error)

    async def subscribe_and_wait():
        nonlocal opened, listener
        await client.subscribe(symbol)
        opened = True
        print(f'Connection opened; subscription sent for {symbol}.', flush=True)
        listener = asyncio.create_task(client.listen(on_message))
        return await received

    try:
        message, receipt, observation_id = await asyncio.wait_for(subscribe_and_wait(), timeout)
        volume_fields = {key: value for key, value in message.items() if 'volume' in key.lower()}
        raw_time = message.get('time')
        try:
            source_utc = datetime.fromtimestamp(int(raw_time) / 1000, timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            source_utc = None
        print(json.dumps({
            'observation_id': observation_id,
            'ticker': message.get('id'),
            'price': message.get('price'),
            'source_time_raw': raw_time,
            'source_time_utc_if_epoch_milliseconds': source_utc,
            'source_time_units': 'apparently Unix epoch milliseconds; inferred, not documented',
            'backend_receipt_time_utc': receipt,
            'volume_fields_raw': volume_fields,
            'volume_semantics': 'day_volume appears to be cumulative daily shares for equities; not verified by provider documentation',
        }, indent=2))
        return 0
    except TimeoutError:
        print(f'Timed out after {timeout:g} seconds. Connection opened: {opened}. Receipt unverified.')
        return 1
    except Exception as error:
        print(f'Connection opened: {opened}. Receipt unverified. {type(error).__name__}: {error}')
        return 1
    finally:
        if listener is not None:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        try:
            await client.close()
        finally:
            db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('symbol', nargs='?', default='AAPL')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--database', type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    return asyncio.run(check(args.symbol.upper(), args.timeout, args.database))


if __name__ == '__main__':
    raise SystemExit(main())
