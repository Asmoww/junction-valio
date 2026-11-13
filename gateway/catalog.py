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


def matches_attributes(p, attrs):
    """Check required attributes (claims). Returns (ok, reason)."""
    for a in attrs:
        if a == "FREE_FROM_LACTOSE":
            ok = "FREE_FROM_LACTOSE" in p["claims"] or re.search(r"lactose[ -]?free|laktoositon", p["_text"])
        elif a == "ORGANIC":
            ok = "EU_ORGANIC_FARMING" in p["labels"] or "organic" in p["_text"] or "luomu" in p["_text"]
        elif a == "FINNISH":
            ok = p["origin"] == "fi" or any(l in p["labels"] for l in ("GOODS_FROM_FINLAND_BLUE_SWAN", "MADE_IN_FINLAND_FLAG_WITH_KEY"))
        else:
            ok = a in p["claims"]
        if not ok:
            return False, f"not {a.lower().replace('_', ' ')}"
    return True, ""


def search(query, attrs=(), limit=12):
    """Return (candidates, trace). Candidates are catalog entries ranked by relevance."""
    load()
    q = _words(query)
    stems = {_stem(t) for t in q}
    scored = []
    for p in _catalog:
        words = _words(p["_text"])
        if not all(any(w.startswith(t) for w in words) for t in stems):
            continue
        ok, _ = matches_attributes(p, attrs)
        if not ok:
            continue
        penalty = len({w for w in words if w in MODIFIERS} - q - {m for m in MODIFIERS if any(m.startswith(t) for t in stems)})
        scored.append((penalty, len(p["name"]), p))
    scored.sort(key=lambda x: (x[0], x[1]))
    plain = [p for pen, _, p in scored if pen == 0] or [p for _, _, p in scored]
    # Keep the dominant product category among clean matches, so "milk" stays in the milk group.
    cat = Counter(p["category"] for p in plain[:20]).most_common(1)
    picked = [p for p in plain if not cat or p["category"] == cat[0][0]][:limit]
    trace = {
        "query": query,
        "attributes": list(attrs),
        "text_matches": len(scored),
        "after_modifier_filter": len(plain),
        "category": cat[0][0] if cat else None,
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
        "claims": p["claims"],
        "labels": [LABELS.get(l, l) for l in p["labels"]],
        "deliveryReliability": p["reliability"],
        "carbonKgCO2ePerKg": p["carbonKgPerKg"],
        "substitutes": p["substitutes"],
    }
