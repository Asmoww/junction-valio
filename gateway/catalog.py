"""Product catalog: loads data/catalog.json (built by scripts/build_catalog.py) and searches it.

Search is local and structured, so the agent only calls the Valio APIs for a short list of
candidates, never for the whole catalogue.
"""
import json
import re
from collections import Counter

from .config import DATA_DIR

ALLERGENS = {
    "AC": "crustaceans", "AE": "egg", "AF": "fish", "AM": "milk", "AN": "tree nuts",
    "AP": "peanuts", "AS": "sesame", "AU": "sulphites", "AW": "gluten", "AY": "soy",
    "BC": "celery", "BM": "mustard", "NL": "lupin", "UM": "molluscs", "ML": "lactose",
    "GB": "barley", "UW": "wheat", "GO": "oats", "NR": "rye", "SA": "almond", "SH": "hazelnut",
}
LABELS = {
    "GOODS_FROM_FINLAND_BLUE_SWAN": "Finnish product (Blue Swan)",
    "MADE_IN_FINLAND_FLAG_WITH_KEY": "Made in Finland (Key Flag)",
    "FINNISH_HEART_SYMBOL": "Heart symbol",
    "EU_ORGANIC_FARMING": "EU organic",
    "SVANEN": "Nordic Swan Ecolabel",
    "RAINFOREST_ALLIANCE": "Rainforest Alliance",
    "EU_ECO_LABEL": "EU Ecolabel",
    "MARINE_STEWARDSHIP_COUNCIL_LABEL": "MSC",
    "FAIR_TRADE_MARK": "Fairtrade",
    "AQUACULTURE_STEWARDSHIP_COUNCIL": "ASC",
    "KRAV_MARK": "KRAV",
}
# Words that change what a product *is*. A candidate containing one is penalised unless the
# request asked for it ("milk" should not return chocolate milk or milk powder).
MODIFIERS = {  # noqa: E501

    "chocolate", "cocoa", "coffee", "latte", "cappuccino", "macchiato", "espresso", "mocca",
    "powder", "fermented", "sour", "curdled", "condensed", "pudding", "cheese", "cake", "flourmix",
    "strawberry", "vanilla", "berry", "berries", "mango", "rhubarb", "caramel", "mint", "flavor",
    "flavour", "portion", "buttermilk", "cooking", "blend", "yoghurt", "yogurt", "quark", "drink",
    "mashed", "mash", "gratin", "salad", "chips", "crisps", "fries", "wedges", "soup", "sauce", "casserole",
    "pie", "dough", "pizza", "snack", "bites", "cream", "creamy", "seasoned", "marinated", "mix",
}
MODIFIERS.discard("drink")  # Finnish lactose-free milk is sold as "milk drink"

_catalog = None
_by_id = None


def load():
    global _catalog, _by_id
    if _catalog is None:
        _catalog = json.load(open(DATA_DIR / "catalog.json", encoding="utf-8"))
        for p in _catalog:
            p["_text"] = f"{p['name']} {p['name_fi']} {p['brand'] or ''}".lower()
        _by_id = {p["id"]: p for p in _catalog}
    return _catalog


def get(product_id):
    load()
    return _by_id.get(str(product_id))


def _words(s):
    return set(re.findall(r"[a-zåäö]+", s.lower()))


def _stem(word):
    """Crude singular form so "potatoes" matches "potato" and "flakes" matches "flake"."""
    for suffix in ("oes", "ies", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "o" if suffix == "oes" else "")
    return word


def is_long_life(p):
    return bool(re.search(r"\buht\b", p["_text"]))


def matches_attributes(p, attrs):
    """Check required attributes against verified data first, names only as a fallback.

    Returns (ok, reason, gaps): gaps lists claims we could not verify from structured data."""
    gaps = []
    for a in attrs:
        if a == "FREE_FROM_LACTOSE":
            if p.get("lactoseG") is not None:
                if p["lactoseG"] > 0.1:
                    return False, f"lactose {p['lactoseG']} g/100 g", gaps
            elif "FREE_FROM_LACTOSE" in p["claims"] or re.search(r"lactose[ -]?free|laktoositon", p["_text"]):
                gaps.append("lactose value missing: lactose-free only per name/claim")
            else:
                return False, "not lactose-free", gaps
            if re.search(r"low[ -]lactose|vähälaktoos", p["_text"]) and p.get("lactoseG") != 0:
                return False, "low-lactose, not lactose-free", gaps
        elif a == "ORGANIC":
            if not ("EU_ORGANIC_FARMING" in p["labels"] or "organic" in p["_text"] or "luomu" in p["_text"]):
                return False, "not organic", gaps
        elif a == "FINNISH":
            if not (p["origin"] == "fi" or any(l in p["labels"] for l in ("GOODS_FROM_FINLAND_BLUE_SWAN", "MADE_IN_FINLAND_FLAG_WITH_KEY"))):
                return False, "not Finnish", gaps
        elif a not in p["claims"]:
            return False, f"not {a.lower().replace('_', ' ')}", gaps
    return True, "", gaps


def matches_spec(p, spec):
    """Optional spec details: fat % (within 0.3 points) and shelf life (fresh / long_life)."""
    fat = spec.get("fat_pct")
    if fat is not None and (p.get("fatPct") is None or abs(p["fatPct"] - fat) > 0.3):
        return False, f"fat {p.get('fatPct')} % ≠ {fat} %"
    shelf = spec.get("shelf_life")
    if shelf == "long_life" and not is_long_life(p):
        return False, "fresh, long-life asked"
    if shelf == "fresh" and is_long_life(p):
        return False, "long-life (UHT), fresh asked"
    return True, ""


def evidence(p, attrs=()):
    """Short, checkable reasons this product matches, from structured data."""
    ev = []
    if p.get("lactoseG") is not None and "FREE_FROM_LACTOSE" in attrs:
        ev.append(f"lactose {p['lactoseG']:g} g/100 g")
    if p.get("fatPct") is not None:
        ev.append(f"fat {p['fatPct']:g} %")
    if p["salesUnit"]:
        ev.append("long-life (UHT)" if is_long_life(p) else "fresh/chilled")
    ev.append("contains: " + (", ".join(ALLERGENS.get(a, a) for a in p["contains"]) or "no declared allergens")
              if p.get("allergenData") else "allergen data missing")
    if p.get("mayContain"):
        ev.append("may contain: " + ", ".join(ALLERGENS.get(a, a) for a in p["mayContain"]))
    return ev


def search(query, attrs=(), limit=30, spec=None):
    """Return (candidates, trace). Candidates are plain products in the dominant category that pass
    verified attribute checks. No arbitrary cut by name or file order: the shortlist is every
    qualifying product up to `limit`, so the cheapest one is never dropped before pricing."""
    load()
    q = _words(query)
    stems = {_stem(t) for t in q}
    spec = spec or {}
    scored, rejected = [], Counter()
    for p in _catalog:
        words = _words(p["_text"])
        if not all(any(w.startswith(t) for w in words) for t in stems):
            continue
        ok, reason, gaps = matches_attributes(p, attrs)
        if not ok:
            rejected[reason.split(" ")[0] if reason.startswith("lactose ") else reason] += 1
            continue
        ok, reason = matches_spec(p, spec)
        if not ok:
            rejected["outside spec"] += 1
            continue
        penalty = len({w for w in words if w in MODIFIERS} - q - {m for m in MODIFIERS if any(m.startswith(t) for t in stems)})
        scored.append((penalty, bool(gaps), p, gaps))
    plain = [x for x in scored if x[0] == 0] or scored
    cat = Counter(x[2]["category"] for x in plain).most_common(1)
    in_cat = [x for x in plain if not cat or x[2]["category"] == cat[0][0]]
    in_cat.sort(key=lambda x: (x[1], x[2]["name"]))  # verified first, then alphabetical (not by length)
    picked = []
    for _, _, p, gaps in in_cat[:limit]:
        p["_gaps"] = gaps
        picked.append(p)
    trace = {
        "query": query,
        "attributes": list(attrs),
        "spec": {k: v for k, v in spec.items() if v not in (None, "any")},
        "text_matches": len(scored) + sum(rejected.values()),
        "failed_checks": dict(rejected),
        "after_modifier_filter": len(plain),
        "category": cat[0][0] if cat else None,
        "in_category": len(in_cat),
        "shortlisted": len(picked),
    }
    return picked, trace


def describe(p):
    """Public view of a product for API responses."""
    return {
        "productId": p["id"],
        "name": p["name"],
        "brand": p["brand"],
        "vendor": p["vendor"],
        "category": p["category"],
        "salesUnit": p["salesUnit"],
        "origin": p["origin"],
        "allergens": [ALLERGENS.get(a, a) for a in p["contains"]],
        "mayContain": [ALLERGENS.get(a, a) for a in p.get("mayContain", [])],
        "allergenDataComplete": p.get("allergenData", False),
        "lactoseGPer100": p.get("lactoseG"),
        "fatPct": p.get("fatPct"),
        "longLife": is_long_life(p),
        "claims": p["claims"],
        "labels": [LABELS.get(l, l) for l in p["labels"]],
        "deliveryReliability": p["reliability"],
        "carbonKgCO2ePerKg": p["carbonKgPerKg"],
        "substitutes": p["substitutes"],
    }
