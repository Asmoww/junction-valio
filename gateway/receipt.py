"""A receipt anyone can verify: scan the QR on an order and every claim behind it is re-checked, live.

The link carries an HMAC of the order id (gateway key), so receipts can't be enumerated; holding the link is what
lets you see it. The page doesn't trust the stored status: it re-verifies Valio's Ed25519 signature on the offer,
the buyer's signature on the mandate (W3C credential, did:web), that the order matches the signed offer, who
decided and when, and that the hash-chained audit trail is intact.
"""
import base64
import hashlib
import hmac

from . import policy, signing, standards, store
from .approval_links import _key


def token(order_id):
    mac = hmac.new(_key(), f"receipt|{order_id}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:12]).rstrip(b"=").decode()


def check_token(order_id, t):
    return hmac.compare_digest(token(order_id), t or "")


def verify(order_id):
    """Every claim on the receipt, each with ok/why. None if there is no such order."""
    o = store.get_order(order_id)
    if not o:
        return None
    checks = []

    def add(name, ok, why):
        checks.append({"check": name, "ok": bool(ok), "why": why})

    stored = store.get_offer(o["offer_id"]) or {}
    ok, payload = signing.verify(stored.get("jws") or "")
    add("Price signed by Valio", ok, f"Ed25519 offer signature, key {signing.kid()}" if ok else f"signature fails: {payload}")
    if ok:
        same = abs(sum(l["lineTotal"] for l in payload["lines"]) - o["total"]) < 0.01 or o["status"] != "placed"
        add("Order matches the signed offer", payload.get("offer_id") == o["offer_id"] and same,
            f"offer {payload.get('offer_id')}, €{payload.get('total_eur', 0):,.2f} signed")
    m = policy.mandates().get(o["mandate_id"])
    if m:
        vok, vc = standards.verify(standards.credential(m), m)
        add("Rules signed by the buyer", vok, f"W3C credential from {standards.buyer_did(o['mandate_id'])}" if vok else str(vc))
    approvals = (o.get("record") or {}).get("approvals") or []
    if approvals:
        a = approvals[-1]
        add("Decided by a named person", a.get("approver") in {x["name"] for x in (m or {}).get("approvers", [])},
            f"{a.get('approver')} {'approved' if a.get('decision') == 'approve' else 'rejected'} at {str(a.get('at'))[:16].replace('T', ' ')} UTC")
    elif o["status"] == "placed":
        add("Within the agent's own authority", o["policy"].get("outcome") == "auto_approve",
            "no person needed: every check passed" + (" inside a signed week plan" if (o["record"].get("context") or {}).get("plan") else ""))
    chain = store.verify_chain()
    events = store.events(o["id"]) + store.events(o["offer_id"])
    add("Audit trail intact", chain["valid"], f"{len(events)} events for this order in a hash chain of {chain['events']}")
    offer = payload if ok else {}
    return {"order": {"id": o["id"], "status": o["status"], "total": o["total"], "created": o["created"],
                      "buyer": (m or {}).get("buyer", o["mandate_id"]).replace(" (demo)", ""),
                      "lines": [{"name": l["name"], "units": l["units"], "unit": l.get("salesUnit"), "price": l["unitPrice"],
                                 "total": l["lineTotal"]} for l in offer.get("lines", [])],
                      "deliver_by": offer.get("deliver_by")},
            "checks": checks, "all_ok": all(c["ok"] for c in checks),
            "jws": stored.get("jws"), "kid": signing.kid()}
