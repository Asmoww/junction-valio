"""Purchasing mandates and the policy engine.

A mandate is the buyer's machine-readable grant of authority to its agent. The gateway evaluates
every offer against it; the agent's prompt is not trusted to do this. Each rule reports pass/fail
so the verdict is fully explainable.
"""
import json
import re
from datetime import date
from pathlib import Path

from . import gates, recalls, request_checks, standards
from .catalog import ALLERGENS, get

MANDATE_DIR = Path(__file__).parent / "mandates"


ORDER = ["mnd_hotel_helsinki", "mnd_school_espoo", "mnd_hospital_ward"]


def mandates():
    from .config import STATE_DIR  # buyers signed from a policy document (onboard.py) live in the state folder
    ms = [json.loads(f.read_text()) for f in [*MANDATE_DIR.glob("*.json"), *(STATE_DIR / "mandates").glob("*.json")]]
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
    # What the product is, by its own name, whatever its allergen data says: "lactose free concentrated butter"
    # declares no milk in the data but is butter. Same check for every path: buyer page, recipe, plan, REST, MCP.
    from .readiness import NON_FOOD  # napkins, cleaners and creams for skin are not food
    named = {} if str(p.get("category") or "").startswith(NON_FOOD) else \
        request_checks.allergen_words(f"{p.get('name', '')} {p.get('name_fi', '')}")
    if hits := sorted(c for c in named if c in banned or (c == "AM" and "ML" in banned)):
        return (f"it is {', '.join(ALLERGENS.get(c, c) for c in hits)} by its name (\u201c{named[hits[0]]}\u201d), "
                "whatever its allergen data says"), "block"
    if not p.get("allergenData") and p.get("ingredients") and not str(p.get("category") or "").startswith(NON_FOOD):
        listed = request_checks.allergen_words(p["ingredients"], ingredients=True)
        if hits := sorted(c for c in listed if c in banned or (c == "AM" and "ML" in banned)):
            return (f"no allergen data, but its ingredients list {', '.join(ALLERGENS.get(c, c) for c in hits)} "
                    f"(\u201c{listed[hits[0]]}\u201d)"), "block"
    if (hits := sorted(set(p.get("mayContain", [])) & banned)) and rules.get("may_contain", "block") != "allow":
        level = "block" if rules.get("may_contain", "block") == "block" else "approve"
        return "may contain " + ", ".join(ALLERGENS.get(a, a) for a in hits), level
    if not p.get("allergenData") and rules.get("no_data", "needs_approval") != "allow":
        return "no allergen data: safety can't be verified", "block" if rules.get("no_data") == "block" else "approve"
    if hits := sorted(set(p.get("undeclared", [])) & banned):
        return "undeclared: " + ", ".join(ALLERGENS.get(a, a) for a in hits), "approve"
    return None


def mandate_signature(mandate):
    """Check the mandate against the buyer-signed credential (standards.py). Demo overrides from the buyer
    page (e.g. the auto-approve slider) are checked against the registered mandate they adjust."""
    registered = mandates().get(mandate["mandate_id"])
    if not registered:
        return False, "mandate not registered"
    return standards.verify(standards.credential(registered), registered)


def evaluate(mandate, offer, context, lookup=None):
    """Return {outcome, approver, rules, ladder}. outcome: auto_approve | needs_approval | blocked.

    Every rule belongs to one gate of the decision ladder (gates.py): 3 Allowed, 4 Proven, 5 Agent's call.
    The first gate with a failed rule decides the action. lookup replaces the catalog for a what-if
    ("would this order go through if Valio added the missing data?", unlock.py); it never changes the catalog."""
    get = lookup or globals()["get"]
    if extra := sorted(set(context.get("request_allergens") or []) - set(mandate["hard_limits"].get("forbidden_allergens", []))):
        # Allergies stated in the request or the caller's request_limits tighten the mandate for this order only.
        mandate = json.loads(json.dumps(mandate))
        mandate["hard_limits"]["forbidden_allergens"] = sorted(set(mandate["hard_limits"].get("forbidden_allergens", [])) | set(extra))
        ap = mandate.setdefault("allergen_policy", {})
        ap["may_contain"] = "block"
        if ap.get("no_data", "allow") == "allow":
            ap["no_data"] = "needs_approval"
    rules = []

    def rule(name, ok, level, detail):
        gate = gates.gate_of(name)
        level = "block" if gate == "allowed" else "approve"  # the gate, not the call site, decides severity
        rules.append({"rule": name, "ok": bool(ok), "level": level if not ok else "pass", "detail": detail,
                      "gate": gate})

    total = offer["total_eur"]
    hard = mandate["hard_limits"]
    auto = mandate["auto_approve"]

    signed_ok, signed = mandate_signature(mandate)
    rule("Mandate valid", date.fromisoformat(mandate["valid_until"]) >= date.today() and signed_ok, "block",
         (f"signed by the buyer ({standards.buyer_did(mandate['mandate_id'])}), " if signed_ok
          else f"buyer's signature fails: {signed}; ") + f"valid until {mandate['valid_until']}")
    if context.get("request_budget") is not None:
        b = context["request_budget"]
        rule("Within the buyer's stated budget", total <= b + 1e-9, "block",
             f"€{total:,.2f} vs the €{b:,.2f} the request allows" + ("" if total <= b + 1e-9 else
             ": nothing ordered; only the requester can raise their own budget"))
    rule("Hard spend limit", total <= hard["max_order_total_eur"], "block",
         f"€{total:,.2f} vs hard limit €{hard['max_order_total_eur']:,}")

    blocked, unverified = [], []
    for line in offer["lines"]:
        p = get(line["productId"])
        problem = allergen_problem(mandate, p)
        if problem:
            (blocked if problem[1] == "block" else unverified).append(f"{p['name']}: {problem[0]}")
    names = ", ".join(ALLERGENS.get(a, a) for a in hard.get("forbidden_allergens", [])) or "-"
    no_data = sum(1 for line in offer["lines"] if not get(line["productId"]).get("allergenData"))
    rule("Forbidden allergens", not blocked, "block", "; ".join(blocked) or (
        f"none of {names} declared or named" + (f"; {no_data} line(s) have no allergen data: see Proven" if no_data and hard.get("forbidden_allergens") else "")))
    if hard.get("forbidden_allergens"):
        rule("Allergens verifiable from data", not unverified, "approve",
             "; ".join(unverified) or "allergen data present for every line")

    # Live recall check against the Finnish Food Authority (recalls.py): a recall stops the order; an unreachable
    # source sends it to a person.
    recalled, unchecked, looked = [], [], 0
    for line in offer["lines"]:
        r = recalls.check(get(line["productId"]))
        if r["status"] == "recalled":
            recalled.append(f"{'TEST RECALL (demo): ' if r.get('test') else ''}{line['name']}: {r['title']} ({r['source']})")
        elif r["status"] == "possible":
            unchecked.append(f"{line['name']}: a recall notice names this brand and product type, check whether this "
                             f"product is included: {r['title']} ({r['source']})")
        elif r["status"] == "unknown":
            unchecked.append(f"{line['name']}: recall list unreachable ({r['source']})")
        looked += r.get("checked", 0)
    rule("Not recalled", not recalled, "block", "; ".join(recalled) or
         f"no current recall notice at the Finnish Food Authority ({looked} checked, {recalls.via()})")
    rule("Recall status checked", not unchecked, "approve", "; ".join(unchecked) or "Food Authority's recall list read live")


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

    assumed = [l.get("name") or l["productId"] for l in offer["lines"] if l.get("piecesAssumed")]
    if assumed:
        rule("Pack contents known", False, "approve",
             "pieces per pack not in the data, so the item count can't be checked: " + ", ".join(assumed))

    unconfirmed = [l.get("name") or l["productId"] for l in offer["lines"] if l.get("stockConfirmed") is False]
    rule("Stock confirmed by Valio", not unconfirmed, "approve",
         "ships from a supplier's warehouse, quantity not confirmed: " + ", ".join(unconfirmed) if unconfirmed
         else "Valio confirms the quantity for every line")

    subs = [l for l in offer["lines"] if l.get("substitution")]
    sub_policy = mandate["substitutions"]
    sub_levels = {l["substitution"] for l in subs}
    # Fail closed: a substitution type the mandate doesn't name is treated as forbidden.
    forbidden_subs = [s for s in sub_levels if sub_policy.get(s, "forbidden") not in ("allowed", "with_approval")]
    rule("Substitutions allowed", not forbidden_subs, "block",
         f"forbidden: {', '.join(forbidden_subs)}" if forbidden_subs else (", ".join(sorted(sub_levels)) or "no substitutes"))
    approve_subs = [s for s in sub_levels if sub_policy.get(s) == "with_approval"]
    rule("Substitutions within preferences", not approve_subs, "approve",
         f"needs sign-off: {', '.join(approve_subs)}" if approve_subs else "none needing sign-off")

    late = context.get("delivery_date") and context.get("deliver_by") and context["delivery_date"] > context["deliver_by"]
    rule("Delivery on time", not late, "approve",
         f"delivers {context.get('delivery_date')} vs needed by {context.get('deliver_by')}")
    early = context.get("delivery_date") and context.get("deliver_by") and \
        (date.fromisoformat(context["deliver_by"][:10]) - date.fromisoformat(context["delivery_date"][:10])).days > 6
    rule("Delivery not too early", not early, "approve",
         f"delivers {context.get('delivery_date')}, a week or more before {context.get('deliver_by')} (fresh goods)"
         if early else "on or near the date")
    rule("Delivery date stated", not context.get("date_assumed"), "approve",
         f"no date in the request: {context.get('deliver_by')} was assumed" if context.get("date_assumed") else "stated in the request")

    asked = context.get("requested_quantity")
    if asked:
        covered = sum(l["units"] * (l.get("perUnit") or 1) for l in offer["lines"])
        # Rounding up to whole packs is fine (one extra pack); beyond that, max +25 %.
        allowance = max(asked * 0.25, max((l.get("perUnit") or 1) for l in offer["lines"]))
        rule("Matches the buyer's request", covered <= asked + allowance, "block",
             f"order covers {covered:,.0f} vs {asked:,.0f} requested (max +25 % or one extra pack)")
        short = covered < asked - max((l.get("perUnit") or 1) for l in offer["lines"]) - 1e-9
        if short and not context.get("partial"):
            rule("Full quantity", False, "approve",
                 f"order covers {covered:,.0f} of the {asked:,.0f} requested: a person decides whether part is enough")

    if asked and context.get("exact_quantity"):
        covered = sum(l["units"] * (l.get("perUnit") or 1) for l in offer["lines"])
        rule("Exact quantity", abs(covered - asked) <= 1e-6 * max(asked, 1), "approve",
             f"the buyer asked for exactly {asked:,.0f}; whole packs cover {covered:,.0f}: a person decides" if abs(covered - asked) > 1e-6 * max(asked, 1)
             else f"exactly {asked:,.0f}, as asked")

    if context.get("partial"):
        rule("Full quantity", False, "approve", "only part of the requested quantity is covered")

    minimum = mandate.get("min_delivery_reliability", 0)
    low = [f"{get(l['productId'])['name']} ({l['reliability']:.0%})" for l in offer["lines"]
           if l.get("reliability") is not None and l["reliability"] < minimum]
    unknown = [get(l["productId"])["name"] for l in offer["lines"] if l.get("reliability") is None]
    rule("Delivery reliability", not low, "approve",
         f"below {minimum:.0%}: " + ", ".join(low) if low else f"every line with a delivery history is ≥ {minimum:.0%}")
    if mandate.get("unknown_reliability", "needs_approval") == "needs_approval":  # unset = fail closed
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

    plan = context.get("plan")
    if plan:
        # The approver already said yes to this spend when signing the week plan (plans.py checked the order fits).
        rule("Auto-approve spend limit", True, "approve",
             f"€{total:,.2f} inside week plan {plan['id']}, signed by {plan['approved_by']} ({plan['role']}): "
             f"€{plan['used_before_eur'] + total:,.2f} of €{plan['max_total_eur']:,.2f} used")
        rule("Agent's own spend in 24 h", True, "approve", "covered by the signed week plan, not the agent's own limit")
    else:
        rule("Auto-approve spend limit", total <= auto["max_order_total_eur"], "approve",
             f"€{total:,.2f} vs auto-approve limit €{auto['max_order_total_eur']:,}"
             + (f" (not covered by a week plan: {context['plan_miss']})" if context.get("plan_miss") else ""))
        # Unset = fail closed: the agent's own spend in 24 h is capped at its per-order limit.
        cap = auto.get("max_auto_eur_per_24h", auto["max_order_total_eur"])
        before = context.get("auto_spend_24h") or 0
        rule("Agent's own spend in 24 h", before + total <= cap, "approve",
             f"€{before:,.2f} already ordered alone in 24 h + €{total:,.2f} vs €{cap:,} "
             "(splitting an order into small ones doesn't avoid approval)")
        if t := context.get("trust"):
            # Earned trust (trust.py): a new or misbehaving agent orders alone only up to part of the buyer's limit.
            rule("Earned trust", total <= t["limit_eur"], "approve",
                 (f"on hold: {t['why']}; a person decides every order" if t["level"] == "On hold" else
                  f"{t['level']} {context.get('channel', '')} agent ({t['clean']} clean orders): may order alone up to "
                  f"€{t['limit_eur']:,.0f} of the buyer's €{auto['max_order_total_eur']:,}"
                  + (f"; {t['next_level']} after {t['next_at']}" if t.get("next_at") else "")))

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
