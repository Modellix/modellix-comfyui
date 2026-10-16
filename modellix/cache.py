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


def protected_record(kind, value):
    """A valid JSON null/partial record is not proof that no paid request exists."""
    if kind not in ("attempt", "upload", "intent"):
        return
    valid = isinstance(value, dict)
    if valid and kind == "intent":
        valid = all(isinstance(value.get(field), str) and value[field]
                    for field in ("schema_hash", "attempt"))
    elif valid and kind == "attempt":
        state = value.get("state")
        valid = state in ("prepared", "submitting", "outcome_unknown", "submitted", "waiting",
                          "downloading", "ready", "failed", "canceled")
        if state in ("submitted", "waiting", "downloading", "ready", "failed", "canceled"):
            valid = valid and isinstance(value.get("task_id"), str) and bool(value["task_id"])
    elif valid:
        valid = value.get("state") in ("submitting", "outcome_unknown", "ready")
        if "nonce" in value:
            valid = valid and isinstance(value["nonce"], str) and bool(value["nonce"])
        if value.get("state") == "ready":
            data = value.get("data")
            valid = (valid and isinstance(data, dict) and isinstance(data.get("url"), str)
                     and bool(data["url"]) and bool(data.get("file_id")))
    if not valid:
        raise ModellixError("Attempt record damaged; automatic replay is disabled.")


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
        if not row:
            return None
        try:
            value = json.loads(row[0])
        except ValueError:
            if kind in ("attempt", "upload", "intent"):
                raise ModellixError("Attempt record damaged; automatic replay is disabled.") from None
            return None
        protected_record(kind, value)
        permanent = kind in ("attempt", "intent") or (
            kind == "upload" and value["state"] != "ready")
        if not permanent and not stale and row[1] and row[1] < time.time():
            return None
        return value

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
    async def lock(self, key, budget=None):
        lock = FileLock(str(self.root / (digest(key) + ".lock")))
        while True:
            if budget:
                budget.remaining()
            try:
                lock.acquire(timeout=0)
                break
            except Timeout:
                if budget:
                    await budget.sleep(0.1)
                else:
                    await asyncio.sleep(0.1)
        try:
            yield
        finally:
            lock.release()

    def records(self, kind):
        with self.connect() as db:
            return [(key, json.loads(value), expires) for key, value, expires in db.execute(
                "SELECT id,value,expires FROM records WHERE kind=?", (kind,))]
