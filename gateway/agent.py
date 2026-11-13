"""Buyer-side purchasing agent and the gateway's offer/order logic.

The agent turns a request into a structured intent, looks up a short list of candidates, asks the
Valio APIs for price and availability only for those, builds good-better-best options, takes a
signed offer for the best one and submits the order. The gateway's policy engine decides whether the
order goes through, needs a human, or is blocked.
"""
import math
import re
import uuid
from datetime import date, datetime, timedelta, timezone

from . import ai, catalog, policy, security, signing, store
from .config import OFFER_TTL_MINUTES
from .valio import ValioClient

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
ATTRS = {
    r"lactose[- ]?free|laktoositon": "FREE_FROM_LACTOSE",
    r"gluten[- ]?free|gluteeniton": "FREE_FROM_GLUTEN",
    r"\bvegan\b": "VEGAN",
    r"organic|luomu": "ORGANIC",
    r"finnish|kotimai": "FINNISH",
}
UNITS = {"l": "L", "litre": "L", "litres": "L", "liter": "L", "liters": "L", "kg": "KG", "kilos": "KG",
         "kilograms": "KG", "pcs": "PCS", "pieces": "PCS", "units": "PCS"}
STOP = {"i", "need", "we", "want", "of", "by", "for", "a", "an", "the", "find", "option", "that", "meets",
        "our", "requirements", "based", "on", "price", "and", "availability", "please", "order", "buy",
        "deliver", "delivered", "delivery", "to", "until", "before", "next", "week", "tomorrow", "today"}


def parse_request(text, today=None):
    """Rule-based intent parser: quantity, unit, product words, attributes, deadline."""
    today = today or date.today()
    t = text.lower()
    q = re.search(r"(\d[\d,. ]*)\s*(litres?|liters?|l|kg|kilos|kilograms|pcs|pieces|units)\b", t)
    quantity = float(q.group(1).replace(" ", "").replace(",", "")) if q else None
    unit = UNITS.get(q.group(2), "PCS") if q else "PCS"
    attrs = [code for pat, code in ATTRS.items() if re.search(pat, t)]
    deliver_by = None
    if m := re.search(r"\d{4}-\d{2}-\d{2}", t):
        deliver_by = date.fromisoformat(m.group())
    elif "tomorrow" in t:
        deliver_by = today + timedelta(days=1)
    else:
        for i, d in enumerate(WEEKDAYS):
            if re.search(rf"\b{d}\b", t):
                deliver_by = today + timedelta(days=(i - today.weekday()) % 7 or 7)
    stripped = t
    if q:
        # The product is what follows the quantity, up to a clause like "by Monday" or "for the school".
        stripped = re.split(r"\b(?:by|for|to|before|until|from|delivered|so|and|that|which)\b|[,.;!?]", t[q.end():])[0]
    for pat in ATTRS:
        stripped = re.sub(pat, " ", stripped)
    words = [w for w in re.findall(r"[a-zåäö]+", stripped) if w not in STOP and w not in WEEKDAYS and w not in UNITS]
    return {"product": " ".join(words[:3]) or "milk", "quantity": quantity, "unit": unit,
            "attributes": attrs, "deliver_by": (deliver_by or today + timedelta(days=3)).isoformat(), "raw": text}


def _per_unit(p, price, unit):
    """How much of the requested unit one sales unit holds (e.g. 20 L per carton)."""
    if unit == "PCS":
        return 1.0
    cp, comp = (price.get("catalogPrice") or {}).get("value"), (price.get("comparisonPrice") or {}).get("value")
    if cp and comp and (price.get("comparisonUnit") or "").upper() == unit:
        x = cp / comp
        return round(x) if abs(x - round(x)) < 0.15 * max(1, x) else round(x, 2)
    return None


def _assess(p, price, avail, intent, now_s):
    """Turn raw API answers into one candidate row with a clear status."""
    row = {"product": catalog.describe(p), "productId": p["id"], "name": p["name"], "status": "ok", "issues": []}
    if not price or price.get("error") or not price.get("catalogPrice"):
        row["status"] = "no_price"
        row["issues"].append("no price for this customer")
        return row
    unit_price = price["catalogPrice"]["value"]
    per_unit = _per_unit(p, price, intent["unit"])
    if not per_unit:
        row["status"] = "unit_mismatch"
        row["issues"].append(f"can't convert {p['salesUnit']} to {intent['unit']}")
        return row
    need = math.ceil(intent["quantity"] / per_unit) if intent["quantity"] else 1
    row.update(unit_price=unit_price, sales_unit=p["salesUnit"], per_unit=per_unit, units_needed=need,
               price_per_req_unit=round(unit_price / per_unit, 3), price_valid_to=price["catalogPrice"]["validTo"][:10],
               reliability=p["reliability"], carbon=p["carbonKgPerKg"])
    a = avail or {}
    lines = [s for s in a.get("scheduleLines", []) if not s.get("salesBlock") and s.get("availableDate")
             and s["availableDate"] <= intent["deliver_by"]]
    available = sum(s["availableAmount"] for s in lines)
    row["closing_time"] = a.get("closingTime")
    row["logistic_model"] = a.get("logisticModel")
    if a.get("salesBlock"):
        row["status"] = "blocked"
        row["issues"].append("sales block")
    elif a.get("closingTime") and a["closingTime"] < now_s:
        row["status"] = "deadline_passed"
        row["issues"].append(f"order deadline for this date passed ({a['closingTime'][:16].replace('T', ' ')})")
    elif not lines:
        row["status"] = "unavailable"
        row["issues"].append("not available by the requested date")
    elif available < need:
        row["status"] = "partial"
        row["issues"].append(f"only {available:g} {p['salesUnit']} available, need {need}")
    if a.get("logisticModel") == "Terminal" and row["status"] == "ok":
        row["issues"].append("delivered from supplier's warehouse: quantity not confirmed by Valio")
        row["unconfirmed_stock"] = True
    row["available_units"] = available
    row["delivery_date"] = min((s["availableDate"] for s in lines), default=None)
    if row["status"] in ("ok", "partial"):
        units = need if row["status"] == "ok" else int(available)
        row["units"] = units
        row["total"] = round(units * unit_price, 2)
    return row


def _score(rows, weights):
    if not rows:
        return
    pmin = min(r["price_per_req_unit"] for r in rows)
    cvals = [r["carbon"] for r in rows if r["carbon"]]
    cmin = min(cvals) if cvals else None
    for r in rows:
        price_s = pmin / r["price_per_req_unit"]
        rel_s = r["reliability"] if r["reliability"] is not None else 0.85  # unknown history: mild penalty
        carb_s = (cmin / r["carbon"]) if (cmin and r["carbon"]) else 0.8
        r["score"] = round(weights["price"] * price_s + weights["reliability"] * rel_s + weights["carbon"] * carb_s, 3)
        r["score_parts"] = {"price": round(price_s, 2), "reliability": round(rel_s, 2), "carbon": round(carb_s, 2)}


def _line(r, substitution=None):
    return {"productId": r["productId"], "name": r["name"], "units": r["units"], "salesUnit": r["sales_unit"],
            "unitPrice": r["unit_price"], "lineTotal": r["total"], "perUnit": r["per_unit"],
            "deliveryDate": r["delivery_date"], "reliability": r["reliability"], "substitution": substitution}


def build_options(rows, intent):
    """Good-better-best: recommended, cheapest, most reliable; or a split order if no single product covers it."""
    full = sorted([r for r in rows if r["status"] == "ok"], key=lambda r: -r["score"])
    options = []
    if full:
        picks = [("Recommended", full[0], "best overall score under your mandate's weights")]
        cheapest = min(full, key=lambda r: r["total"])
        reliable = max(full, key=lambda r: (r["reliability"] or 0, -r["total"]))
        if cheapest is not full[0]:
            picks.append(("Cheapest", cheapest, "lowest total price"))
        if reliable not in (full[0], cheapest):
            picks.append(("Most reliable", reliable, "best delivery track record"))
        for r in full[1:]:
            if len(picks) >= 3:
                break
            if all(r is not x for _, x, _ in picks):
                picks.append(("Alternative", r, f"next best score ({r['name'].split(' ')[0]})"))
        for label, r, why in picks:
            options.append({"label": label, "why": why, "lines": [_line(r, r.get("substitution"))],
                            "total_eur": r["total"], "covers": r["units"] * r["per_unit"], "partial": False})
    else:
        # Split across products, best score first, until the quantity is covered.
        usable = sorted([r for r in rows if r["status"] in ("ok", "partial")], key=lambda r: -r["score"])
        remaining, lines = intent["quantity"] or 0, []
        for r in usable:
            if remaining <= 0:
                break
            units = min(r.get("units", 0), math.ceil(remaining / r["per_unit"]))
            if units <= 0:
                continue
            rr = dict(r, units=units, total=round(units * r["unit_price"], 2))
            lines.append(_line(rr, r.get("substitution")))
            remaining -= units * r["per_unit"]
        if lines:
            covers = sum(l["units"] * l["perUnit"] for l in lines)
            options.append({"label": "Split order", "why": "no single product covers the full quantity in time",
                            "lines": lines, "total_eur": round(sum(l["lineTotal"] for l in lines), 2),
                            "covers": covers, "partial": covers < (intent["quantity"] or 0)})
    return options


def create_offer(mandate, option, intent):
    """Lock list prices and availability for a short time and sign the offer."""
    offer_id = "ofr_" + uuid.uuid4().hex[:12]
    exp = datetime.now(timezone.utc) + timedelta(minutes=OFFER_TTL_MINUTES)
    payload = {
        "offer_id": offer_id, "issuer": "valio-aimo-trade-gateway (demo)", "customer": mandate["customer"],
        "mandate_id": mandate["mandate_id"], "currency": "EUR", "price_basis": "catalog price, fixed (not negotiated)",
        "lines": [{k: l.get(k) for k in ("productId", "name", "units", "salesUnit", "perUnit", "unitPrice", "lineTotal",
                                         "deliveryDate", "reliability", "substitution")} for l in option["lines"]],
        "total_eur": option["total_eur"], "deliver_by": intent["deliver_by"],
        "issued_at": store.now(), "expires_at": exp.isoformat(timespec="seconds"),
    }
    jws = signing.sign(payload)
    store.save_offer(offer_id, mandate["mandate_id"], payload, jws)
    store.log(offer_id, "gateway", "offer.signed", {"total_eur": payload["total_eur"], "lines": len(payload["lines"]),
                                                     "expires_at": payload["expires_at"]})
    return payload, jws


def place_order(offer_jws, idempotency_key, mandate, context, client=None, actor="agent"):
    """Gateway side of an order: verify the signed offer, re-check prices, apply the mandate."""
    if existing := store.order_by_key(idempotency_key):
        return existing, "duplicate request: returning the original order (idempotent)"
    ok, payload = signing.verify(offer_jws)
    if not ok:
        return None, payload
    if payload["expires_at"] < store.now():
        return None, "offer expired: request a new one"
    client = client or ValioClient()
    fresh = client.prices(mandate["customer"], payload["deliver_by"], [l["productId"] for l in payload["lines"]])
    drift = [l["productId"] for l in payload["lines"]
             if abs((fresh.get(l["productId"], {}).get("catalogPrice") or {}).get("value", -1) - l["unitPrice"]) > 0.005]
    if context.get("simulate_price_change"):
        drift = [payload["lines"][0]["productId"]]
    if drift:
        store.log(payload["offer_id"], "gateway", "order.rejected", {"reason": "price changed", "products": drift})
        return None, f"price changed since the offer for {', '.join(drift)}: request a new offer"
    verdict = policy.evaluate(mandate, payload, context)
    status = {"auto_approve": "placed", "needs_approval": "pending_approval", "blocked": "blocked"}[verdict["outcome"]]
    order = {"id": "ord_" + uuid.uuid4().hex[:10], "offer_id": payload["offer_id"], "idempotency_key": idempotency_key,
             "status": status, "total": payload["total_eur"], "mandate_id": mandate["mandate_id"], "policy": verdict,
             "record": {"offer": payload, "context": context, "agent": mandate["agent"]}}
    store.save_order(order)
    store.log(order["id"], actor, "order.submitted", {"offer_id": payload["offer_id"], "total_eur": payload["total_eur"]})
    store.log(order["id"], "gateway", f"policy.{verdict['outcome']}",
              {"failed_rules": [r["rule"] for r in verdict["rules"] if not r["ok"]],
               "approver": (verdict["approver"] or {}).get("role")})
    return order, None


def quote(mandate_id, deliver_by, lines, client=None):
    """Signed fixed-price offer for explicit lines (used by /v1/quotes and the MCP tool).

    Returns (offer, jws, None) or (None, None, error)."""
    mandate = policy.load(mandate_id)
    ids = [str(l["productId"]) for l in lines]
    client = client or ValioClient()
    prices = client.prices(mandate["customer"], deliver_by, ids)
    now_s, avail = client.availability(mandate["customer"], deliver_by, ids)
    out = []
    for l in lines:
        pid = str(l["productId"])
        pr, av, p = prices.get(pid), avail.get(pid) or {}, catalog.get(pid)
        if not p or not pr or not pr.get("catalogPrice"):
            return None, None, f"no price for {pid}"
        if av.get("closingTime") and av["closingTime"] < now_s:
            return None, None, f"{pid}: order deadline for {deliver_by} passed at {av['closingTime']}"
        sched = [x for x in av.get("scheduleLines", []) if not x.get("salesBlock") and x.get("availableDate")
                 and x["availableDate"] <= deliver_by]
        if not sched or sum(x["availableAmount"] for x in sched) < l["units"]:
            return None, None, f"{pid} not available in that quantity by {deliver_by}"
        up = pr["catalogPrice"]["value"]
        out.append({"productId": pid, "name": p["name"], "units": l["units"], "salesUnit": p["salesUnit"],
                    "unitPrice": up, "lineTotal": round(up * l["units"], 2), "perUnit": 1,
                    "deliveryDate": min(x["availableDate"] for x in sched), "reliability": p["reliability"],
                    "substitution": l.get("substitution")})
    option = {"lines": out, "total_eur": round(sum(x["lineTotal"] for x in out), 2)}
    offer, jws = create_offer(mandate, option, {"deliver_by": deliver_by})
    return offer, jws, None


def order_from_jws(jws, idempotency_key, rationale="", requested_units=None):
    """Commit a signed offer on behalf of an external agent (used by /v1/orders and MCP)."""
    ok, payload = signing.verify(jws)
    if not ok:
        store.log("security", "unknown", "security.invalid_signature", {"error": payload})
        return None, payload
    mandate = policy.load(payload["mandate_id"])
    ctx = {"deliver_by": payload["deliver_by"], "delivery_date": max(l["deliveryDate"] for l in payload["lines"]),
           "rationale": rationale, "requested_units": requested_units}
    return place_order(jws, idempotency_key, mandate, ctx, actor=f"agent:{mandate['agent']}")


def decide(order_id, decision, approver, comment=""):
    order = store.get_order(order_id)
    if not order or order["status"] != "pending_approval":
        return None, "order is not waiting for approval"
    order["status"] = "placed" if decision == "approve" else "rejected"
    order["record"].setdefault("approvals", []).append(
        {"approver": approver, "decision": decision, "comment": comment, "at": store.now()})
    store.save_order(order)
    store.log(order_id, f"human:{approver}", f"approval.{decision}", {"comment": comment})
    return order, None


def run(text, mandate_id, overrides=None, simulate_price_change=False, inject=False):
    """Run the whole buyer-agent flow and return a step-by-step trace for the demo UI."""
    mandate = policy.load(mandate_id, overrides)
    client = ValioClient()
    steps = []
    run_id = "run_" + uuid.uuid4().hex[:10]
    ai_intent = ai.parse(text)
    if ai_intent and "_error" not in ai_intent:
        intent, parser = ai_intent, f"Claude ({ai.MODEL})"
    else:
        intent = parse_request(text)
        parser = "rules" + (f" (Claude unavailable: {ai_intent['_error']})" if ai_intent else " (no Claude key configured)")
    intent["parser"] = parser
    store.log(run_id, f"agent:{mandate['agent']}", "request.received", {"text": text, "mandate_id": mandate_id, "parser": parser})
    steps.append({"step": "request", "title": "Understand the request", "intent": intent})

    # The agent also filters out anything the mandate forbids before asking for prices.
    candidates, trace = catalog.search(intent["product"], intent["attributes"])
    forbidden = set(mandate["hard_limits"].get("forbidden_allergens", []))
    excluded = [{"productId": p["id"], "name": p["name"], "reason": "contains " + ", ".join(
        catalog.ALLERGENS.get(a, a) for a in sorted(set(p["contains"]) & forbidden))}
        for p in candidates if set(p["contains"]) & forbidden]
    candidates = [p for p in candidates if not set(p["contains"]) & forbidden]
    # Add the official substitutes Valio lists for these products.
    seen = {p["id"] for p in candidates}
    subs = []
    for p in list(candidates):
        for sid in p["substitutes"]:
            sp = catalog.get(sid)
            if sp and sid not in seen and not set(sp["contains"]) & forbidden:
                seen.add(sid)
                subs.append(sid)
                candidates.append(sp)
    steps.append({"step": "lookup", "title": "Find candidates in the catalog (local, no API calls)",
                  "search": trace, "excluded_by_mandate": excluded, "official_substitutes_added": subs,
                  "candidates": [catalog.describe(p) for p in candidates]})
    if not candidates:
        return {"run_id": run_id, "steps": steps, "result": {"status": "no_match"}, "api_calls": client.calls}

    if inject and len(candidates) > 1:
        steps += _injection_attack(candidates[1], mandate, intent, client, run_id)
        candidates = [p for p in candidates if p["id"] != candidates[1]["id"]]

    ids = [p["id"] for p in candidates]
    prices = client.prices(mandate["customer"], intent["deliver_by"], ids)
    now_s, avail = client.availability(mandate["customer"], intent["deliver_by"], ids)
    rows = [_assess(p, prices.get(p["id"]), avail.get(p["id"]), intent, now_s) for p in candidates]
    for r in rows:
        if r["productId"] in subs:
            r["substitution"] = "official_substitute"
    # Exception handling: for products we can't get in time, find the earliest date they could come.
    late = [r for r in rows if r["status"] in ("deadline_passed", "unavailable")]
    if late:
        later = (date.fromisoformat(intent["deliver_by"]) + timedelta(days=1)).isoformat()
        _, avail2 = client.availability(mandate["customer"], later, [r["productId"] for r in late])
        for r in late:
            a2 = avail2.get(r["productId"]) or {}
            ok2 = [s for s in a2.get("scheduleLines", []) if not s.get("salesBlock") and s.get("availableDate")]
            if ok2 and not (a2.get("closingTime") and a2["closingTime"] < now_s):
                r["earliest_alternative"] = min(s["availableDate"] for s in ok2)
    usable = [r for r in rows if r["status"] in ("ok", "partial")]
    _score(usable, mandate["weights"])
    rows.sort(key=lambda r: (r["status"] not in ("ok", "partial"), -(r.get("score") or 0)))
    steps.append({"step": "price_availability", "title": "Ask Valio for price and availability (shortlist only)",
                  "now": now_s, "rows": rows})

    options = build_options(usable, intent)
    steps.append({"step": "options", "title": "Compare options (good-better-best)", "options": options})
    if not options:
        store.log(run_id, "agent", "no_option", {"reason": "nothing deliverable by the date"})
        return {"run_id": run_id, "steps": steps, "result": {"status": "no_option"}, "api_calls": client.calls,
                "mandate": mandate}

    chosen = options[0]
    offer, jws = create_offer(mandate, chosen, intent)
    steps.append({"step": "offer", "title": "Get a signed, fixed-price offer", "offer": offer, "jws": jws})

    context = {"deliver_by": intent["deliver_by"], "delivery_date": max(l["deliveryDate"] for l in chosen["lines"]),
               "partial": chosen["partial"], "simulate_price_change": simulate_price_change,
               "request": text, "run_id": run_id, "options_considered": len(options),
               "requested_quantity": intent["quantity"],
               "rationale": f"{chosen['label']}: {chosen['why']}."}
    order, err = place_order(jws, f"{run_id}:{offer['offer_id']}", mandate, context, client)
    if err and simulate_price_change:
        steps.append({"step": "order_rejected", "title": "Order rejected by the gateway", "error": err})
        context["simulate_price_change"] = False
        offer, jws = create_offer(mandate, chosen, intent)
        steps.append({"step": "offer", "title": "Agent requests a fresh signed offer", "offer": offer, "jws": jws})
        order, err = place_order(jws, f"{run_id}:{offer['offer_id']}", mandate, context, client)
    if err:
        steps.append({"step": "order_rejected", "title": "Order rejected by the gateway", "error": err})
        return {"run_id": run_id, "steps": steps, "result": {"status": "error", "error": err},
                "api_calls": client.calls, "mandate": mandate}
    order["record"]["api_calls"] = client.calls
    order["record"]["rows"] = [{k: r.get(k) for k in ("productId", "name", "status", "issues", "total", "score",
                                                      "reliability", "earliest_alternative")} for r in rows]
    order["record"]["options"] = options
    store.save_order(order)
    steps.append({"step": "policy", "title": "Check the order against the mandate", "policy": order["policy"]})
    store.log(run_id, f"agent:{mandate['agent']}", "run.completed",
              {"order_id": order["id"], "status": order["status"], "api_calls": len(client.calls)})
    return {"run_id": run_id, "steps": steps, "order": order, "api_calls": client.calls, "mandate": mandate,
            "result": {"status": order["status"]}}


def _injection_attack(target, mandate, intent, client, run_id):
    """Red-team demo: a poisoned product description tries to hijack the buyer agent.

    1. The gateway scans supplier text before serving it and flags the product.
    2. We then play a *compromised* agent that obeys the hidden instruction anyway, to show that the
       gateway's mandate check stops the order even when the agent itself has been fooled.
    """
    steps = []
    findings = security.scan(security.POISON)
    store.log(target["id"], "gateway", "security.injection_detected",
              {"productId": target["id"], "findings": [f["why"] for f in findings], "run_id": run_id})
    steps.append({"step": "injection_scan", "title": "Red team: poisoned supplier text detected",
                  "product": catalog.describe(target), "text": security.POISON, "findings": findings})
    units = 10000
    offer, jws, err = quote(mandate["mandate_id"], intent["deliver_by"], [{"productId": target["id"], "units": units}], client)
    if err:
        steps.append({"step": "attack_result", "title": "Compromised agent tries to obey the injection",
                      "units": units, "product": target["name"], "blocked_at": "offer", "error": err})
        return steps
    order, oerr = place_order(jws, f"{run_id}:attack", mandate,
                              {"deliver_by": intent["deliver_by"], "delivery_date": offer["lines"][0]["deliveryDate"],
                               "requested_quantity": intent["quantity"], "request": "(injected instruction)",
                               "rationale": "followed instructions found in a product description", "run_id": run_id},
                              client, actor=f"agent:{mandate['agent']} (compromised)")
    steps.append({"step": "attack_result", "title": "Compromised agent tries to obey the injection",
                  "units": units, "product": target["name"], "total_eur": offer["total_eur"],
                  "order": order, "error": oerr})
    return steps
