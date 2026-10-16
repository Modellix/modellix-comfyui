"""Atomic records; paid-attempt protection is independent of media cache eviction."""
import asyncio
import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from .errors import ModellixError


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "state.sqlite3"
        try:
            with self.connect() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("CREATE TABLE IF NOT EXISTS records "
                           "(kind TEXT, id TEXT, value TEXT, expires REAL, PRIMARY KEY(kind,id))")
        except sqlite3.DatabaseError:
            raise ModellixError("State database is damaged. Restore its backup before submitting; "
                                "do not delete it to retry a paid request.") from None
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def get(self, kind, key, *, stale=False):
        with self.connect() as db:
            row = db.execute("SELECT value,expires FROM records WHERE kind=? AND id=?",
                             (kind, key)).fetchone()
        if not row or (not stale and row[1] and row[1] < time.time()):
            return None
        try:
            return json.loads(row[0])
        except ValueError:
            if kind in ("attempt", "upload"):
                raise ModellixError("Attempt record damaged; automatic replay is disabled.") from None
            return None

    def put(self, kind, key, value, ttl=0):
        try:
            with self.connect() as db:
                db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?)",
                           (kind, key, json.dumps(value), time.time() + ttl if ttl else 0))
        except (sqlite3.Error, OSError):
            raise ModellixError("Cannot save local task state. Check disk space and permissions. "
                                "Do not automatically replay a paid request.",
                                task_id=value.get("task_id") if isinstance(value, dict) else None) from None

    def identity(self, key):
        # Salt and identifiers stay local. Never return them through public routes/logs.
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO records VALUES ('identity','salt',?,0)",
                       (json.dumps(uuid.uuid4().hex),))
            salt = json.loads(db.execute("SELECT value FROM records WHERE kind='identity' "
                                         "AND id='salt'").fetchone()[0])
        token = hashlib.pbkdf2_hmac("sha256", key.encode(), salt.encode(), 10000).hex()
        return token

    def public_fingerprint(self, context):
        # ComfyUI can serialize IS_CHANGED values into history. Expose a random
        # configuration ID there, never the internal credential-derived identifier.
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO records VALUES ('fingerprint',?,?,0)",
                       (context, json.dumps(uuid.uuid4().hex)))
            return json.loads(db.execute("SELECT value FROM records WHERE kind='fingerprint' "
                                         "AND id=?", (context,)).fetchone()[0])

    @asynccontextmanager
    async def lock(self, key):
        lock = FileLock(str(self.root / (digest(key) + ".lock")))
        while True:
            try:
                lock.acquire(timeout=0)
                break
            except Timeout:
                await asyncio.sleep(0.1)
        try:
            yield
        finally:
            lock.release()

    def records(self, kind):
        with self.connect() as db:
            return [(key, json.loads(value), expires) for key, value, expires in db.execute(
                "SELECT id,value,expires FROM records WHERE kind=?", (kind,))]
