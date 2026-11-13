"""Purchasing mandates and the policy engine.

A mandate is the buyer's machine-readable grant of authority to its agent. The gateway evaluates
every offer against it; the agent's prompt is not trusted to do this. Each rule reports pass/fail
so the verdict is fully explainable.
"""
import json
from datetime import date
from pathlib import Path

from .catalog import ALLERGENS, get

MANDATE_DIR = Path(__file__).parent / "mandates"


def mandates():
    return {m["mandate_id"]: m for m in (json.loads(f.read_text()) for f in sorted(MANDATE_DIR.glob("*.json")))}


def load(mandate_id, overrides=None):
    m = json.loads(json.dumps(mandates()[mandate_id]))
    for path, value in (overrides or {}).items():  # e.g. {"auto_approve.max_order_total_eur": 2000}
        node = m
        *head, last = path.split(".")
        for k in head:
            node = node[k]
        node[last] = value
    return m


def evaluate(mandate, offer, context):
    """Return {outcome, approver, rules}. outcome: auto_approve | needs_approval | blocked."""
    rules = []

    def rule(name, ok, level, detail):
        rules.append({"rule": name, "ok": bool(ok), "level": level if not ok else "pass", "detail": detail})

    total = offer["total_eur"]
    hard = mandate["hard_limits"]
    auto = mandate["auto_approve"]

    rule("Mandate valid", date.fromisoformat(mandate["valid_until"]) >= date.today(), "block",
         f"valid until {mandate['valid_until']}")
    rule("Hard spend limit", total <= hard["max_order_total_eur"], "block",
         f"€{total:,.2f} vs hard limit €{hard['max_order_total_eur']:,}")

    bad = []
    for line in offer["lines"]:
        p = get(line["productId"])
        hits = sorted(set(p["contains"]) & set(hard.get("forbidden_allergens", [])))
        if hits:
            bad.append(f"{p['name']}: {', '.join(ALLERGENS.get(a, a) for a in hits)}")
    rule("Forbidden allergens", not bad, "block",
         "; ".join(bad) if bad else f"none of: {', '.join(ALLERGENS.get(a, a) for a in hard.get('forbidden_allergens', [])) or '-'}")

    missing = []
    for line in offer["lines"]:
        p = get(line["productId"])
        for claim in hard.get("required_claims", []):
            if claim not in p["claims"] and not (claim == "FREE_FROM_LACTOSE" and "lactose free" in p["name"].lower()):
                missing.append(f"{p['name']}: not {claim.lower().replace('_', ' ')}")
    rule("Required claims", not missing, "block", "; ".join(missing) or ", ".join(hard.get("required_claims", [])) or "-")

    subs = [l for l in offer["lines"] if l.get("substitution")]
    sub_policy = mandate["substitutions"]
    sub_levels = {l["substitution"] for l in subs}
    forbidden_subs = [s for s in sub_levels if sub_policy.get(s) == "forbidden"]
    rule("Substitutions allowed", not forbidden_subs, "block",
         f"forbidden: {', '.join(forbidden_subs)}" if forbidden_subs else (", ".join(sorted(sub_levels)) or "no substitutes"))
    approve_subs = [s for s in sub_levels if sub_policy.get(s) == "with_approval"]
    rule("Substitutions within preferences", not approve_subs, "approve",
         f"needs sign-off: {', '.join(approve_subs)}" if approve_subs else "none needing sign-off")

    late = context.get("delivery_date") and context.get("deliver_by") and context["delivery_date"] > context["deliver_by"]
    rule("Delivery on time", not late, "approve",
         f"delivers {context.get('delivery_date')} vs needed by {context.get('deliver_by')}")

    if len(offer["lines"]) < context.get("lines_requested", 1) or context.get("partial"):
        rule("Full quantity", False, "approve", "only part of the requested quantity is covered")

    low = [f"{get(l['productId'])['name']} ({l['reliability']:.0%})" for l in offer["lines"]
           if l.get("reliability") is not None and l["reliability"] < mandate.get("min_delivery_reliability", 0)]
    rule("Delivery reliability", not low, "approve",
         "below " + f"{mandate.get('min_delivery_reliability', 0):.0%}: " + ", ".join(low) if low
         else f"all lines ≥ {mandate.get('min_delivery_reliability', 0):.0%} or no history")

    rule("Auto-approve spend limit", total <= auto["max_order_total_eur"], "approve",
         f"€{total:,.2f} vs auto-approve limit €{auto['max_order_total_eur']:,}")

    if any(r["level"] == "block" for r in rules):
        outcome, approver = "blocked", None
    elif any(r["level"] == "approve" for r in rules):
        outcome = "needs_approval"
        approver = next((a for a in mandate["approvers"] if total <= a["up_to_eur"]), mandate["approvers"][-1])
    else:
        outcome, approver = "auto_approve", None
    return {"outcome": outcome, "approver": approver, "rules": rules, "mandate_id": mandate["mandate_id"]}
