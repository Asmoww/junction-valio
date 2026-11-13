"""Purchasing mandates and the policy engine.

A mandate is the buyer's machine-readable grant of authority to its agent. The gateway evaluates
every offer against it; the agent's prompt is not trusted to do this. Each rule reports pass/fail
so the verdict is fully explainable.
"""
import json
import re
from datetime import date
from pathlib import Path

from .catalog import ALLERGENS, get

MANDATE_DIR = Path(__file__).parent / "mandates"


ORDER = ["mnd_hotel_helsinki", "mnd_school_espoo", "mnd_hospital_ward"]


def mandates():
    ms = [json.loads(f.read_text()) for f in MANDATE_DIR.glob("*.json")]
    ms.sort(key=lambda m: ORDER.index(m["mandate_id"]) if m["mandate_id"] in ORDER else len(ORDER))
    return {m["mandate_id"]: m for m in ms}


def load(mandate_id, overrides=None):
    m = json.loads(json.dumps(mandates()[mandate_id]))
    for path, value in (overrides or {}).items():  # e.g. {"auto_approve.max_order_total_eur": 2000}
        node = m
        *head, last = path.split(".")
        for k in head:
            node = node[k]
        node[last] = value
    return m


def allergen_problem(mandate, p):
    """Why a product fails the mandate's allergen rules, as (reason, level), or None.

    level "block": the product contains a banned allergen (or may contain one, if the mandate says so).
    level "approve": safety can't be verified from data (no allergen data, or undeclared allergens),
    so a person must check. Missing data never counts as "allergen-free"."""
    banned = set(mandate["hard_limits"].get("forbidden_allergens", []))
    if not banned:
        return None
    rules = mandate.get("allergen_policy", {})
    if hits := sorted(set(p["contains"]) & banned):
        return "contains " + ", ".join(ALLERGENS.get(a, a) for a in hits), "block"
    if (hits := sorted(set(p.get("mayContain", [])) & banned)) and rules.get("may_contain", "block") != "allow":
        level = "block" if rules.get("may_contain", "block") == "block" else "approve"
        return "may contain " + ", ".join(ALLERGENS.get(a, a) for a in hits), level
    if not p.get("allergenData") and rules.get("no_data", "needs_approval") != "allow":
        return "no allergen data: safety can't be verified", "block" if rules.get("no_data") == "block" else "approve"
    if hits := sorted(set(p.get("undeclared", [])) & banned):
        return "undeclared: " + ", ".join(ALLERGENS.get(a, a) for a in hits), "approve"
    return None


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

    blocked, unverified = [], []
    for line in offer["lines"]:
        p = get(line["productId"])
        problem = allergen_problem(mandate, p)
        if problem:
            (blocked if problem[1] == "block" else unverified).append(f"{p['name']}: {problem[0]}")
    names = ", ".join(ALLERGENS.get(a, a) for a in hard.get("forbidden_allergens", [])) or "-"
    rule("Forbidden allergens", not blocked, "block", "; ".join(blocked) or f"none of: {names}")
    if hard.get("forbidden_allergens"):
        rule("Allergens verifiable from data", not unverified, "approve",
             "; ".join(unverified) or "allergen data present for every line")


    missing, name_only = [], []
    # Lactose-free can come from the mandate or from the buyer's request ("lactose-free milk").
    asked_lactose_free = "FREE_FROM_LACTOSE" in (context.get("required_attributes") or [])
    for line in offer["lines"]:
        p = get(line["productId"])
        needed = list(hard.get("required_claims", []))
        if asked_lactose_free and "FREE_FROM_LACTOSE" not in needed:
            needed.append("FREE_FROM_LACTOSE")
        for claim in needed:
            if claim in p["claims"] or (claim == "FREE_FROM_LACTOSE" and p.get("lactose") == 0):
                continue  # backed by the manufacturer's data (claim, or lactose 0 g/100 g)
            if claim == "FREE_FROM_LACTOSE" and re.search(r"lactose[ -]?free|laktoositon", p["name"].lower()):
                name_only.append(f"{p['name']}: lactose free by name only, no lactose value in the data")
            else:
                missing.append(f"{p['name']}: not {claim.lower().replace('_', ' ')}")
    rule("Required claims", not missing, "block", "; ".join(missing) or ", ".join(hard.get("required_claims", [])) or "-")
    rule("Claims verified in product data", not name_only, "approve",
         "; ".join(name_only) or "every required claim is backed by the manufacturer's data")

    unconfirmed = [l.get("name") or l["productId"] for l in offer["lines"] if l.get("stockConfirmed") is False]
    rule("Stock confirmed by Valio", not unconfirmed, "approve",
         "ships from a supplier's warehouse, quantity not confirmed: " + ", ".join(unconfirmed) if unconfirmed
         else "Valio confirms the quantity for every line")

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

    asked = context.get("requested_quantity")
    if asked:
        covered = sum(l["units"] * (l.get("perUnit") or 1) for l in offer["lines"])
        # Rounding up to whole packs is fine (one extra pack); beyond that, max +25 %.
        allowance = max(asked * 0.25, max((l.get("perUnit") or 1) for l in offer["lines"]))
        rule("Matches the buyer's request", covered <= asked + allowance, "block",
             f"order covers {covered:,.0f} vs {asked:,.0f} requested (max +25 % or one extra pack)")

    if context.get("partial"):
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
