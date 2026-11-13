"""Buyer-side purchasing agent and the gateway's offer/order logic.

The agent turns a request into a structured intent, looks up a short list of candidates, asks the
Valio APIs for price and availability only for those, builds good-better-best options, takes a
signed offer for the best one and submits the order. The gateway's policy engine decides whether the
order goes through, needs a human, or is blocked.
"""
import math
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import ai, catalog, policy, security, signing, store
from .config import MAX_OFFER_LINES, OFFER_TTL_MINUTES, TIMEZONE, WEIGHT_SALES_UNITS
from .valio import ValioClient

# Valio's availability API uses 999999 for supplier-warehouse ("Terminal") stock it can't see, and the
# mock also returns huge placeholder numbers. None of these are real, confirmed quantities.
STOCK_PLACEHOLDERS = {999999, 666666666, 9999999999}


def _when(s):
    """Parse an API timestamp or date into an aware datetime (naive values are Helsinki time; a bare
    date means the end of that day)."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    if len(str(s)) == 10:
        d = datetime.combine(d.date(), time(23, 59, 59))
    return d if d.tzinfo else d.replace(tzinfo=ZoneInfo(TIMEZONE))


def _passed(deadline, now_s):
    """True if an order deadline is before the API's own clock (compared as times, not strings)."""
    d, n = _when(deadline), _when(now_s)
    return bool(d and n and d < n)


def _number(s):
    """'1,000' / '1 000' / '1.000' -> 1000; '2,5' / '2.5' -> 2.5 (Finnish and English styles)."""
    s = s.strip().replace("\u00a0", " ")
    if re.fullmatch(r"\d{1,3}([ ,.]\d{3})+", s):
        return float(re.sub(r"[ ,.]", "", s))
    return float(s.replace(" ", "").replace(",", "."))

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
    quantity = _number(q.group(1)) if q else None
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
    fat = re.search(r"(\d+(?:[.,]\d+)?)\s*%", t)
    shelf = "long_life" if re.search(r"\buht\b|long[- ]life|pitkään säilyvä", t) else \
        "fresh" if re.search(r"\bfresh\b|tuore", t) else None
    # No guessing: if we can't tell what to buy, say so instead of defaulting to some product.
    return {"product": " ".join(words[:3]), "quantity": quantity, "unit": unit,
            "attributes": attrs, "deliver_by": (deliver_by or today + timedelta(days=3)).isoformat(),
            "fat_pct": float(fat.group(1).replace(",", ".")) if fat else None, "shelf_life": shelf, "raw": text}


def _per_unit(p, price, unit):
    """How much of the requested unit one sales unit holds (e.g. 20 L per carton)."""
    if unit == "PCS":
        return 1.0
    cp, comp = (price.get("catalogPrice") or {}).get("value"), (price.get("comparisonPrice") or {}).get("value")
    if cp and comp and (price.get("comparisonUnit") or "").upper() == unit:
        x = cp / comp  # both prices are rounded to cents, so 31.31 / 1.57 = 19.94 means a 20 L pack
        return round(x) if round(x) and abs(x - round(x)) <= 0.03 * x else round(x, 2)
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
    # Valio stocks "Stock" products itself (the docs say it knows their quantities, even when the mock shows
    # a placeholder number); supplier-warehouse ("Terminal") stock, or no answer, is not confirmed.
    row["stock_confirmed"] = a.get("logisticModel") == "Stock"
    if a.get("salesBlock"):
        row["status"] = "blocked"
        row["issues"].append("sales block")
    elif _passed(a.get("closingTime"), now_s):
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
    row["trust"] = trust_flags(p, price, a, available, now_s)
    row["evidence"] = catalog.evidence(p, intent["attributes"])
    if row["status"] in ("ok", "partial"):
        units = need if row["status"] == "ok" else int(available)
        row["units"] = units
        row["total"] = round(units * unit_price, 2)
    return row


def trust_flags(p, price, avail, available, now_s):
    """How far each claim in Valio's answer can be trusted. Published with every offer line."""
    model = avail.get("logisticModel")
    if model == "Terminal":
        stock = "unknown: supplier warehouse, Valio can't see the quantity"
    elif available in STOCK_PLACEHOLDERS:
        stock = "in stock: exact quantity not given (placeholder value)"
    elif model == "Stock":
        stock = f"confirmed: {available:g} {p['salesUnit']} in Valio stock"
    else:
        stock = "no availability answer"
    cut = avail.get("closingTime")
    cp = price.get("catalogPrice") or {}
    gaps = list(p.get("_gaps", []))
    if not p.get("allergenData"):
        gaps.append("no allergen data: allergens unknown, not absent")
    if p.get("undeclared"):
        gaps.append("undeclared allergens: " + ", ".join(catalog.ALLERGENS.get(a, a) for a in p["undeclared"]))
    if p.get("reliability") is None:
        gaps.append("no delivery history")
    return {
        "stock": stock,
        "stock_known": model == "Stock",
        "cutoff": (f"passed at {cut[:16].replace('T', ' ')}" if cut < now_s else f"order by {cut[:16].replace('T', ' ')}") if cut else "no cut-off given",
        "price": f"{'customer net price' if price.get('netPriceFound') else 'catalog price'}, valid until {cp.get('validTo', '')[:10]}",
        "data_gaps": gaps,
        "reliability_warning": p.get("reliability") is not None and p["reliability"] < 0.95,
    }


def _score(rows, weights):
    if not rows:
        return
    pmin = min(r["price_per_req_unit"] for r in rows)
    cvals = [r["carbon"] for r in rows if r["carbon"]]
    cmin = min(cvals) if cvals else None
    for r in rows:
        price_s = pmin / r["price_per_req_unit"]
        rel_s = r["reliability"] if r["reliability"] is not None else 0.95  # unknown: neutral, shown as a flag instead
        carb_s = (cmin / r["carbon"]) if (cmin and r["carbon"]) else 0.8
        r["score"] = round(weights["price"] * price_s + weights["reliability"] * rel_s + weights["carbon"] * carb_s, 3)
        r["score_parts"] = {"price": round(price_s, 2), "reliability": round(rel_s, 2), "carbon": round(carb_s, 2)}


def _line(r, substitution=None):
    return {"productId": r["productId"], "name": r["name"], "units": r["units"], "salesUnit": r["sales_unit"],
            "unitPrice": r["unit_price"], "lineTotal": r["total"], "perUnit": r["per_unit"],
            "deliveryDate": r["delivery_date"], "reliability": r["reliability"], "substitution": substitution,
            "trust": r.get("trust"), "evidence": r.get("evidence"),
            "orderDeadline": r.get("closing_time"), "priceValidTo": r.get("price_valid_to"),
            "stockConfirmed": r.get("stock_confirmed", True)}


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
    # The price lock ends at the earliest of: 15 minutes, any line's order deadline, any line's price validity.
    limits = [(datetime.now(timezone.utc) + timedelta(minutes=OFFER_TTL_MINUTES), f"{OFFER_TTL_MINUTES} min offer window")]
    for l in option["lines"]:
        limits += [(t, why) for t, why in ((_when(l.get("orderDeadline")), f"order cut-off for {l['productId']}"),
                                           (_when(l.get("priceValidTo")), f"price validity for {l['productId']}")) if t]
    exp, reason = min(limits, key=lambda x: x[0])
    exp = exp.astimezone(timezone.utc)
    payload = {
        "offer_id": offer_id, "issuer": "valio-aimo-trade-gateway (demo)", "customer": mandate["customer"],
        "mandate_id": mandate["mandate_id"], "currency": "EUR", "price_basis": "catalog price, fixed (not negotiated)",
        "lines": [{k: l.get(k) for k in ("productId", "name", "units", "salesUnit", "perUnit", "unitPrice", "lineTotal",
                                         "deliveryDate", "reliability", "substitution", "orderDeadline",
                                         "priceValidTo", "stockConfirmed", "trust", "evidence")}
                  for l in option["lines"]],
        "total_eur": option["total_eur"], "deliver_by": intent["deliver_by"],
        "issued_at": store.now(), "expires_at": exp.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "expiry_reason": reason,
    }
    jws = signing.sign(payload)
    store.save_offer(offer_id, mandate["mandate_id"], payload, jws)
    store.log(offer_id, "gateway", "offer.signed", {"total_eur": payload["total_eur"], "lines": len(payload["lines"]),
                                                     "expires_at": payload["expires_at"]})
    return payload, jws


def place_order(offer_jws, idempotency_key, mandate, context, client=None, actor="agent"):
    """Gateway side of an order: verify the signed offer, re-check prices, apply the mandate."""
    ok, payload = signing.verify(offer_jws)
    if not ok:
        return None, payload
    if existing := store.order_by_key(idempotency_key):
        if existing["offer_id"] != payload["offer_id"]:
            return None, "idempotency key already used for a different offer: use a new key"
        return existing, "duplicate request: returning the original order (idempotent)"
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
    if err := _check_lines(lines):
        store.log("security", "gateway", "security.quote_refused", {"mandate_id": mandate_id, "reason": err})
        return None, None, err
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
        if _passed(av.get("closingTime"), now_s):
            return None, None, f"{pid}: order deadline for {deliver_by} passed at {av['closingTime']}"
        sched = [x for x in av.get("scheduleLines", []) if not x.get("salesBlock") and x.get("availableDate")
                 and x["availableDate"] <= deliver_by]
        if not sched or sum(x["availableAmount"] for x in sched) < l["units"]:
            return None, None, f"{pid} not available in that quantity by {deliver_by}"
        up = pr["catalogPrice"]["value"]
        out.append({"productId": pid, "name": p["name"], "units": l["units"], "salesUnit": p["salesUnit"],
                    "unitPrice": up, "lineTotal": round(up * l["units"], 2), "perUnit": l.get("perUnit", 1),
                    "deliveryDate": min(x["availableDate"] for x in sched), "reliability": p["reliability"],
                    "substitution": l.get("substitution"), "orderDeadline": av.get("closingTime"),
                    "priceValidTo": (pr["catalogPrice"].get("validTo") or "")[:10] or None,
                    "stockConfirmed": av.get("logisticModel") == "Stock",
                    "trust": trust_flags(p, pr, av, sum(x["availableAmount"] for x in sched), now_s),
                    "evidence": catalog.evidence(p)})
    option = {"lines": out, "total_eur": round(sum(x["lineTotal"] for x in out), 2)}
    offer, jws = create_offer(mandate, option, {"deliver_by": deliver_by})
    return offer, jws, None


def _check_lines(lines):
    """Refuse malformed order lines before anything is priced or signed.

    Without this, an agent could add a line with negative units to pull the total under the
    auto-approve limit, or order 0.37 of a carton."""
    if not isinstance(lines, list) or not lines:
        return "an offer needs at least one line"
    if len(lines) > MAX_OFFER_LINES:
        return f"at most {MAX_OFFER_LINES} lines per offer"
    seen = set()
    for l in lines:
        pid = str((l or {}).get("productId", ""))
        p = catalog.get(pid)
        if not p:
            return f"unknown product {pid!r}"
        if pid in seen:
            return f"{pid} appears twice: combine it into one line"
        seen.add(pid)
        units = l.get("units")
        if isinstance(units, bool) or not isinstance(units, (int, float)) or not math.isfinite(units) or units <= 0:
            return f"{pid}: units must be a positive number"
        if (p.get("salesUnit") or "").upper() not in WEIGHT_SALES_UNITS and units != int(units):
            return f"{pid}: sold per {p.get('salesUnit')}, so units must be a whole number"
        lot = p.get("lot") or 1
        if lot >= 1 and round(units / lot, 6) != int(round(units / lot, 6)):
            return f"{pid}: units must be a multiple of the lot size {lot:g}"
    return None


def order_from_jws(jws, idempotency_key, rationale="", requested_units=None):
    """Commit a signed offer on behalf of an external agent (used by /v1/orders and MCP)."""
    ok, payload = signing.verify(jws)
    if not ok:
        store.log("security", "unknown", "security.invalid_signature", {"error": payload})
        return None, payload
    mandate = policy.load(payload["mandate_id"])
    ctx = {"deliver_by": payload["deliver_by"], "delivery_date": max(l["deliveryDate"] for l in payload["lines"]),
           "rationale": rationale, "requested_units": requested_units,
           # The policy's "matches the buyer's request" rule reads requested_quantity (in the same units as
           # the offer lines). Before, external agents' orders skipped that rule entirely.
           "requested_quantity": requested_units}
    return place_order(jws, idempotency_key, mandate, ctx, actor=f"agent:{mandate['agent']}")


def decide(order_id, decision, approver, comment="", client=None):
    """A person approves or rejects a pending order.

    The approver must be named in the buyer's mandate with authority for this total. If the price lock
    ran out while the order waited, the price is checked again before the order is placed: a changed
    price means a new offer, never a silent placement at an old price."""
    order = store.get_order(order_id)
    if not order or order["status"] != "pending_approval":
        return None, "order is not waiting for approval"
    if decision not in ("approve", "reject"):
        return None, "decision must be 'approve' or 'reject'"
    mandate = policy.load(order["mandate_id"])
    who = next((a for a in mandate["approvers"] if a["name"] == approver), None)
    if not who:
        store.log(order_id, f"unknown:{approver}", "security.unknown_approver", {"decision": decision})
        return None, f"{approver or 'this person'} is not an approver in the buyer's mandate"
    if decision == "approve" and order["total"] > who["up_to_eur"]:
        return None, f"{approver} can approve up to €{who['up_to_eur']:,}; this order is €{order['total']:,.2f}"
    offer = order["record"]["offer"]
    if decision == "approve" and offer["expires_at"] < store.now():
        client = client or ValioClient()
        fresh = client.prices(mandate["customer"], offer["deliver_by"], [l["productId"] for l in offer["lines"]])
        changed = [l["productId"] for l in offer["lines"]
                   if abs((fresh.get(l["productId"], {}).get("catalogPrice") or {}).get("value", -1) - l["unitPrice"]) > 0.005]
        if changed:
            order["status"] = "expired"
            order["record"].setdefault("approvals", []).append(
                {"approver": approver, "decision": decision, "comment": comment, "at": store.now(),
                 "note": f"price lock expired and the price changed for {', '.join(changed)}"})
            store.save_order(order)
            store.log(order_id, "gateway", "order.expired", {"products": changed})
            return None, f"the price lock expired and the price changed for {', '.join(changed)}: request a new offer"
        store.log(order_id, "gateway", "price.reconfirmed", {"reason": "price lock expired before approval"})
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
    if not (intent.get("product") or "").strip():
        store.log(run_id, "agent", "request.unclear", {"text": text})
        return {"run_id": run_id, "steps": steps, "api_calls": client.calls, "mandate": mandate,
                "result": {"status": "unclear_request", "error": "couldn't tell which product to buy: name it"}}

    spec = {"fat_pct": intent.get("fat_pct"), "shelf_life": intent.get("shelf_life")}
    candidates, trace = catalog.search(intent["product"], intent["attributes"], spec=spec,
                                       type_check=ai.type_check if ai.enabled() else None)
    # The agent drops what the mandate forbids, or can't be verified, before asking for prices.
    excluded = []
    kept = []
    for p in candidates:
        why = policy.allergen_problem(mandate, p)
        if why and why[1] == "block":
            excluded.append({"productId": p["id"], "name": p["name"], "reason": why[0]})
        else:
            kept.append(p)
    verified = [p for p in kept if not policy.allergen_problem(mandate, p)]
    if verified and len(verified) < len(kept):  # prefer products whose allergen data proves safety
        for p in kept:
            if p not in verified:
                excluded.append({"productId": p["id"], "name": p["name"], "reason": policy.allergen_problem(mandate, p)[0]})
        kept = verified
    candidates = kept
    # Add Valio's official substitutes, but only if they are the same kind of product (fit check).
    seen = {p["id"] for p in candidates}
    subs, misfits = [], []
    for p in list(candidates):
        for sid in p["substitutes"]:
            sp = catalog.get(sid)
            if not sp or sid in seen:
                continue
            seen.add(sid)
            if sp["category"] != p["category"]:
                misfits.append({"productId": sid, "name": sp["name"], "reason": f"fit check: different product type than {p['name']}"})
            elif policy.allergen_problem(mandate, sp):
                misfits.append({"productId": sid, "name": sp["name"], "reason": policy.allergen_problem(mandate, sp)[0]})
            else:
                subs.append(sid)
                sp["_gaps"] = []
                candidates.append(sp)
    gaps = spec_gaps(candidates, intent)
    steps.append({"step": "lookup", "title": "Find candidates in the catalog (local, no API calls)",
                  "search": trace, "excluded_by_mandate": excluded, "official_substitutes_added": subs,
                  "substitutes_rejected": misfits, "spec_gaps": gaps,
                  "candidates": [dict(catalog.describe(p), evidence=catalog.evidence(p, intent["attributes"])) for p in candidates]})
    if not candidates:
        if excluded:
            reasons = sorted({e["reason"] for e in excluded})
            msg = (f"All {len(excluded)} matching products fail this mandate's allergen rules ({'; '.join(reasons)}). "
                   "Nothing was ordered. Lactose-free is not milk-free: ask for a plant-based alternative instead."
                   if any("milk" in r for r in reasons) else
                   f"All {len(excluded)} matching products fail this mandate's allergen rules ({'; '.join(reasons)}). Nothing was ordered.")
            store.log(run_id, f"agent:{mandate['agent']}", "run.no_safe_product", {"excluded": len(excluded)})
            return {"run_id": run_id, "steps": steps, "api_calls": client.calls, "mandate": mandate,
                    "result": {"status": "no_safe_product", "error": msg}}
        # Reality check failed: say what doesn't exist and what does, but order nothing.
        what = intent["product"]
        attrs = " and ".join(a.lower().replace("_", " ").replace("free from ", "") + "-free" if a.startswith("FREE_FROM_")
                             else a.lower() for a in intent["attributes"])
        rc = trace.get("reality_check")
        bare = []
        if intent["attributes"]:  # does the product itself exist, without the requirements?
            bare, btrace = catalog.search(what, [], type_check=ai.type_check if ai.enabled() else None)
        if bare:
            msg = f"Valio has {what} products, but none is verifiably {attrs}. Nothing was ordered."
            sugg = []
        else:
            msg = (f"“{what}” is not a product in Valio's catalog" + (f" ({rc['reason']})" if rc and rc.get("reason") else "")
                   + ". Nothing was ordered.")
            sugg = catalog.suggest(what, intent["attributes"])
        store.log(run_id, f"agent:{mandate['agent']}", "run.not_in_catalog", {"product": what, "suggestions": [x["phrase"] for x in sugg]})
        return {"run_id": run_id, "steps": steps, "api_calls": client.calls, "mandate": mandate,
                "result": {"status": "not_in_catalog", "error": msg, "suggestions": sugg}}

    ids = [p["id"] for p in candidates]
    prices = client.prices(mandate["customer"], intent["deliver_by"], ids)
    now_s, avail = client.availability(mandate["customer"], intent["deliver_by"], ids)
    rows = [_assess(p, prices.get(p["id"]), avail.get(p["id"]), intent, now_s) for p in candidates]
    for r in rows:
        if r["productId"] in subs:
            r["substitution"] = "official_substitute"
    # Note: the mock answers the same for every requested date (see docs/ASSUMPTIONS.md), so asking
    # for "the next possible date" would add API calls without information. We don't.
    usable = [r for r in rows if r["status"] in ("ok", "partial")]
    if inject and len(usable) > 1:
        # Red team: a deliverable product's supplier text is poisoned. The honest agent drops it.
        target = catalog.get(sorted(usable, key=lambda r: r["price_per_req_unit"])[1]["productId"])
        steps += _injection_attack(target, mandate, intent, client, run_id)
        usable = [r for r in usable if r["productId"] != target["id"]]
        rows = [r for r in rows if r["productId"] != target["id"]]
    _score(usable, mandate["weights"])
    rows.sort(key=lambda r: (r["status"] not in ("ok", "partial"), -(r.get("score") or 0)))
    steps.append({"step": "price_availability", "title": "Ask Valio for price and availability (shortlist only)",
                  "now": now_s, "rows": rows})

    options = build_options(usable, intent)
    priced = [r for r in usable if r["status"] == "ok"]
    for g in gaps:
        # Cheapest deliverable product for each value of the unspecified detail.
        key = (lambda r: f"{r['product']['fatPct']:g} %" if r["product"]["fatPct"] is not None else None) \
            if g["field"] == "fat level" else (lambda r: "long-life (UHT)" if r["product"]["longLife"] else "fresh")
        best = {}
        for r in priced:
            k = key(r)
            if k and (k not in best or r["price_per_req_unit"] < best[k]):
                best[k] = r["price_per_req_unit"]
        g["cheapest_per_value"] = dict(sorted(best.items(), key=lambda kv: kv[1]))
        if len(best) > 1:
            g["cost_spread_eur"] = round((max(best.values()) - min(best.values())) * (intent["quantity"] or 0), 2)
    steps.append({"step": "options", "title": "Compare options (good-better-best)", "options": options, "spec_gaps": gaps})
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
               "requested_quantity": intent["quantity"], "required_attributes": intent.get("attributes") or [],
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


def spec_gaps(candidates, intent):
    """Details the buyer didn't specify but that split the candidates (e.g. fat %, fresh vs long-life).

    No chat: the agent compares across the gap and shows it, so the approver sees what was assumed."""
    gaps = []
    fats = sorted({p["fatPct"] for p in candidates if p.get("fatPct") is not None})
    if intent.get("fat_pct") is None and len(fats) > 1:
        gaps.append({"field": "fat level", "values": [f"{f:g} %" for f in fats]})
    shelves = {catalog.is_long_life(p) for p in candidates}
    if intent.get("shelf_life") is None and len(shelves) > 1:
        gaps.append({"field": "fresh or long-life", "values": ["fresh", "long-life (UHT)"]})
    return gaps
