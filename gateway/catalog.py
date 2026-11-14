"""Product catalog: loads data/catalog.json (built by scripts/build_catalog.py) and searches it.

Search is local and structured, so the agent only calls the Valio APIs for a short list of
candidates, never for the whole catalogue.
"""
import json
import threading
import re
import unicodedata
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
MODIFIERS |= {"cafe", "barista", "kahvi", "kaakao", "piima", "kerma", "jogurtti", "rahka", "annos"}
# "butter" must not return nut or seed butters, "milk" must not return coconut or almond milk.
MODIFIERS |= {"nut", "nuts", "cashew", "peanut", "almond", "hazelnut", "seed", "seeds", "shea", "coconut", "apple", "soy", "soya"}
# "butter" must not return butter croissants: baked goods and dishes made with the product are other products.
MODIFIERS |= {"croissant", "croissants", "bun", "buns", "butterbun", "cookie", "cookies", "biscuit", "biscuits", "pastry", "pastries",
              "pie", "muffin", "donut", "doughnut", "roll", "rolls", "bread", "brioche", "danish", "flavoured", "flavored",
              "squash", "butternutsquash", "tableskirt", "candle", "scotch", "butterscotch", "spray", "glaze", "ball", "balls", "bean", "beans",
              "popcorn", "microwave", "taste", "aroma", "aromaoil", "voiaromiöljy"}
# Finnish compounds: "kahvimaito" (coffee milk), "annosmaito" (portion cups), "suklaamaito" ...
MODIFIER_PREFIXES = ("kahvi", "kaakao", "suklaa", "annos", "piimä", "kerma", "jogurt", "rahka", "vanilja", "mansikka")
# English words for the Finnish prefixes: when the request itself asks for "coffee milk", "kahvimaito" is the
# product, not a modifier (otherwise every real coffee milk is penalised and search falls back to coffee drinks).
PREFIX_MEANS = {"kahvi": {"coffee", "cafe"}, "kaakao": {"cocoa"}, "suklaa": {"chocolate"}, "annos": {"portion"},
                "piimä": {"buttermilk", "sour"}, "kerma": {"cream"}, "jogurt": {"yoghurt", "yogurt"},
                "rahka": {"quark"}, "vanilja": {"vanilla"}, "mansikka": {"strawberry"}}

_catalog = None
_by_id = None


_load_lock = threading.Lock()


def load():
    """Load the catalog once. Built fully before it is published: several requests arrive together after a
    restart, and one used to see the list before its id index existed (crash in the Valio view)."""
    global _catalog, _by_id
    if _catalog is None or _by_id is None:
        with _load_lock:
            if _catalog is None or _by_id is None:
                items = json.load(open(DATA_DIR / "catalog.json", encoding="utf-8"))
                for p in items:
                    p["_text"] = fold(f"{p['name']} {p['name_fi']} {p['brand'] or ''}")
                by_id = {p["id"]: p for p in items}
                _by_id, _catalog = by_id, items
    return _catalog


def get(product_id):
    load()
    return _by_id.get(str(product_id))


def fold(s):
    """Lower-case and drop accents (Cafè, café -> cafe) but keep the Finnish letters å, ä, ö."""
    out = []
    for ch in (s or "").lower():
        if ch in "åäö":
            out.append(ch)
        else:
            out.append("".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)))
    return "".join(out)


def _words(s):
    return set(re.findall(r"[a-zåäö]+", fold(s)))


def _is_modifier(w, asked=frozenset()):
    if w in MODIFIERS:
        return True
    pre = next((x for x in MODIFIER_PREFIXES if w.startswith(x)), None)
    return bool(pre) and not (PREFIX_MEANS.get(pre, set()) & asked)


_vocab = None


def vocabulary():
    """Every word that appears in a product name: used to spot food words a request mentioned but the
    agent's reading dropped ("chili cheese chicken ... milk" must not quietly become just "milk")."""
    global _vocab
    if _vocab is None:
        _vocab = set()
        for p in load():
            _vocab |= _words(p["_text"])
    return _vocab


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
            if p.get("lactose") is not None:
                if p["lactose"] > 0.1:
                    return False, f"lactose {p['lactose']} g/100 g", gaps
            elif "FREE_FROM_LACTOSE" in p["claims"] or re.search(r"lactose[ -]?free|laktoositon", p["_text"]):
                gaps.append("lactose value missing: lactose-free only per name/claim")
            else:
                return False, "not lactose-free", gaps
            if re.search(r"low[ -]lactose|vähälaktoos", p["_text"]) and p.get("lactose") != 0:
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


def measure(p):
    """How a product is measured, from its name: "KG" (e.g. 4x2kg, 250 g), "L" (1 l, 2,5 dl) or None."""
    t = p["_text"]
    if re.search(r"\d\s*(?:l|dl|cl|ml|litra?a?)\b", t):
        return "L"
    if re.search(r"\d\s*(?:kg|g|kilo)\b", t):
        return "KG"
    return None


def pieces(p):
    """How many pieces one sales unit holds, from the name: "egg M10" -> 10, "12 x Arla ..." -> 12,
    "40x90g" -> 40, "100pcs" -> 100. None when the name doesn't say."""
    t = fold(p["name"])
    for pat in (r"\b(?:s|m|l|xl)(\d{1,3})\b",           # egg size and count: M10, L30
                r"^\s*(\d{1,4})\s*x\s*(?=[a-zåäö\d])",    # leading multipack: "12 x Arla", "40x90g"
                r"\b(\d{1,4})\s*x\s*\d",                 # "40x90g", "100x2 cl"
                r"\b(\d{1,4})\s*(?:pcs|kpl|pieces|st)\b"):  # "100pcs", "30 kpl"
        m = re.search(pat, t)
        if m and int(m.group(1)) > 1:
            return int(m.group(1))
    return None


def evidence(p, attrs=()):
    """Short, checkable reasons this product matches, from structured data."""
    ev = []
    if p.get("lactose") is not None and "FREE_FROM_LACTOSE" in attrs:
        ev.append(f"lactose {p['lactose']:g} g/100 g")
    if p.get("fatPct") is not None:
        ev.append(f"fat {p['fatPct']:g} %")
    if p["salesUnit"]:
        ev.append("long-life (UHT)" if is_long_life(p) else "fresh/chilled")
    ev.append("contains: " + (", ".join(ALLERGENS.get(a, a) for a in p["contains"]) or "no declared allergens")
              if p.get("allergenData") else "allergen data missing")
    if p.get("mayContain"):
        ev.append("may contain: " + ", ".join(ALLERGENS.get(a, a) for a in p["mayContain"]))
    return ev


# Words that make a product a processed form of what was asked for ("boiled eggs", "pea patty", "tomato puree").
# They only count when the buyer didn't say them ("liquid egg" asks for liquid egg).
PROCESSED = {"boiled", "cooked", "peeled", "liquid", "yolk", "white", "albumen", "powder", "spread", "scrambled", "mix",
             "mayo", "mayonnaise", "glaze", "spray", "patty", "patties", "burger", "burgers", "balls", "protein", "mince",
             "pasty", "chorizo", "puree", "paste", "passata", "crushed", "chopped", "polpa", "sauce", "ketchup", "soup",
             "juice", "dried", "sundried", "cubes", "oil", "pickled", "brine", "concentrate", "flavoured", "chips",
             "crisps", "wasabi", "snack", "salad", "dressing", "pie", "pizza", "cake", "fried", "breaded", "nuggets"}


def _processed(p, query_words):
    words = set(_words(p["name"]))
    return bool((words & PROCESSED) - set(query_words))


def search(query, attrs=(), limit=30, spec=None, type_check=None):
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
        penalty = len({w for w in words if _is_modifier(w, q)} - q - {w for w in words if any(w.startswith(t) for t in stems)})
        scored.append((penalty, bool(gaps), p, gaps))
    plain = [x for x in scored if x[0] == 0] or scored
    reality = None
    if type_check and plain:
        # Reality check: which categories ARE the requested product (cream, not ice cream)?
        counts = Counter(x[2]["category"] for x in plain)
        groups = {c: {"count": n, "samples": [x[2]["name"][:60] for x in plain if x[2]["category"] == c][:4]}
                  for c, n in counts.most_common(25)}
        verdict = type_check(query, groups)
        if verdict is not None:
            removed = {c: groups[c]["samples"][:2] for c in groups if c not in verdict["categories"]}
            reality = {"checked_by": "Claude", "kept_categories": verdict["categories"],
                       "removed": [{"category": c, "count": counts[c], "examples": ex} for c, ex in removed.items()],
                       "reason": verdict["reason"]}
            plain = [x for x in plain if x[2]["category"] in verdict["categories"]]
    if reality and plain:
        cat = [(verdict["categories"][0], 0)]  # plainest form of the product, as ranked by the reality check
    else:
        # Without the reality check, the category with the most text matches can be a processed form: "eggs" has
        # more boiled, liquid and powdered egg products than shell eggs, "frozen peas" more pea patties than peas.
        # So count, per category, the products that are the thing itself (no processed-form word the buyer didn't
        # say), and take the category with most of those; only if there are none, the most matches overall.
        # (and that name the thing as a whole word: "peas" must not count "pearl" mozzarella)
        whole = Counter(x[2]["category"] for x in plain if not _processed(x[2], q)
                        and all(any(w == t or _stem(w) == t for w in _words(x[2]["name"])) for t in stems))
        cat = whole.most_common(1) or Counter(x[2]["category"] for x in plain).most_common(1)
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
        "reality_check": reality,
    }
    return picked, trace


def suggest(query, attrs=(), limit=3):
    """For a request with no real match: which parts of it do exist (e.g. "chicken nuggets", "milk").

    Only for explaining the miss. Nothing suggested here is ever ordered automatically."""
    words = [w for w in re.findall(r"[a-zåäö]+", query.lower()) if len(w) > 2]
    phrases = []
    for n in (2, 1):
        for i in range(len(words) - n + 1):
            phrases.append(" ".join(words[i:i + n]))
    out, seen = [], set()
    for ph in phrases:
        if ph == " ".join(words) or ph in seen:
            continue
        seen.add(ph)
        found, tr = search(ph, attrs, limit=3)
        if found and not any(ph in o["phrase"] or o["phrase"] in ph for o in out):
            out.append({"phrase": ph, "count": tr["in_category"], "example": found[0]["name"]})
    return sorted(out, key=lambda o: -len(o["phrase"].split()))[:limit]


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
        "lactosePer100": p.get("lactose"),
        "fatPct": p.get("fatPct"),
        "longLife": is_long_life(p),
        "claims": p["claims"],
        "labels": [LABELS.get(l, l) for l in p["labels"]],
        "deliveryReliability": p["reliability"],
        "carbonKgCO2ePerKg": p["carbonKgPerKg"],
        "substitutes": p["substitutes"],
    }
