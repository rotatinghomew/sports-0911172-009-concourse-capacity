import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


class Store:
    def __init__(self, path=':memory:'):
        self.connection = sqlite3.connect(path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS entities(
                kind TEXT NOT NULL, key TEXT NOT NULL, revision INTEGER NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(kind,key));
            CREATE TABLE IF NOT EXISTS changes(
                sequence INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL,
                revision INTEGER NOT NULL, recorded_at TEXT NOT NULL, payload TEXT NOT NULL);
        """)

    @contextmanager
    def atomic(self):
        nested = self.connection.in_transaction
        if not nested:
            self.connection.execute('BEGIN IMMEDIATE')
        try:
            yield
            if not nested:
                self.connection.commit()
        except BaseException:
            if not nested:
                self.connection.rollback()
            raise

    def get(self, kind, key):
        row = self.connection.execute(
            'SELECT payload FROM entities WHERE kind=? AND key=?', (kind, str(key))).fetchone()
        if row is None:
            raise ValueError(f'不存在的业务记录: {kind}/{key}')
        return json.loads(row['payload'])

    def all(self, kind):
        return [json.loads(row[0]) for row in self.connection.execute(
            'SELECT payload FROM entities WHERE kind=? ORDER BY key', (kind,))]

    def put(self, kind, key, value, create=False):
        key = str(key)
        with self.atomic():
            row = self.connection.execute(
                'SELECT revision FROM entities WHERE kind=? AND key=?', (kind, key)).fetchone()
            if create and row:
                raise ValueError(f'业务编号重复: {kind}/{key}')
            revision = row[0] + 1 if row else 1
            payload = dict(value, id=key, revision=revision)
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
            self.connection.execute(
                'INSERT OR REPLACE INTO entities VALUES (?,?,?,?)', (kind,key,revision,encoded))
            self.connection.execute(
                'INSERT INTO changes(kind,key,revision,recorded_at,payload) VALUES(?,?,?,?,?)',
                (kind,key,revision,datetime.now(timezone.utc).isoformat(),encoded))
            return payload

    def history(self, kind, key):
        return [dict(row) | {'payload': json.loads(row['payload'])}
                for row in self.connection.execute(
                    'SELECT * FROM changes WHERE kind=? AND key=? ORDER BY sequence', (kind,str(key)))]

    def snapshot(self):
        return {kind: self.all(kind) for kind, in self.connection.execute(
            'SELECT DISTINCT kind FROM entities ORDER BY kind').fetchall()}

    def close(self):
        self.connection.close()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def money(value):
    require(isinstance(value, int) and not isinstance(value, bool) and value >= 0, '金额须为非负整数分')
    return value
