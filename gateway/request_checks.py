"""Deterministic checks on the request text, run whatever reads the request (Claude or the rule-based parser).

They only ever make the agent more careful: they can turn a "buy" into a question, a decline or an ask-back,
add allergen bans, or refuse a quantity. They never make an order more likely. Results feed gates 0-1 of
the decision ladder (gates.py).
"""
import re
from datetime import date

from . import catalog

# ---- Gate 0: is it a purchase? ---------------------------------------------------------------------
NO_BUY = re.compile(r"\b(do not|don'?t|dont|never|not)\s+(order|buy|purchase|place|commit|tilaa)\b"
                    r"|\b(don'?t|do not|no longer)\s+need\b|\bälä\s+(tilaa|osta|tee tilaus)"
                    r"|\bbeställ\s+inte\b|\binte\s+beställ|\bei\s+tarvita\b")
CANCEL = re.compile(r"\b(cancel\w*|peruuta\w*|avbeställ\w*|annul\w*)\b|\bstop (all |the )?(recurring|standing|regular)\b"
                    r"|\b(return|refund|credit note|rma)\b")
ABORT = re.compile(r"\b(scratch that|wait,? no|hold (everything|off|it all)|never ?mind|forget it)\b")
OUT_OF_SCOPE = re.compile(r"\b(recipe|resepti|why did)\b")
DRY_RUN = re.compile(r"\b(dry[- ]?run|simulate|simulation|hypothetical\w*|without committing|just checking|price check"
                     r"|before locking|don'?t order yet|not place an order)\b")
QUESTION = re.compile(r"\?|\b(how much|how many|what (is|does|would|are)|check (whether|if)|is there|are there|compare"
                      r"|paljonko|vad kostar|show me|can you show)\b")
ORDER_VERB = re.compile(r"\b(order|need|buy|purchase|send|get me|give me|restock|tilaa|tarvitse\w*|beställ|behöver)\b")
CONDITIONAL = re.compile(r"\b(if and only if|only if|only order if|unless|provided that|if (tomorrow|it is|the price|there is))\b")


def request_type(text):
    """(type, reason) when the wording settles it, else (None, ""). Only ever away from "buy"."""
    t = catalog.fold(text)
    asks = bool(QUESTION.search(t)) or "check" in t
    if NO_BUY.search(t):
        if asks or DRY_RUN.search(t):
            return "question", "it asks about prices or stock and says not to order"
        return "other", "the request says not to order"
    if CANCEL.search(t):
        return "change", "it asks to cancel, stop, change or return an order"
    if ABORT.search(t):
        return "other", "the requester withdrew the request in the same message"
    if OUT_OF_SCOPE.search(t):
        return "other", "it asks for something other than a purchase"
    if DRY_RUN.search(t):
        return "question", "it asks for a dry run or a price check, not an order"
    if asks and not ORDER_VERB.search(t):
        return "question", "it's a question, not an order"
    return None, ""


def conditional(text):
    m = CONDITIONAL.search(catalog.fold(text))
    return f"the order depends on a condition the agent can't check (“{m.group(0)}”)" if m else ""


# ---- Allergies and diets stated in the request -------------------------------------------------------
ALLERGY = [
    (r"milk[- ]?(protein )?allerg\w*|dairy[- ]?(allerg\w*|free)|cow'?s? milk allerg\w*|milk[- ]free|allergic to (milk|dairy)"
     r"|maitoallerg\w*|maidoton|ige[- ]mediated|\bvegan\b|anaphyla\w* (to )?(milk|dairy)|dairy allergy", ["AM"]),
    (r"celiac|coeliac|keliak\w*|gluten[- ]?(free|allerg\w*|intoleran\w*)|gluteeniton", ["AW"]),
    (r"\bnut[- ]?(free|allerg\w*)|tree[- ]nut|pähkinäallerg\w*|pähkinätön", ["AN", "AP"]),
    (r"peanut[- ]?(free|allerg\w*)|maapähkinä", ["AP"]),
    (r"soy[a]?[- ]?(free|allerg\w*)|soija\w*allerg\w*", ["AY"]),
    (r"\begg[- ]?(free|allerg\w*)|kananmuna\w*allerg\w*", ["AE"]),
    (r"\bfish[- ]?(free|allerg\w*)|kala\w*allerg\w*", ["AF"]),
    (r"sesame[- ]?(free|allerg\w*)|seesam\w*", ["AS"]),
]


def allergies(text):
    """Allergen codes the request says must be avoided ("for a guest with a milk allergy", "celiac ward")."""
    t = catalog.fold(text)
    codes = set()
    for pat, cs in ALLERGY:
        if re.search(pat, t):
            codes |= set(cs)
    return sorted(codes)


# ---- Quantities and units -------------------------------------------------------------------------
VOLUME = {"l": 1, "litre": 1, "litres": 1, "liter": 1, "liters": 1, "litra": 1, "litraa": 1, "litran": 1,
          "dl": 0.1, "decilitre": 0.1, "decilitres": 0.1, "deciliter": 0.1, "deciliters": 0.1,
          "cl": 0.01, "centilitre": 0.01, "centilitres": 0.01, "ml": 0.001, "millilitre": 0.001, "millilitres": 0.001}
MASS = {"kg": 1, "kilo": 1, "kilos": 1, "kiloa": 1, "kilogram": 1, "kilograms": 1, "g": 0.001, "gram": 0.001, "grams": 0.001}
COUNT = {"pcs", "pieces", "piece", "units", "unit", "kpl", "st", "cartons", "carton", "boxes", "box", "packs", "pack",
         "bottles", "bottle", "cups", "crates", "crate", "bags", "bag", "cans", "trays", "tray"}
BAD_UNITS = {"km", "kilometre", "kilometres", "kilometer", "kilometers", "metre", "metres", "meter", "meters", "m2", "m²",
             "square", "kw", "kilowatt", "kilowatts", "watt", "watts", "teaspoon", "teaspoons", "tsp", "tablespoon",
             "tablespoons", "tbsp", "mg", "milligram", "milligrams", "pallet", "pallets", "rullakko", "rullakkoa",
             "gallon", "gallons", "pint", "pints", "ounce", "ounces", "oz"}
_NUM = r"(-\s*)?(\d{1,3}(?:[ ,.]\d{3})+(?!\d)|\d+(?:[.,]\d+)?)"
_TOKEN = re.compile(_NUM + r"\s*\)?\s*([a-zåäö²]+)")


def _value(s):
    s = re.sub(r"\s", "", s)
    if re.fullmatch(r"\d{1,3}([,.]\d{3})+", s):
        return float(re.sub(r"[,.]", "", s))
    return float(s.replace(",", "."))


def quantities(text):
    """Every number+unit in the request that reads as an order quantity (not a pack size, price, date or SKU).

    Returns a list of dicts {value, unit, factor, kind, raw, negative, start, end}."""
    t = catalog.fold(text)
    out = []
    for m in _TOKEN.finditer(t):
        neg, num, word = m.group(1), m.group(2), m.group(3)
        before, after = t[max(0, m.start() - 6):m.start()], t[m.end():m.end() + 24]
        if "€" in before or re.match(r"\s*/", t[m.end():]):
            continue  # a price (€2.01/L)
        if re.search(r"\d\s*x\s*$", before) or re.search(r"\bx\s*$", t[max(0, m.start() - 2):m.start()]):
            continue  # "100x2 cl": pack description
        if word in VOLUME or word in MASS:
            # A small size followed by a container or pack word is a pack size ("1 L cartons", "20 L novobox");
            # "200 L Novobox milk" is an amount.
            small = _value(num) * (VOLUME.get(word) or MASS.get(word)) <= 25
            if small and re.match(r"\s*(cartons?|boxes?|bottles?|packs?|cups?|bricks?|novobox\w*|bag[- ]in[- ]box|portion)", after) \
                    or small and re.search(r"(cartons?|boxes?|bottles?|packs?|cups?)\s+of\s*$", t[max(0, m.start() - 16):m.start()]):
                continue
            unit, factor = ("L", VOLUME[word]) if word in VOLUME else ("KG", MASS[word])
            kind = "measure"
        elif word in COUNT:
            unit, factor, kind = "PCS", 1, "count"
        elif word in BAD_UNITS:
            unit, factor, kind = None, None, "bad_unit"
        else:
            continue
        try:
            value = _value(num)
        except ValueError:
            continue
        out.append({"value": value, "unit": unit, "factor": factor, "kind": kind, "raw": m.group(0).strip(),
                    "negative": bool(neg), "start": m.start(), "end": m.end()})
    return out


def quantity_problem(text):
    """Why the quantity can't be ordered as written, or ""."""
    t = catalog.fold(text)
    if re.search(r"\b(nan|inf|infinity)\s*(l|litres?|liters?|kg|units?|pcs)\b", t):
        return "the quantity isn't a number"
    qs = quantities(text)
    for q in qs:
        if q["kind"] == "bad_unit":
            return f"“{q['raw']}” isn't a unit food is sold in: use litres, kilograms or pieces"
        if q["negative"] or q["value"] <= 0:
            return f"“{q['raw']}” isn't a quantity that can be ordered"
        if q["kind"] == "count" and q["value"] != int(q["value"]):
            return f"“{q['raw']}”: packs can only be ordered whole"
    return ""


def _order_quantities(text):
    qs = [q for q in quantities(text) if q["kind"] in ("measure", "count")]
    if any(q["kind"] == "count" for q in qs):
        # "100 units of Eila milk 1 l": with a count present, a small size is the pack size, not a second amount.
        qs = [q for q in qs if q["kind"] == "count" or q["value"] * q["factor"] > 25]
    if not qs:
        # "300 eggs": a number followed by a product word is a count of pieces.
        t = catalog.fold(text)
        for m in re.finditer(r"(?<![\d.,-])(\d+)\s+([a-zåäö]+)", t):
            if len(m.group(1)) < 6 and m.group(2) in catalog.vocabulary() and m.group(2) not in VOLUME | MASS:
                # "1000 milk liter": a unit word later in the request is the unit; otherwise it's a count of pieces.
                later = re.search(r"\b(" + "|".join(sorted(set(VOLUME) | set(MASS), key=len, reverse=True)) + r")\b",
                                  t[m.end():])
                unit, factor = ("PCS", 1) if not later else (("L", VOLUME[later.group(1)]) if later.group(1) in VOLUME
                                                            else ("KG", MASS[later.group(1)]))
                qs = [{"value": float(m.group(1)), "unit": unit, "factor": factor, "kind": "count" if not later else "measure",
                       "raw": m.group(0), "negative": False, "start": m.start(), "end": m.start() + len(m.group(1))}]
                break
    return qs


def order_quantity(text):
    """The single quantity the request asks for, converted to L / KG / PCS, or None. Several → see multi_line."""
    qs = [q for q in _order_quantities(text) if not q["negative"] and q["value"] > 0]
    if len(qs) != 1:
        return None
    q = qs[0]
    return {"quantity": round(q["value"] * q["factor"], 6), "unit": q["unit"], "raw": q["raw"], "end": q["end"]}


def multi_line(text):
    """True when the request names several quantities (a shopping list): each line must be sent separately."""
    return len(_order_quantities(text)) > 1


# ---- Dates -----------------------------------------------------------------------------------------
def date_problem(text, deliver_by, today):
    """(reason, gate): an invalid or past date is unclear (gate 1); a window Valio can't meet is impossible (gate 2)."""
    t = catalog.fold(text)
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", t):
        try:
            date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return f"{m.group(0)} isn't a real date", "clear"
    try:
        if deliver_by and date.fromisoformat(deliver_by) < today:
            return f"the delivery date {deliver_by} has already passed", "clear"
    except ValueError:
        return f"{deliver_by} isn't a real date", "clear"
    if re.search(r"\b(in|within)\s+\d+\s*(minutes?|mins?|hours?|h)\b", t):
        return "Valio delivers on delivery days, not within minutes or hours", "possible"
    return "", None


# ---- Breadth, exclusions, named products, policy gaming -------------------------------------------
GENERIC = {"something", "anything", "everything", "stuff", "things", "thing", "items", "item", "products", "product",
           "goods", "ingredients", "ingredient", "food", "foods", "liquid", "liquids", "beverage", "beverages", "cold",
           "white", "usual", "supplies", "protein", "dairy", "kitchen", "cartons", "boxes", "bricks", "units", "base",
           "the", "of", "some", "breakfast", "lunch", "dinner", "baking", "cooking", "fat", "suomesta"}
_brands = None


def brands():
    global _brands
    if _brands is None:
        _brands = {catalog.fold(p["brand"]) for p in catalog.load() if p.get("brand") and " " not in p["brand"].strip()}
    return _brands


def too_broad(product):
    words = set(re.findall(r"[a-zåäö]+", catalog.fold(product or "")))
    if not words:
        return ""
    if words <= GENERIC | brands():
        return f"“{product}” could be hundreds of different products: name the product"
    return ""


EXCLUDE = re.compile(r"\b(?:do not send|don'?t send|do not include|must not (?:match|include|be)|not|ei|eikä|except|excluding"
                     r"|without|reject|no)\s+([^,.;:()—–]+)")


def exclusions(text):
    """Phrases the buyer excluded ("do NOT send Arla Cafè milk 463380", "ei kaakaomaitoa"), as word lists + ids."""
    out = []
    for m in EXCLUDE.finditer(catalog.fold(text)):
        phrase = " ".join(m.group(1).split()[:6])
        out.append({"words": set(re.findall(r"[a-zåäö]+", phrase)), "ids": set(re.findall(r"\b\d{6}\b", phrase))})
    return out


def named_products(text):
    """Catalog ids written in the request (outside exclusions)."""
    ex = exclusions(text)
    excluded = set().union(*[e["ids"] for e in ex]) if ex else set()
    return [i for i in re.findall(r"\b\d{6}\b", text) if catalog.get(i) and i not in excluded]


GAMING = re.compile(r"\b(stay|keep (it|this|them)|remain)\s+(just\s+)?(under|below)\b|\bto avoid (the )?(approval|limit|escalation"
                    r"|sign[- ]?off)\b|\bsub-?orders?\b|\bsplit (this|it|the order)\b|\bqueue another\b|\bparallel requests\b"
                    r"|\bwithout (logging|approval|escalation|re-?signing)\b")


def policy_gaming(text):
    m = GAMING.search(catalog.fold(text))
    return f"it asks the agent to work around a limit (“{m.group(0)}”)" if m else ""
