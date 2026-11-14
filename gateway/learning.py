"""Learned from decisions: suggestions drawn from what people decided, for the buyer to accept or ignore.

The gateway already records every ask-back, every approval with its comment and every rejection. This module
reads that record and proposes changes:
- a check that people always wave through for the same product ("approved 4 of 4 times") → put it in the
  week plan, or loosen that mandate setting;
- a check that people keep rejecting → make it a hard rule;
- a request the agent had to ask back about, followed by the buyer's clearer request → a phrasing to learn.

The agent never applies a suggestion itself, and nothing here changes a mandate or a plan: a person does that
(the week plan page, or a re-signed mandate). That keeps rule 2: only the requester relaxes a stated need,
and the agent never widens its own authority."""
from collections import defaultdict
from datetime import datetime, timedelta

from . import gates, store

MIN_SAME_WAY = 3      # this many decisions the same way before suggesting anything
CLARIFY_WINDOW = timedelta(minutes=30)

ADVICE = {
    "Auto-approve spend limit": ("Put it in the week plan", "plan"),
    "Agent's own spend in 24 h": ("Put it in the week plan", "plan"),
    "Substitutions within preferences": ("Allow this substitution type in the mandate", "mandate"),
    "Unstated details within tolerance": ("Raise the tolerance for unstated details, or state them in requests", "mandate"),
    "Delivery not too early": ("Accept earlier delivery for this product", "mandate"),
    "Delivery on time": ("Accept the later delivery date for this product", "mandate"),
    "Delivery date stated": ("Set a default delivery day in the buyer profile", "mandate"),
    "Stock confirmed by Valio": ("Accept supplier-warehouse stock for this product", "mandate"),
    "Delivery reliability": ("Accept this product's delivery record", "mandate"),
    "Delivery history known": ("Accept products without a delivery history", "mandate"),
}


def _decisions(mandate_id):
    """One row per human decision on an order: which checks were open, for which products, approve or reject."""
    rows = []
    for o in store.list_orders(5000):
        if o["mandate_id"] != mandate_id:
            continue
        for a in o["record"].get("approvals", []):
            if a.get("decision") not in ("approve", "reject") or a.get("note"):  # a note = refused by the gateway
                continue
            open_rules = [r["rule"] for r in o["policy"].get("rules", []) if not r["ok"] and r.get("gate") != "allowed"]
            rows.append({"order": o["id"], "total": o["total"], "decision": a["decision"], "approver": a.get("approver"),
                         "comment": a.get("comment") or "", "rules": open_rules,
                         "products": [(l["productId"], l.get("name") or l["productId"]) for l in o["record"]["offer"]["lines"]]})
    return rows


def _rule_suggestions(mandate_id):
    groups = defaultdict(list)
    for d in _decisions(mandate_id):
        for rule in d["rules"]:
            for pid, name in d["products"]:
                groups[(rule, pid, name)].append(d)
    out = []
    for (rule, pid, name), ds in groups.items():
        yes = [d for d in ds if d["decision"] == "approve"]
        no = [d for d in ds if d["decision"] == "reject"]
        evidence = [{"order": d["order"], "decision": d["decision"], "approver": d["approver"], "comment": d["comment"],
                     "total": d["total"]} for d in ds[-6:]]
        if len(yes) >= MIN_SAME_WAY and not no:
            action, where = ADVICE.get(rule, ("Review this check for this product", "mandate"))
            if gates.gate_of(rule) == "proven":
                action, where = "Ask Valio to add the missing data, or note it in the buyer profile", "mandate"
            out.append({"kind": "loosen", "rule": rule, "productId": pid, "product": name, "approved": len(yes),
                        "rejected": 0, "max_total": max(d["total"] for d in yes), "action": action, "where": where,
                        "text": f"“{rule}” held {name} for a person {len(yes)} times, and it was approved every time.",
                        "evidence": evidence})
        elif len(no) >= MIN_SAME_WAY - 1 and not yes:
            out.append({"kind": "tighten", "rule": rule, "productId": pid, "product": name, "approved": 0,
                        "rejected": len(no), "action": "Make this a hard rule for this product, or exclude the product",
                        "where": "mandate", "text": f"“{rule}” held {name} for a person {len(no)} times, and it was rejected every time.",
                        "evidence": evidence})
    return sorted(out, key=lambda s: -(s["approved"] + s["rejected"]))


def _clarifications(mandate_id):
    """Requests the agent asked back about, paired with the buyer's next request that went through."""
    runs, order = {}, []
    for e in store.events(limit=5000):
        d = e["data"]
        if e["type"] == "request.received" and d.get("mandate_id") == mandate_id:
            runs[e["ref"]] = {"text": d.get("text", ""), "ts": e["ts"], "action": None, "reason": ""}
            order.append(e["ref"])
        elif e["ref"] in runs and e["type"].startswith("gate."):
            runs[e["ref"]]["action"] = e["type"].split(".")[-1]
            runs[e["ref"]]["reason"] = d.get("reason", "")
        elif e["ref"] in runs and e["type"] == "run.completed":
            runs[e["ref"]]["action"] = runs[e["ref"]]["action"] or "order"
    out = []
    for i, ref in enumerate(order):
        r = runs[ref]
        if r["action"] != "ask_back":
            continue
        for nxt in order[i + 1:]:
            n = runs[nxt]
            if datetime.fromisoformat(n["ts"]) - datetime.fromisoformat(r["ts"]) > CLARIFY_WINDOW:
                break
            if n["action"] in ("order", "person_approves", "person_checks"):
                out.append({"kind": "phrasing", "asked": r["text"][:200], "why": r["reason"][:200], "clarified": n["text"][:200],
                            "action": "Teach the request reader this phrasing (alias or default)", "where": "reader",
                            "text": "The agent asked back, and the buyer's next request went through."})
                break
            if n["action"] == "ask_back":
                continue
            break
    return out[-10:]


def suggestions(mandate_id):
    return {"mandate_id": mandate_id, "rule_suggestions": _rule_suggestions(mandate_id),
            "clarifications": _clarifications(mandate_id),
            "note": "Suggestions only. The agent never applies them: a person changes the plan or re-signs the mandate."}
