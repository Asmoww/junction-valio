"""Signed, single-use approval links: a person approves an order from their own phone without signing in.

The link names one order and one approver from the buyer's mandate, expires in 30 minutes, and is signed with a
key only the gateway holds (HMAC-SHA256). It is single-use by nature: once the order is decided it is no longer
pending. The approval itself goes through agent.decide, so every rule of a desktop approval still applies
(authority for the total, the gate-4 fact-check note, price re-check after the lock expires)."""
import base64
import hashlib
import hmac
import secrets
import time

from .config import STATE_DIR

TTL_SECONDS = 30 * 60
_KEY_FILE = STATE_DIR / "approval_link_key"


def _key():
    if not _KEY_FILE.exists():
        _KEY_FILE.write_bytes(secrets.token_bytes(32))
        _KEY_FILE.chmod(0o600)
    return _KEY_FILE.read_bytes()


def _sig(order_id, approver, exp):
    mac = hmac.new(_key(), f"{order_id}|{approver}|{exp}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def make(order_id, approver):
    """Query parameters for an approval link: a (approver), e (expiry, unix seconds), t (signature)."""
    exp = int(time.time()) + TTL_SECONDS
    return {"a": approver, "e": str(exp), "t": _sig(order_id, approver, exp)}


def check(order_id, a, e, t):
    """(ok, reason). Valid signature for this order and approver, and not expired."""
    try:
        exp = int(e)
    except (TypeError, ValueError):
        return False, "this approval link is malformed"
    if not t or not hmac.compare_digest(_sig(order_id, a or "", exp), t):
        return False, "this approval link is not valid"
    if exp < time.time():
        return False, "this approval link has expired: ask for a new one"
    return True, ""
