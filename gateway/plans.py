"""Week plans: the chef approves the week's purchases once, and the agent orders inside them alone.

A plan names products, the most units of each for the week, a delivery window and a total budget. An approver
from the buyer's mandate (with authority for the whole budget) signs it once. After that, an order the agent
places is "inside the plan" only if every line is a planned product, the plan's units and budget still cover it
(counting earlier orders), and delivery falls in the window. Inside the plan, the per-order and 24 h autonomy
limits are lifted: the approver already said yes to this spend. Nothing else is: hard rules (gate 3), fact checks
(gate 4) and every other gate-5 rule still run on each order, so only exceptions reach a person.

The plan is checked when the order is committed, under the commit lock, against the signed plan in the store.
An agent can't claim a plan; the gateway finds it."""
import math
import re
import uuid
from datetime import date

from . import catalog, policy, signing, store

MAX_PLAN_LINES = 30
MAX_PLAN_DAYS = 14


def _day(s):
    if not isinstance(s, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def create(mandate_id, lines, start, end, max_total_eur, created_by="buyer", note=""):
    """A draft plan, or (None, why). Hard rules are checked now, so the chef is never asked to sign something
    the mandate forbids anyway."""
    if mandate_id not in policy.mandates():
        return None, "unknown mandate"
    mandate = policy.load(mandate_id)
    d0, d1 = _day(start), _day(end)
    if not d0 or not d1 or d1 < d0:
        return None, "the plan needs a start and end date (YYYY-MM-DD), end on or after start"
    if (d1 - d0).days > MAX_PLAN_DAYS:
        return None, f"a plan covers at most {MAX_PLAN_DAYS} days"
    try:
        budget = float(max_total_eur)
    except (TypeError, ValueError):
        return None, "max_total_eur must be a number"
    if not math.isfinite(budget) or budget <= 0:
        return None, "max_total_eur must be a positive number"
    if budget > mandate["hard_limits"]["max_order_total_eur"] * 7:
        return None, "the plan budget is above a week of the mandate's hard limit"
    if not isinstance(lines, list) or not lines or len(lines) > MAX_PLAN_LINES:
        return None, f"a plan needs 1 to {MAX_PLAN_LINES} products"
    out, seen = [], set()
    for l in lines:
        if not isinstance(l, dict):
            return None, "each plan line needs productId and units"
        pid = str(l.get("productId", ""))
        p = catalog.get(pid)
        if not p:
            return None, f"unknown product {pid!r}"
        if pid in seen:
            return None, f"{pid} appears twice: combine it into one line"
        seen.add(pid)
        units = l.get("units")
        if isinstance(units, bool) or not isinstance(units, (int, float)) or not math.isfinite(units) or units <= 0:
            return None, f"{pid}: units must be a positive number"
        if problem := policy.allergen_problem(mandate, p):
            if problem[1] == "block":
                return None, f"{p['name']}: {problem[0]}: the mandate forbids it, so it can't be planned"
        missing = [c for c in mandate["hard_limits"].get("required_claims", [])
                   if c not in p["claims"] and not (c == "FREE_FROM_LACTOSE" and p.get("lactose") == 0)]
        if missing:
            return None, f"{p['name']}: not {', '.join(m.lower().replace('_', ' ') for m in missing)} (required by the mandate)"
        out.append({"productId": pid, "name": p["name"], "units": units, "salesUnit": p["salesUnit"],
                    "note": str(l.get("note") or "")[:120]})
    plan = {"id": "pln_" + uuid.uuid4().hex[:10], "kind": "week_plan", "mandate_id": mandate_id, "buyer": mandate["buyer"],
            "start": start, "end": end, "max_total_eur": round(budget, 2), "lines": out, "status": "draft",
            "created_by": str(created_by)[:80], "note": str(note)[:300], "created": store.now()}
    store.save_plan(plan)
    store.log(plan["id"], f"human:{created_by}", "plan.drafted",
              {"mandate_id": mandate_id, "products": len(out), "max_total_eur": plan["max_total_eur"]})
    return plan, None


def approve(plan_id, approver, comment=""):
    """The approver signs the plan once. Returns (plan, None) or (None, why)."""
    with store.commit_lock():
        plan = store.get_plan(plan_id)
        if not plan or plan["status"] != "draft":
            return None, "plan is not waiting for approval"
        mandate = policy.load(plan["mandate_id"])
        who = next((a for a in mandate["approvers"] if a["name"] == approver), None)
        if not who:
            store.log(plan_id, f"unknown:{approver}", "security.unknown_approver", {"plan": True})
            return None, f"{approver or 'this person'} is not an approver in the buyer's mandate"
        if plan["max_total_eur"] > who["up_to_eur"]:
            return None, f"{approver} can approve up to €{who['up_to_eur']:,}; this plan's budget is €{plan['max_total_eur']:,.2f}"
        signed = {k: plan[k] for k in ("id", "kind", "mandate_id", "start", "end", "max_total_eur", "lines")}
        signed.update(approved_by=approver, role=who["role"], approved_at=store.now())
        plan.update(status="approved", approved_by=approver, role=who["role"], approved_at=signed["approved_at"],
                    comment=str(comment)[:300], jws=signing.sign(signed))
        store.save_plan(plan)
        store.log(plan_id, f"human:{approver}", "plan.approved", {"max_total_eur": plan["max_total_eur"], "comment": comment})
        return plan, None


def revoke(plan_id, by, reason=""):
    with store.commit_lock():
        plan = store.get_plan(plan_id)
        if not plan or plan["status"] not in ("draft", "approved"):
            return None, "plan is not active"
        plan.update(status="revoked", revoked_by=by, revoked_at=store.now(), revoke_reason=str(reason)[:200])
        store.save_plan(plan)
        store.log(plan_id, f"human:{by}", "plan.revoked", {"reason": reason})
        return plan, None


def usage(plan):
    """What earlier orders (placed or waiting) already took from the plan."""
    used_units, used_eur = {}, 0.0
    for o in store.orders_in_plan(plan["id"]):
        if o["status"] not in ("placed", "pending_approval"):
            continue
        used_eur += o["total"]
        for l in o["record"]["offer"]["lines"]:
            used_units[l["productId"]] = used_units.get(l["productId"], 0) + l["units"]
    return used_units, round(used_eur, 2)


def view(plan):
    """A plan with what's used and what's left, for pages and agents."""
    units, eur = usage(plan)
    return dict({k: v for k, v in plan.items() if k != "jws"}, signed=bool(plan.get("jws")),
                used_eur=eur, left_eur=round(plan["max_total_eur"] - eur, 2),
                lines=[dict(l, used=units.get(l["productId"], 0), left=l["units"] - units.get(l["productId"], 0))
                       for l in plan["lines"]])


def covering(mandate_id, offer):
    """(plan summary, None) if an approved plan covers this offer in full, else (None, why not or None).

    Call under the commit lock: it reads what earlier orders used."""
    why = None
    for plan in store.plans_for(mandate_id, "approved"):
        ok, payload = signing.verify(plan.get("jws") or "")
        if not ok or payload.get("id") != plan["id"] or payload.get("lines") != plan["lines"] \
                or payload.get("max_total_eur") != plan["max_total_eur"]:
            store.log(plan["id"], "gateway", "security.plan_signature_invalid", {})
            continue
        planned = {l["productId"]: l for l in plan["lines"]}
        dates = [offer["deliver_by"]] + [l.get("deliveryDate") or offer["deliver_by"] for l in offer["lines"]]
        if not all(plan["start"] <= d[:10] <= plan["end"] for d in dates):
            why = why or f"delivery is outside plan {plan['id']} ({plan['start']} to {plan['end']})"
            continue
        if missing := [l["productId"] for l in offer["lines"] if l["productId"] not in planned]:
            why = why or f"{', '.join(missing)} not in plan {plan['id']}"
            continue
        units, eur = usage(plan)
        over = [l["productId"] for l in offer["lines"] if units.get(l["productId"], 0) + l["units"] > planned[l["productId"]]["units"]]
        if over:
            why = f"plan {plan['id']} has fewer units left than this order for {', '.join(over)}"
            continue
        if eur + offer["total_eur"] > plan["max_total_eur"] + 1e-9:
            why = f"plan {plan['id']} has €{plan['max_total_eur'] - eur:,.2f} left, this order is €{offer['total_eur']:,.2f}"
            continue
        return {"id": plan["id"], "approved_by": plan["approved_by"], "role": plan["role"], "start": plan["start"],
                "end": plan["end"], "max_total_eur": plan["max_total_eur"], "used_before_eur": eur}, None
    return None, why
