"""SQLite storage: offers, orders and a hash-chained audit log."""
import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone

from .config import DB_PATH

_db = sqlite3.connect(DB_PATH, check_same_thread=False)
_db.row_factory = sqlite3.Row
_lock = threading.Lock()
_db.executescript("""
CREATE TABLE IF NOT EXISTS offers (id TEXT PRIMARY KEY, mandate_id TEXT, payload TEXT, jws TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, offer_id TEXT, idempotency_key TEXT UNIQUE,
  status TEXT, total REAL, mandate_id TEXT, policy TEXT, record TEXT, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT, ts TEXT, actor TEXT,
  type TEXT, data TEXT, prev_hash TEXT, hash TEXT);
""")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(prev, ts, ref, actor, type_, data):
    return hashlib.sha256(json.dumps([prev, ts, ref, actor, type_, data], sort_keys=True).encode()).hexdigest()


def log(ref, actor, type_, data):
    """Append an event to the audit chain. Each event's hash covers the previous one."""
    with _lock:
        row = _db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row["hash"] if row else "genesis"
        ts = now()
        data_s = json.dumps(data, sort_keys=True, default=str)
        h = _hash(prev, ts, ref, actor, type_, data_s)
        _db.execute("INSERT INTO events (ref, ts, actor, type, data, prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
                    (ref, ts, actor, type_, data_s, prev, h))
        _db.commit()
        return h


def events(ref=None, limit=200):
    q = "SELECT * FROM events" + (" WHERE ref = ?" if ref else "") + " ORDER BY seq DESC LIMIT ?"
    rows = _db.execute(q, (ref, limit) if ref else (limit,)).fetchall()
    return [dict(r, data=json.loads(r["data"])) for r in reversed(rows)]


def verify_chain():
    prev = "genesis"
    rows = _db.execute("SELECT * FROM events ORDER BY seq").fetchall()
    for r in rows:
        if r["prev_hash"] != prev or _hash(prev, r["ts"], r["ref"], r["actor"], r["type"], r["data"]) != r["hash"]:
            return {"valid": False, "broken_at_seq": r["seq"], "events": len(rows)}
        prev = r["hash"]
    return {"valid": True, "events": len(rows), "head": prev}


def save_offer(offer_id, mandate_id, payload, jws):
    with _lock:
        _db.execute("INSERT INTO offers VALUES (?,?,?,?,?)", (offer_id, mandate_id, json.dumps(payload), jws, now()))
        _db.commit()


def get_offer(offer_id):
    r = _db.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
    return dict(r, payload=json.loads(r["payload"])) if r else None


def order_by_offer(offer_id):
    r = _db.execute("SELECT * FROM orders WHERE offer_id = ? ORDER BY created LIMIT 1", (offer_id,)).fetchone()
    return _order(r)


def order_by_key(key):
    r = _db.execute("SELECT * FROM orders WHERE idempotency_key = ?", (key,)).fetchone()
    return _order(r)


def get_order(order_id):
    return _order(_db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone())


def list_orders(limit=50):
    return [_order(r) for r in _db.execute("SELECT * FROM orders ORDER BY created DESC LIMIT ?", (limit,))]


def _order(r):
    if not r:
        return None
    return dict(r, policy=json.loads(r["policy"]), record=json.loads(r["record"] or "{}"))


def save_order(o):
    with _lock:
        _db.execute("INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (o["id"], o["offer_id"], o["idempotency_key"], o["status"], o["total"], o["mandate_id"],
                     json.dumps(o["policy"]), json.dumps(o["record"], default=str), o.get("created") or now(), now()))
        _db.commit()
