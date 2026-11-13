"""Persistent named chart indicator configurations."""
import json
import sqlite3
from contextlib import closing
from backend.indicators import INDICATOR_TYPES
from backend.observations import connect, DEFAULT_DB


def open_store(path=DEFAULT_DB):
    db = connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS indicator_instances (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, type TEXT NOT NULL, settings_json TEXT NOT NULL)')
    db.commit()
    return db


def list_instances(path=DEFAULT_DB):
    with closing(open_store(path)) as db:
        return [{'id': id_, 'name': name, 'type': kind, 'settings': json.loads(settings)}
                for id_, name, kind, settings in db.execute('SELECT id,name,type,settings_json FROM indicator_instances ORDER BY id')]


def create_instance(name, kind, settings, path=DEFAULT_DB):
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Name is required')
    if kind not in INDICATOR_TYPES:
        raise ValueError('Unknown indicator type')
    if not isinstance(settings, dict):
        raise ValueError('Settings must be an object')
    INDICATOR_TYPES[kind]['validate'](settings)
    with closing(open_store(path)) as db:
        try:
            with db:
                cursor = db.execute('INSERT INTO indicator_instances(name,type,settings_json) VALUES (?,?,?)',
                                    (name.strip(), kind, json.dumps(settings, sort_keys=True)))
        except sqlite3.IntegrityError as error:
            raise ValueError('An indicator with that name already exists') from error
        return {'id': cursor.lastrowid, 'name': name.strip(), 'type': kind, 'settings': settings}


def update_instance(instance_id, settings, path=DEFAULT_DB):
    if not isinstance(settings, dict):
        raise ValueError('Settings must be an object')
    with closing(open_store(path)) as db:
        row = db.execute('SELECT name,type FROM indicator_instances WHERE id=?',(instance_id,)).fetchone()
        if not row:
            raise ValueError('Indicator not found')
        INDICATOR_TYPES[row[1]]['validate'](settings)
        with db:
            db.execute('UPDATE indicator_instances SET settings_json=? WHERE id=?',(json.dumps(settings,sort_keys=True),instance_id))
        return {'id':instance_id,'name':row[0],'type':row[1],'settings':settings}
