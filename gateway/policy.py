"""Purchasing mandates and the policy engine.

A mandate is the buyer's machine-readable grant of authority to its agent. The gateway evaluates
every offer against it; the agent's prompt is not trusted to do this. Each rule reports pass/fail
so the verdict is fully explainable.
"""
import json
import re
from datetime import date
from pathlib import Path

from . import gates
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
    """Return {outcome, approver, rules, ladder}. outcome: auto_approve | needs_approval | blocked.

    Every rule belongs to one gate of the decision ladder (gates.py): 3 Allowed, 4 Proven, 5 Agent's call.
    The first gate with a failed rule decides the action."""
    rules = []

    def rule(name, ok, level, detail):
        gate = gates.gate_of(name)
        level = "block" if gate == "allowed" else "approve"  # the gate, not the call site, decides severity
        rules.append({"rule": name, "ok": bool(ok), "level": level if not ok else "pass", "detail": detail,
                      "gate": gate})

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

    minimum = mandate.get("min_delivery_reliability", 0)
    low = [f"{get(l['productId'])['name']} ({l['reliability']:.0%})" for l in offer["lines"]
           if l.get("reliability") is not None and l["reliability"] < minimum]
    unknown = [get(l["productId"])["name"] for l in offer["lines"] if l.get("reliability") is None]
    rule("Delivery reliability", not low, "approve",
         f"below {minimum:.0%}: " + ", ".join(low) if low else f"every line with a delivery history is ≥ {minimum:.0%}")
    if mandate.get("unknown_reliability", "allow") == "needs_approval":
        rule("Delivery history known", not unknown, "approve",
             "no delivery history for: " + ", ".join(unknown) if unknown else "every line has a delivery history")
    elif unknown:  # allowed by this mandate, but said out loud rather than shown as a pass on reliability
        rules.append({"rule": "Delivery history known", "ok": True, "level": "pass", "gate": "proven",
                      "detail": "no history for " + ", ".join(unknown) + " (this mandate allows it)"})

    # Details the buyer didn't state (fat %, fresh vs long-life) that change the price: the agent may pick
    # alone only if the difference is within the mandate's tolerance; otherwise the approver chooses.
    tolerance = mandate.get("auto_approve", {}).get("max_unstated_detail_spread_eur", 25)
    wide = [g for g in context.get("spec_gaps") or [] if (g.get("cost_spread_eur") or 0) > tolerance]
    rule("Unstated details within tolerance", not wide, "approve",
         "; ".join(f"{g['field']} not stated, moves the order by €{g['cost_spread_eur']:,.0f}" for g in wide)
         + f" (tolerance €{tolerance:,.0f})" if wide else
         (f"unstated details move the order by at most €{tolerance:,.0f}" if context.get("spec_gaps") else "nothing left unstated"))

    rule("Auto-approve spend limit", total <= auto["max_order_total_eur"], "approve",
         f"€{total:,.2f} vs auto-approve limit €{auto['max_order_total_eur']:,}")
    if auto.get("max_auto_eur_per_24h") is not None:
        before = context.get("auto_spend_24h") or 0
        rule("Agent's own spend in 24 h", before + total <= auto["max_auto_eur_per_24h"], "approve",
             f"€{before:,.2f} already ordered alone in 24 h + €{total:,.2f} vs €{auto['max_auto_eur_per_24h']:,} "
             "(splitting an order into small ones doesn't avoid approval)")

    verdict = gates.from_rules(rules)
    if verdict["action"] == "stop":
        outcome, approver = "blocked", None
    elif verdict["action"] in ("person_checks", "person_approves"):
        outcome = "needs_approval"
        approver = next((a for a in mandate["approvers"] if total <= a["up_to_eur"]), mandate["approvers"][-1])
    else:
        outcome, approver = "auto_approve", None
    return {"outcome": outcome, "approver": approver, "rules": rules, "mandate_id": mandate["mandate_id"],
            "ladder": verdict}
