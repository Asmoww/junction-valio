"""Deterministic checks on the request text, run whatever reads the request (Claude or the rule-based parser).

They only ever make the agent more careful: they can turn a "buy" into a question, a decline or an ask-back,
add allergen bans, or refuse a quantity. They never make an order more likely. Results feed gates 0-1 of
the decision ladder (gates.py).
"""
import re
import unicodedata
from datetime import date

from . import catalog

# ---- Normalising the request (before any reading) ------------------------------------------------------
_INVISIBLE = re.compile("[\u200b-\u200f\u2060-\u2064\ufeff\u202a-\u202e\u2066-\u2069\u00ad]")
_HOMOGLYPHS = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "і": "i", "ј": "j", "у": "y",
                             "к": "k", "м": "m", "т": "t", "н": "h", "в": "b", "ѕ": "s", "ԁ": "d", "ɡ": "g", "ⅼ": "l",
                             "\u2212": "-", "\u2010": "-", "\u2011": "-", "\u2013": "-", "\u00d7": "x"})
_NEGATED = [  # a stated "no X" is a requirement, not an exclusion of the X-free products
    (r"\b(?:no|without|with no|zero|free of|non[- ]?)\s*lactose\b|\blactose\s*(?::\s*none|0\s*%)", "lactose-free"),
    (r"\b(?:no|without|with no|free of|non[- ]?)\s*gluten\b", "gluten-free"),
    (r"\b(?:no|without|with no|free of|non[- ]?)\s*(?:tree )?nuts?\b", "nut-free"),
    (r"\b(?:no|without|with no|free of)\s*eggs?\b", "egg-free"),
    (r"\b(?:no|without|with no|free of|non[- ]?)\s*dairy\b", "dairy-free"),
    (r"\b(?:no|not|non|without|except|anything but|isn'?t|is not)[- ]?(?:uht|long[- ]?life)\b", "fresh (not UHT)"),
]


def normalise(text):
    """The request as the deterministic checks read it: invisible characters removed, look-alike letters and
    full-width characters mapped to plain ones, "no lactose" read as "lactose-free", "12 dozen" as 144."""
    t = unicodedata.normalize("NFKC", text or "")
    t = _INVISIBLE.sub("", t).translate(_HOMOGLYPHS)
    for pat, rep in _NEGATED:
        t = re.sub(pat, rep, t, flags=re.I)
    t = re.sub(r"\b(\d+)\s*dozen\b", lambda m: str(int(m.group(1)) * 12), t, flags=re.I)
    t = re.sub(r"(?<=\d)\s+(?:individual|single|separate|loose)\s+(?=[A-Za-zåäö])", " ", t, flags=re.I)  # "24 individual eggs"
    t = re.sub(r"(?<=[A-Za-zåäöÅÄÖ])\s*(?:&|\+)\s*(?=[A-Za-zåäöÅÄÖ])", " and ", t)  # "milk & cream" is two products
    return t


# ---- Exact quantities ("exactly 24 eggs, no overdelivery") ---------------------------------------------
EXACT = re.compile(r"\b(exactly|precisely|no more than|not more than|at most|maximum|max\.?|no over-?deliver\w*"
                   r"|over-?deliver\w*|don'?t round\w* up|instead of rounding|without rounding|not round\w* up|no rounding)\b", re.I)
_EXACT_CLAUSE = re.compile(r"round\w* up|over-?deliver\w*|\bexceed\w*|\bmaximum quantity\b|\bnot \d+ (packs?|units?|boxes?)\b"
                           r"|\bno more than\b|\bat most\b", re.I)


def exact_quantity(text):
    """Does the buyer rule out rounding up to whole packs? Then an order covering more needs a person."""
    t = catalog.fold(text)
    # "no more than five euros" is a budget (stated_budget), not an exact quantity
    return any(not re.match(r"\s*(€|eur\b|euros?\b|\d[\d,.]*\s*(€|eur\b|euros?\b)|(one|two|three|four|five|six|seven|eight|nine|ten|twenty|fifty|hundred)\s+euros?)",
                            t[m.end():m.end() + 20]) for m in EXACT.finditer(t))


def drop_exact_clauses(text):
    """Remove sentences that only restate the quantity as a limit ("Maximum quantity is 24 eggs, not 24 packs.",
    "If packs would exceed 24 eggs, ask me instead of rounding up."), so the restated number isn't read as a second
    order line and the words aren't 'not understood'. Only sentences whose numbers all repeat the order quantity and
    that order nothing new are dropped; the limit itself is kept as exact_quantity()."""
    sentences = re.split(r"(?<=[.;!?])\s+", text)
    if len(sentences) < 2:
        return re.sub(r",?\s*(with )?no over-?deliver\w*", "", re.sub(r"\bexactly\s+(?=\d)", "", text, flags=re.I), flags=re.I)
    # The first sentence is the order itself and is never dropped.
    main = [sentences[0]] + [s for s in sentences[1:] if not _EXACT_CLAUSE.search(s)]
    nums = set(re.findall(r"\d+(?:[.,]\d+)?", " ".join(main)))
    keep = [sentences[0]]
    for s in sentences[1:]:
        if _EXACT_CLAUSE.search(s) and set(re.findall(r"\d+(?:[.,]\d+)?", s)) <= nums \
                and not re.search(r"\b(and|also|plus|as well)\b[^.]{0,30}\d", s, re.I):
            continue
        keep.append(s)
    out = " ".join(keep)
    out = re.sub(r"\bexactly\s+(?=\d)", "", out, flags=re.I)
    return re.sub(r",?\s*(with )?no over-?deliver\w*", "", out, flags=re.I)


# ---- A budget stated in the request ("total budget EUR 5", "no more than five euros") ---------------------
_NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "twenty": 20, "fifty": 50, "hundred": 100, "a hundred": 100, "one hundred": 100, "thousand": 1000,
             "a thousand": 1000, "one thousand": 1000}
_AMOUNT = r"(?:€|eur|euros?)?\s*(\d[\d\s,.]*\d|\d|" + "|".join(sorted(_NUMWORDS, key=len, reverse=True)) + r")\s*(?:€|eur\b|euros?\b)?"
_BUDGET_CUE = (r"(?:budget(?:\s+(?:for this (?:purchase|order)|of|is|:))*|spend(?:ing)?\s+(?:no more than|at most|max(?:imum)?|up to|under|less than)"
               r"|(?:cost|costs|total|price)\s+(?:no more than|at most|max(?:imum)?|under|less than|below|up to)"
               r"|no more than|at most|max(?:imum)?|up to|under|below|less than|within|cap(?:ped)? (?:at|of))")
_BUDGET = re.compile(r"\b" + _BUDGET_CUE + r"\s*(?:is\s+|of\s+|:\s*)?" + _AMOUNT, re.I)


def stated_budget(text):
    """The most the buyer says this purchase may cost, in EUR, or None. Only amounts written as money count
    (a € sign, EUR or euros, or the word budget), so "no more than 24 eggs" is not a budget."""
    t = catalog.fold(normalise(text))
    best = None
    for m in _BUDGET.finditer(t):
        span = m.group(0)
        if not re.search(r"€|\beur\b|euros?\b|budget", span) and not re.search(r"^\s*(€|eur\b|euros?\b)", t[m.end():m.end() + 8]):
            continue
        raw = m.group(1).strip()
        try:
            v = float(_NUMWORDS[raw]) if raw in _NUMWORDS else _value(raw)
        except (ValueError, KeyError):
            continue
        best = v if best is None else min(best, v)
    return best


def drop_budget_clauses(text):
    """The reader shouldn't call budget words 'not understood': the budget is enforced as a rule instead.
    Sentences that only state the budget (and the ask-first fallback) are left out of reading."""
    if stated_budget(text) is None:
        return text
    sentences = re.split(r"(?<=[.;!?])\s+", text)
    keep = [sentences[0]] + [s for s in sentences[1:] if not (_BUDGET.search(catalog.fold(s)) or
                             re.search(r"\b(if it costs more|ask me first|ask first|including all charges)\b", s, re.I))]
    out = " ".join(keep)
    out = re.sub(r"[,;]?\s*(?:with a |for a |on a )?(?:total )?budget(?: of| is)?\s*" + _AMOUNT + r"(?:\s*(?:in total|total))?", "", out, flags=re.I)
    out = re.sub(r"[,;]?\s*(?:spend(?:ing)?\s+)?(?:no more than|at most|max(?:imum)?|up to|under)\s*(?:€|eur)\s*\d[\d,.]*(?:\s*(?:in total|total))?", "", out, flags=re.I)
    return out


# ---- Gate 0: is it a purchase? ---------------------------------------------------------------------
NO_BUY = re.compile(r"\b(do not|don'?t|dont|never|not)\s+(order|buy|purchase|place|commit|tilaa)\b"
                    r"(?!\s+(?:only|just|half|part|partial\w*|one of|a single|less|fewer|more|anything else|without))"
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
CONDITIONAL = re.compile(r"\b(if and only if|only if|only order if|unless|provided that|if (tomorrow|it is|it'?s|the price|there is"
                         r"|cheaper|under|below|over|less|more)|when the price|as soon as the price|if valio has|in case)\b")
# A question about ordering is not an order ("How much would it cost to order 100 L?", "Should we order ...?").
QUESTION_FRAME = re.compile(r"\b(cost|price|cheaper|expensive|costs)\s+(us\s+)?(to|if (we|i))\s+(order|buy)|\bshould (we|i) (order|buy)\b"
                            r"|\bdo (we|i) need to (order|buy)|\bif (we|i) (order|ordered|buy|bought)\b|\bwhat would ordering\b"
                            r"|\bis it possible to (order|buy)\b|\bwhat'?s the price\b|\bwould it (cost|be)\b"
                            r"|\b(roughly|approximately) what would\b|\bwhat would .{0,60}\bcost\b")
_LATER_ORDER = re.compile(r"\b(order|need|send|buy|get me|tilaa|beställ)\b[^.?!]{0,40}\d")


def _ordered_after(t, pos):
    """True if the text goes on to order something after a negation ("I don't need cream, just order 100 L milk")."""
    return bool(_LATER_ORDER.search(t[pos:]))


def request_type(text):
    """(type, reason) when the wording settles it, else (None, ""). Only ever away from "buy"."""
    t = catalog.fold(normalise(text))
    asks = bool(QUESTION.search(t)) or "check" in t
    if (m := NO_BUY.search(t)) and not _ordered_after(t, m.end()):
        if asks or DRY_RUN.search(t):
            return "question", "it asks about prices or stock and says not to order"
        return "other", "the request says not to order"
    if CANCEL.search(t):
        return "change", "it asks to cancel, stop, change or return an order"
    if (m := ABORT.search(t)) and not _ordered_after(t, m.end()):
        return "other", "the requester withdrew the request in the same message"
    if asks and QUESTION_FRAME.search(t):
        return "question", "it asks what ordering would cost or whether to order: a question, not an order"
    if OUT_OF_SCOPE.search(t):
        return "other", "it asks for something other than a purchase"
    if DRY_RUN.search(t):
        return "question", "it asks for a dry run or a price check, not an order"
    if asks and not ORDER_VERB.search(t):
        return "question", "it's a question, not an order"
    return None, ""


def conditional(text):
    m = CONDITIONAL.search(catalog.fold(normalise(text)))
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


ALLERGY += [
    (r"\bcmpa\b|milk[- ]?(protein )?al+erg\w*|cow'?s? milk( protein)? (al+erg\w*|intoleran\w*)|non[- ]dairy|plant[- ]based"
     r"|mjolkallerg\w*|mjölkallerg\w*|allergia maido\w*|lehmanmaito\w*|lehmänmaito\w*|maito\w*allerg\w*"
     r"|(milk|dairy)[- ]allergic", ["AM"]),
]
# "allergic to X", "X allergy" (with words in between: "severe dairy allergy"), "allergies: X", "avoid: X", "can't eat X"
ALLERGEN_WORDS = [(r"cow'?s? milk|milk|dairy|maito\w*|mjölk|lactose protein", ["AM"]),
                  (r"gluten|wheat|vehnä\w*|rye|barley", ["AW"]), (r"peanuts?|maapähkinä\w*", ["AP"]),
                  (r"(tree )?nuts?|almonds?|hazelnuts?|cashews?|walnuts?|pecans?|pistachios?|pähkinä\w*", ["AN", "AP"]),
                  (r"eggs?|kananmuna\w*|muna\w*", ["AE"]), (r"soy[a]?|soija\w*", ["AY"]), (r"fish|kala\w*", ["AF"]),
                  (r"sesame|seesam\w*", ["AS"])]
_ALLERGY_FRAMES = [r"al+ergic to (?:\w+[ -]){{0,3}}?(?:{w})\b", r"\b(?:{w})(?:[ -]\w+){{0,2}}[ -]al+erg\w*",
                   r"al+erg(?:y|ies|ia)\s*(?::|to|for)\s*(?:\w+[ -]){{0,3}}?(?:{w})\b", r"\bavoid\s*:?\s*(?:\w+[ -]){{0,2}}?(?:{w})\b",
                   r"(?:can'?t|cannot|must not|mustn'?t) (?:eat|have|tolerate) (?:any )?(?:{w})\b", r"\ballerg\w*\s+(?:{w})\b"]


def allergies(text):
    """Allergen codes the request says must be avoided ("for a guest with a milk allergy", "celiac ward")."""
    t = catalog.fold(normalise(text))
    codes = set()
    for pat, cs in ALLERGY:
        if re.search(pat, t):
            codes |= set(cs)
    for words, cs in ALLERGEN_WORDS:
        if any(re.search(f.format(w=words), t) for f in _ALLERGY_FRAMES):
            codes |= set(cs)
    return sorted(codes)


# ---- Quantities and units -------------------------------------------------------------------------
VOLUME = {"l": 1, "litre": 1, "litres": 1, "liter": 1, "liters": 1, "litra": 1, "litraa": 1, "litran": 1,
          "dl": 0.1, "decilitre": 0.1, "decilitres": 0.1, "deciliter": 0.1, "deciliters": 0.1,
          "cl": 0.01, "centilitre": 0.01, "centilitres": 0.01, "ml": 0.001, "millilitre": 0.001, "millilitres": 0.001}
MASS = {"kg": 1, "kilo": 1, "kilos": 1, "kiloa": 1, "kilogram": 1, "kilograms": 1, "g": 0.001, "gram": 0.001, "grams": 0.001}
COUNT = {"pcs", "pieces", "piece", "units", "unit", "kpl", "st", "cartons", "carton", "boxes", "box", "packs", "pack",
         "bottles", "bottle", "crates", "crate", "bags", "bag", "cans", "trays", "tray"}
SERVINGS = {"cups", "cup", "glasses", "glass", "servings", "serving", "portions", "mugs", "bowls", "spoons"}
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
        try:
            value = _value(num)
        except (ValueError, OverflowError):
            continue
        if word in VOLUME or word in MASS:
            # A small size followed by a container or pack word is a pack size ("1 L cartons", "20 L novobox");
            # "200 L Novobox milk" is an amount.
            small = value * (VOLUME.get(word) or MASS.get(word)) <= 25
            if small and re.match(r"\s*(cartons?|boxes?|bottles?|packs?|cups?|bricks?|novobox\w*|bag[- ]in[- ]box|portion)", after) \
                    or small and re.search(r"(cartons?|boxes?|bottles?|packs?|cups?)\s+of\s*$", t[max(0, m.start() - 16):m.start()]):
                continue
            unit, factor = ("L", VOLUME[word]) if word in VOLUME else ("KG", MASS[word])
            kind = "measure"
        elif word in COUNT:
            unit, factor, kind = "PCS", 1, "count"
        elif word in BAD_UNITS:
            unit, factor, kind = None, None, "bad_unit"
        elif word in SERVINGS:
            unit, factor, kind = None, None, "serving"
        else:
            continue
        out.append({"value": value, "unit": unit, "factor": factor, "kind": kind, "raw": m.group(0).strip(),
                    "negative": bool(neg), "start": m.start(), "end": m.end()})
    return out


def quantity_problem(text):
    """Why the quantity can't be ordered as written, or ""."""
    text = normalise(text)
    t = catalog.fold(text)
    if re.search(r"\b(nan|inf|infinity)\s*(l|litres?|liters?|kg|units?|pcs)\b", t):
        return "the quantity isn't a number"
    u = r"(l|litres?|liters?|kg|kilos?|kilograms?|units?|pcs|pieces)\b"
    if re.search(r"\d(\.\d+)?\s*(e[+-]?|\^|\*\*)\s*\d+\s*" + u, t) or re.search(r"\d\.\d+\.\d", t):
        return "write the quantity as a plain number"
    big = any(_value(m.group(2)) > 25 for m in re.finditer(r"(\d+)\s*(?:x|\*)\s*(\d+(?:[.,]\d+)?)\s*(?:l|litres?|liters?|kg|kilos?)\b", t))
    if big or re.search(r"\b(l|litres?|liters?|kg)\s*(x|\*)\s*\d", t):  # "3 x 100 L", "100 L x 3" (not "4x5 kg" packs)
        return "the quantity is a multiplication: say the total amount"
    if re.search(r"\bbetween \d[\d,. ]*\s*(and|-)\s*\d|\d\s*(to|-)\s*\d+\s*" + u, t):
        return "the quantity is a range: say one amount"
    if re.search(r"\b(per|a|each|every)\s+(day|week|month)\b|\b(per|each|every)\s+(kitchen|hotel|site|guest|person)\b"
                 r"|/\s*(day|week)\b|\bfor (each|every) (of )?|\beach for\b", t):
        return "the quantity is a rate or per-site amount: say the total for this delivery"
    if re.search(r"\b(or|tai|eller)\s+\d+(?![\d-])|\bactually\b.{0,25}\d|\+\s*\d+\s*%|\bplus (some|a few|extra|spare)\b|\bor so\b", t):
        return "the quantity isn't settled: say one amount"
    qs = quantities(text)
    for q in qs:
        if q["kind"] == "bad_unit":
            return f"“{q['raw']}” isn't a unit food is sold in: use litres, kilograms or pieces"
        if q["kind"] == "serving":
            return f"“{q['raw']}” is a number of servings: say how many litres, kilograms or packs"
        if q["value"] > 10_000_000:
            return f"“{q['raw'][:20]}…” isn't a realistic quantity"
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
            w = m.group(2)
            if len(m.group(1)) < 6 and (w in catalog.vocabulary() or catalog._stem(w) in catalog.vocabulary()) \
                    and w not in VOLUME | MASS:
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
    qs = _order_quantities(text)
    if len(qs) > 1:
        return True
    if len(qs) == 1:  # "100 L milk by Monday. And 300 eggs.": a bare count of another product
        t = catalog.fold(text)
        for m in re.finditer(r"(?<![\d.,%x-])(\d+)\s+([a-zåäö]+)", t):
            if qs[0]["start"] <= m.start() < qs[0]["end"] or len(m.group(1)) >= 6:  # 6 digits: a product number
                continue
            w = m.group(2)
            if w not in VOLUME | MASS and w not in COUNT and (w in catalog.vocabulary() or catalog._stem(w) in catalog.vocabulary()):
                return True
    return False


# ---- Dates -----------------------------------------------------------------------------------------
def date_problem(text, deliver_by, today):
    """(reason, gate): an invalid or past date is unclear (gate 1); a window Valio can't meet is impossible (gate 2)."""
    t = catalog.fold(text)
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", t):
        try:
            date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return f"{m.group(0)} isn't a real date", "clear"
    for m in re.finditer(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})?(?!\d)", t):
        try:
            date(int(m.group(3) or today.year), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return f"{m.group(0)} isn't a real date", "clear"
    try:
        if deliver_by and date.fromisoformat(deliver_by) < today:
            return f"the delivery date {deliver_by} has already passed", "clear"
    except ValueError:
        return f"{deliver_by} isn't a real date", "clear"
    if re.search(r"\b(in|within)\s+\d+\s*(minutes?|mins?|hours?|h)\b", t):
        return "Valio delivers on delivery days, not within minutes or hours", "possible"
    if re.search(r"\b(yesterday|last (week|monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b", t):
        return "the delivery date has already passed", "clear"
    if re.search(r"\b(christmas|xmas|easter|juhannus|midsummer|end of (the )?month|before the weekend|this weekend|next week"
                 r"|next (monday|tuesday|wednesday|thursday|friday|saturday|sunday)|on the \d+(st|nd|rd|th)?)\b", t):
        return "the delivery date isn't clear: give the date (for example 2026-11-16)", "clear"
    days = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.?)"
    if re.search(days + r"\s+(or|tai|eller)\s+" + days, t):
        return "two delivery dates were given: choose one", "clear"
    wd = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t)
    if wd and deliver_by and re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.", t):
        try:
            if date.fromisoformat(deliver_by).strftime("%A").lower() != wd.group(1):
                return f"{wd.group(1).title()} and {deliver_by} don't match: which one?", "clear"
        except ValueError:
            pass
    try:
        if deliver_by and (date.fromisoformat(deliver_by) - today).days > 21:
            return (f"{deliver_by} is more than three weeks away. Valio's availability covers the coming days, so the "
                    "goods would arrive weeks early: order closer to the date"), "possible"
    except ValueError:
        pass
    return "", None


def date_stated(text):
    """Does the request say when (a weekday, a date, today/tomorrow, "in 3 days")? If not, the date is assumed."""
    t = catalog.fold(normalise(text))
    return bool(re.search(r"\d{4}-\d{2}-\d{2}|\b\d{1,2}\.\d{1,2}\.|\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday"
                          r"|today|tonight|tomorrow|asap|right now|immediately|in \d{1,2} days?)\b|\b(maanantai|tiistai|keskiviikko"
                          r"|torstai|perjantai|lauantai|sunnuntai|huomen|måndag|tisdag|onsdag|torsdag|fredag|lördag|söndag|imorgon)"
                          r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b"
                          r"|\b\d{1,2}(st|nd|rd|th)?\s+(of\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", t))


INHERENT = {"butter": "AM", "voi": "AM", "milk": "AM", "maito": "AM", "cream": "AM", "kerma": "AM", "cheese": "AM",
            "juusto": "AM", "yoghurt": "AM", "yogurt": "AM", "jogurtti": "AM", "quark": "AM", "rahka": "AM",
            "egg": "AE", "eggs": "AE", "kananmuna": "AE"}
_PLANT = re.compile(r"\b(oat|kaura\w*|soy|soya|soija\w*|almond|coconut|rice|plant|vegan|vegetable|kasvi\w*|non[- ]dairy"
                    r"|dairy[- ]free|egg[- ]free|replacer|substitute|alternative)\b")


def inherent_allergen(product, banned):
    """(word, code) when the product asked for is itself a banned allergen ("butter" with a milk ban), else None."""
    t = catalog.fold(product)
    if _PLANT.search(t):
        return None
    for w in re.findall(r"[a-zåäö]+", t):
        if INHERENT.get(w) in banned:
            return w, INHERENT[w]
    return None


def allergy_unclear(text):
    """An allergy is mentioned but no allergen could be read ("for an allergic guest (milk)"): ask, don't guess."""
    t = catalog.fold(normalise(text))
    t = re.sub(r"lactose[- ]intoleran\w*|laktoosi-?intoleran\w*", " ", t)
    if re.search(r"al+erg\w*|anaphyla\w*|intoleran\w*|\breacts? (badly )?to\b|allergi\w*", t) and not allergies(text):
        return "an allergy is mentioned, but not which allergen it is: say which (for example “milk allergy”)"
    return ""


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


GAMING = re.compile(r"\b(stay|keep (it|this|them|the (total|order|amount|price|sum))|remain)\s+(just\s+)?(under|below)\b"
                    r"|\b(to |and )?avoid (the |any )?(approval|limit|escalation|sign[- ]?off|approver)\b|\bsub-?orders?\b"
                    r"|\bsplit (this|it|the order|across|into)\b|\bqueue another\b|\bparallel requests\b"
                    r"|\bwithout (logging|approval|escalation|re-?signing)\b|\b(doesn'?t|does not|won'?t|wouldn'?t) need (an |any )?approval"
                    r"|\b(doesn'?t|does not|don'?t|won'?t) have to approve\b|\b(no need to|don'?t) (bother|ask|involve) the (approver|chef|manager)"
                    r"|\bunder the radar\b|\bjust (under|below) the (auto |approval )?limit\b|\b(two|2|three|3|several|separate) orders of\b"
                    r"|\banother \d+\s*\w*\s*(litres?|kg|l)?\s*(in|after) (an|one|\d+) (hour|minute)")


def policy_gaming(text):
    m = GAMING.search(catalog.fold(normalise(text)))
    return f"it asks the agent to work around a limit (“{m.group(0)}”)" if m else ""
