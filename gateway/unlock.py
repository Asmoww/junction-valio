"""Which missing data, if Valio added it, would let the most real orders go through without a person?

Every order a person had to check because the data couldn't prove something (gate 4, "Proven?") names the
product and the check. This module groups those orders by (product, missing data), then re-runs the policy on
the very same orders as if Valio had added that data, and counts how many would then have gone through alone.
That turns the gateway's escalations into a ranked data to-do list for Valio, measured on real demand rather
than on catalog completeness.

The re-run is a what-if: the policy gets a patched copy of the product and offer for one evaluation. The
catalog, the orders and the audit log are not changed, and nothing is sent to Valio."""
import copy
from collections import defaultdict

from . import catalog, policy, store

# Gate-4 check -> the data that would settle it, and how to simulate "Valio added it".
GAPS = {
    "Allergens verifiable from data": ("allergen data", "the full allergen declaration (contains / may contain)",
                                       lambda p, l: (p.update(allergenData=True, undeclared=[]), None)),
    "Claims verified in product data": ("lactose value", "the lactose value per 100 g, backing the lactose-free claim",
                                        lambda p, l: (p.update(lactose=0), None)),
    "Delivery history known": ("delivery history", "the product's delivery reliability record",
                               lambda p, l: (p.update(reliability=0.98), l.update(reliability=0.98))),
    "Pack contents known": ("pieces per pack", "how many pieces one sales unit holds",
                            lambda p, l: (None, l.update(piecesAssumed=False))),
    "Stock confirmed by Valio": ("stock confirmation", "a confirmed quantity instead of supplier-warehouse placeholder stock",
                                 lambda p, l: (None, l.update(stockConfirmed=True))),
}
HELD = ("pending_approval", "placed", "rejected", "expired")  # orders that went to a person (placed = after approval)


def _held_orders(mandate_id=None):
    for o in store.list_orders(5000):
        if mandate_id and o["mandate_id"] != mandate_id:
            continue
        if o["status"] not in HELD or o["policy"].get("outcome") != "needs_approval":
            continue
        yield o


def _what_if(order, fixes):
    """Policy outcome for this order if Valio had added the data in fixes ({(productId, rule)})."""
    offer = copy.deepcopy(order["record"]["offer"])
    products = {}
    for l in offer["lines"]:
        p = copy.deepcopy(catalog.get(l["productId"]) or {})
        for pid, rule in fixes:
            if pid == l["productId"] and rule in GAPS:
                GAPS[rule][2](p, l)
        products[l["productId"]] = p
    if order["mandate_id"] not in policy.mandates():
        return None
    mandate = policy.load(order["mandate_id"])
    ctx = dict(order["record"].get("context") or {})
    ctx["auto_spend_24h"] = 0  # judge each order on its own, as when it came in
    return policy.evaluate(mandate, offer, ctx, lookup=lambda pid: products.get(pid) or catalog.get(pid))["outcome"]


def report(mandate_id=None, top=10):
    """Data gaps ranked by how many held orders fixing them would let through alone."""
    gaps = defaultdict(list)
    orders = list(_held_orders(mandate_id))
    for o in orders:
        failed = [r for r in o["policy"].get("rules", []) if not r["ok"] and r["rule"] in GAPS]
        for r in failed:
            for l in o["record"]["offer"]["lines"]:
                name = l.get("name") or (catalog.get(l["productId"]) or {}).get("name", "")
                if name and name in str(r.get("detail", "")) or len(o["record"]["offer"]["lines"]) == 1:
                    gaps[(l["productId"], r["rule"])].append(o)
    out = []
    for (pid, rule), os_ in gaps.items():
        unlocked = [o for o in os_ if _what_if(o, {(pid, rule)}) == "auto_approve"]
        p = catalog.get(pid) or {}
        field, what, _ = GAPS[rule]
        other = sorted({r["rule"] for o in os_ if o not in unlocked for r in o["policy"]["rules"]
                        if not r["ok"] and r["rule"] != rule})
        out.append({"productId": pid, "product": p.get("name", pid), "vendor": p.get("vendor"), "check": rule,
                    "missing": field, "add": what, "held_orders": len(os_), "unlocked": len(unlocked),
                    "value_eur": round(sum(o["total"] for o in os_), 2),
                    "unlocked_eur": round(sum(o["total"] for o in unlocked), 2),
                    "buyers": sorted({o["mandate_id"] for o in os_}),
                    "requests": [((o["record"].get("context") or {}).get("request") or o["id"])[:120] for o in os_[:3]],
                    "still_needs_a_person_because": other})
    out.sort(key=lambda g: (-g["unlocked"], -g["unlocked_eur"], -g["held_orders"]))
    gated = [o for o in orders if any(not r["ok"] and r["rule"] in GAPS for r in o["policy"].get("rules", []))]
    all_fixes = {k for k in gaps}
    unlocked_all = [o for o in gated if _what_if(o, all_fixes) == "auto_approve"]
    return {"held_orders": len(orders), "held_for_missing_data": len(gated),
            "unlocked_if_all_fixed": len(unlocked_all), "gaps": out[:top],
            "note": "What-if on real held orders: the policy re-run with the missing data added. Nothing is changed."}
