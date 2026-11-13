"""Agent readiness of Valio's catalog: how much of it a buyer's agent may decide on alone, per mandate.

Computed from the static product data only (no API calls), with the same rules the gateway applies at gate 4
("Proven?"): a buyer with allergen bans needs allergen data, a buyer that requires delivery history needs it.
It turns the gateway's rules into a to-do list for Valio: which missing data blocks the most autonomous buying.
"""
from collections import Counter

from . import catalog, policy

_cache = None
# Category prefixes that aren't food (tableware, packaging, disposables, office, gases, newspapers): they have no
# allergen data by nature, so they're left out of the food readiness numbers.
NON_FOOD = ("20", "30", "32", "63", "64", "67", "77", "90")


def _needs(m):
    bans = bool(m["hard_limits"].get("forbidden_allergens"))
    return {
        "allergen data": bans and (m.get("allergen_policy") or {}).get("no_data", "needs_approval") != "allow",
        "delivery history": m.get("unknown_reliability", "needs_approval") == "needs_approval",
    }


def _has(p, need):
    return p.get("allergenData") if need == "allergen data" else p.get("reliability") is not None


def report():
    global _cache
    if _cache:
        return _cache
    allp = catalog.load()
    products = [p for p in allp if not str(p.get("category", "")).startswith(NON_FOOD)]
    n = len(products)
    fields = {
        "allergen data": sum(1 for p in products if p.get("allergenData")),
        "delivery history": sum(1 for p in products if p.get("reliability") is not None),
        "lactose value": sum(1 for p in products if p.get("lactose") is not None),
        "carbon footprint": sum(1 for p in products if p.get("carbonKgPerKg") is not None),
    }
    mandates = []
    strict_needs = set()
    for m in policy.mandates().values():
        needs = [k for k, v in _needs(m).items() if v]
        strict_needs |= set(needs)
        ready = sum(1 for p in products if all(_has(p, k) for k in needs))
        mandates.append({"buyer": m["buyer"], "agent": m["agent"], "needs": needs, "ready": ready,
                         "ready_pct": round(100 * ready / n, 1)})
    # Where fixing data unlocks the most: categories with the most products missing what strict buyers need.
    blocked = Counter()
    samples = {}
    for p in products:
        missing = [k for k in sorted(strict_needs) if not _has(p, k)]
        if missing:
            blocked[p["category"]] += 1
            samples.setdefault(p["category"], [])
            if len(samples[p["category"]]) < 2:
                samples[p["category"]].append(p["name"][:40])
    size = Counter(p["category"] for p in products)
    fix_first = [{"category": c, "blocked": k, "of": size[c], "examples": samples[c]} for c, k in blocked.most_common(8)]
    _cache = {"products": n, "non_food_excluded": len(allp) - n,
              "fields": [{"field": f, "have": v, "pct": round(100 * v / n, 1)} for f, v in fields.items()],
              "mandates": mandates, "fix_first": fix_first,
              "fix_first_unlocks": sum(x["blocked"] for x in fix_first)}
    return _cache
