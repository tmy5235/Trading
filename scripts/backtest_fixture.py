"""Disposable recorded-observation fixture for the backtest browser test."""
from datetime import datetime, timedelta, timezone
from scripts.classroom_fixture import app, path
from backend.observations import connect, record

db=connect(path)
try:
    base=datetime(2026,11,9,14,30,tzinfo=timezone.utc)
    for minute, price in ((6,1),(7,1),(8,2)):
        source=base+timedelta(minutes=minute,seconds=1)
        record(db,{'id':'AAPL','time':str(int(source.timestamp()*1000)),'price':price,'day_volume':str(1000+minute)},(source+timedelta(seconds=1)).isoformat())
finally: db.close()
