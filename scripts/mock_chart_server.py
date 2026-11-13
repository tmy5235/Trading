"""Synthetic-only local provider double used by the chart smoke test."""
import base64
import hashlib
import os
import sqlite3
import uvicorn
import backend.chart_decisions as charts
import backend.review as review

os.environ['OPENAI_API_KEY'] = 'synthetic-provider-double'


def mock(snapshot, image, key):
    assert key == 'synthetic-provider-double' and snapshot.get('test_data')
    assert charts.decode_png('data:image/png;base64,' + base64.b64encode(image).decode()) == image
    with sqlite3.connect(os.environ['TRADING_BOT_DB_PATH']) as db:
        db.execute('CREATE TABLE IF NOT EXISTS synthetic_provider_calls (snapshot_id TEXT, sha256 TEXT)')
        db.execute('INSERT INTO synthetic_provider_calls VALUES (?,?)',(snapshot['id'],hashlib.sha256(image).hexdigest()))
    return {'answers':[{'type':'choice','name':name,'choice':choice} for name,choice in [('action','wait'),('crossover','up'),('evidence','sufficient')]]}


def mock_reviewer(snapshot, analyst, facts, key, image):
    assert snapshot.get('test_data') and image is not None and analyst['crossover'] == 'up'
    with sqlite3.connect(os.environ['TRADING_BOT_DB_PATH']) as db:
        db.execute('CREATE TABLE IF NOT EXISTS synthetic_review_calls (snapshot_id TEXT, sha256 TEXT)')
        db.execute('INSERT INTO synthetic_review_calls VALUES (?,?)',(snapshot['id'],hashlib.sha256(image).hexdigest()))
    return {'answers':[{'type':'choice','name':'verdict','choice':'accept'}]}


charts.provider_image_request = mock
review.reviewer_request = mock_reviewer
uvicorn.run('backend.main:app',host='127.0.0.1',port=8000,log_level='warning')
