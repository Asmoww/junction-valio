"""Trust that's earned: an agent orders alone only as far as its record shows it can be trusted.

Each buyer's agents are tracked per channel: the built-in agent, and outside agents (REST/MCP). A new channel may
order alone up to a quarter of the buyer's per-order limit; clean orders raise that, up to the buyer's own cap.
An outside agent that tries something a hard rule forbids is put on hold for 24 hours: everything it orders then
needs a person. Never loosens the buyer's limits; a signed week plan (approved by a person) still applies.
"""
from datetime import datetime, timedelta, timezone

from . import store

LEVELS = [(20, "Trusted", 1.0), (5, "Proven", 0.5), (0, "New", 0.25)]  # (clean orders needed, name, share of cap)
HOLD_HOURS = 24


def channel(context):
    return "built-in" if (context or {}).get("run_id") else "external"


def status(mandate, chan):
    """{level, share, clean, incidents, limit_eur, next_at} for one buyer's agent channel."""
    cap = (mandate.get("auto_approve") or {}).get("max_order_total_eur", 0)
    since = (datetime.now(timezone.utc) - timedelta(hours=HOLD_HOURS)).isoformat()
    clean = incidents = 0
    for o in store.list_orders(5000):
        if o["mandate_id"] != mandate["mandate_id"] or channel((o.get("record") or {}).get("context")) != chan:
            continue
        if o["status"] == "placed":
            clean += 1
        elif (chan == "external" and o["status"] == "blocked" and o["created"] >= since
              and (o["policy"].get("ladder") or {}).get("decided_by") == "allowed"):
            incidents += 1
    if incidents:
        return {"level": "On hold", "share": 0.0, "clean": clean, "incidents": incidents, "limit_eur": 0.0,
                "why": f"{incidents} attempt(s) a hard rule forbids in the last {HOLD_HOURS} h"}
    need, name, share = next(x for x in LEVELS if clean >= x[0])
    nxt = next((x for x in reversed(LEVELS) if x[0] > clean), None)
    return {"level": name, "share": share, "clean": clean, "incidents": 0, "limit_eur": round(cap * share, 2),
            "next_at": nxt[0] if nxt else None, "next_level": nxt[1] if nxt else None}


def everyone(mandates):
    return [dict(status(m, ch), mandate_id=m["mandate_id"], buyer=m["buyer"].replace(" (demo)", ""), channel=ch,
                 cap_eur=(m.get("auto_approve") or {}).get("max_order_total_eur", 0))
            for m in mandates for ch in ("built-in", "external")]
