"""Buyer-side purchasing agent and the gateway's offer/order logic.

The agent turns a request into a structured intent, looks up a short list of candidates, asks the
Valio APIs for price and availability only for those, builds good-better-best options, takes a
signed offer for the best one and submits the order. The gateway's policy engine decides whether the
order goes through, needs a human, or is blocked.
"""
import contextvars
import math
import re
import sqlite3
import uuid
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import ai, catalog, gates, policy, request_checks, security, signing, store
from .config import DEMO_CLOCK, MAX_OFFER_LINES, OFFER_TTL_MINUTES, TIMEZONE, WEIGHT_SALES_UNITS
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


# Demo clock (see config.DEMO_CLOCK). A context variable so one demo run can switch back to Valio's real
# clock (the "passed cut-off" trap) without affecting other requests.
CLOCK = contextvars.ContextVar("demo_clock", default=DEMO_CLOCK)

# The mandate the calling agent's token is bound to (set per request by main.py; None = in-process caller,
# such as the demo page or tests). An agent holding the hotel's token can't quote, order or read under
# the hospital's mandate (benchmark AUDIT_RECORD cross-tenant; winner reports' "whose authority?" question).
CALLER = contextvars.ContextVar("caller_mandate", default=None)


def caller_refusal(mandate_id, ref="security"):
    """An error if the calling agent's token is bound to a different mandate, else None (and nothing logged)."""
    bound = CALLER.get()
    if bound and bound != mandate_id:
        store.log(ref, f"agent:{bound}", "security.mandate_mismatch", {"token_for": bound, "tried": mandate_id})
        return f"this agent token acts for {bound}, not {mandate_id}"
    return None


def _clock_shift():
    """How far the demo clock is behind real time (zero when it's off)."""
    demo = _when(CLOCK.get())
    return (datetime.now(timezone.utc) - demo) if demo else timedelta(0)


def _deadline(s):
    """An order cut-off as it applies now: shifted by the demo clock, if one is set."""
    d = _when(s)
    return d + _clock_shift() if d else None


def _passed(deadline, now_s):
    """True if an order deadline is before the API's own clock (compared as times, not strings)."""
    d, n = _deadline(deadline), _when(now_s)
    if deadline and d is None:
        return True  # a cut-off we can't read counts as passed: fail closed
    return bool(d and n and d < n)


def _amount(x):
    """A schedule line's quantity as a finite number ≥ 0, or 0 if Valio sent something else (NaN, inf, text)."""
    v = x.get("availableAmount", 0)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return 0
    return v


def _price(pr):
    """Valio's catalog price as a finite positive number, or None."""
    v = ((pr or {}).get("catalogPrice") or {}).get("value")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        return None
    return v


def _valio(call, *args):
    """Call Valio; on any failure (HTTP 500, 429 after retries, timeout) return (None, reason) so the caller
    gives a clean refusal instead of crashing."""
    try:
        return call(*args), None
    except Exception as e:  # noqa: BLE001 - any upstream failure must fail closed
        return None, f"Valio didn't answer ({type(e).__name__}): nothing was ordered, try again"


def check_quote_input(deliver_by, requested_quantity, requested_unit, lines, mandate):
    """Refuse malformed quote fields before anything is priced or signed (an agent's typo must not
    become a signed offer)."""
    if not isinstance(deliver_by, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", deliver_by):
        return "deliver_by must be a date like 2026-11-18"
    try:
        d = date.fromisoformat(deliver_by)
    except ValueError:
        return f"deliver_by {deliver_by} is not a real date"
    today = (datetime.now(timezone.utc) - _clock_shift()).astimezone(ZoneInfo(TIMEZONE)).date()
    if d < today:
        return f"deliver_by {deliver_by} is in the past"
    if requested_quantity is not None and (isinstance(requested_quantity, bool) or not isinstance(requested_quantity, (int, float))
                                           or not math.isfinite(requested_quantity) or requested_quantity <= 0):
        return "requested_quantity must be a positive number"
    if requested_unit not in (None, "") and str(requested_unit).upper() not in ("L", "KG", "PCS", "ST", "KPL"):
        return "requested_unit must be L, KG or PCS"
    allowed = set(mandate.get("substitutions", {}))
    for l in lines if isinstance(lines, list) else []:
        sub = (l or {}).get("substitution")
        if sub is not None and sub not in allowed:
            return f"substitution {sub!r} is not a type this mandate knows ({', '.join(sorted(allowed))})"
    return None


def _number(s):
    """'1,000' / '1 000' / '1.000' -> 1000; '2,5' / '2.5' -> 2.5 (Finnish and English styles). None if not a number."""
    s = s.strip().replace("\u00a0", " ")
    try:
        if re.fullmatch(r"\d{1,3}([ ,.]\d{3})+", s):
            return float(re.sub(r"[ ,.]", "", s))
        return float(s.replace(" ", "").replace(",", "."))
    except (ValueError, OverflowError):
        return None  # "100.0.0": request_checks.quantity_problem explains

UNIT_WORDS = {"L": "litres", "KG": "kg", "PCS": "pieces"}
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
    t = catalog.fold(text)
    q = re.search(r"(\d[\d,. ]*)\s*(litres?|liters?|l|kg|kilos|kilograms|pcs|pieces|units)\b", t)
    quantity = _number(q.group(1)) if q else None
    unit = UNITS.get(q.group(2), "PCS") if q else "PCS"
    oq = request_checks.order_quantity(text)
    if oq:  # the deterministic reading knows dl/cl/ml, packs and pack sizes ("0.01 cartons of 1 L")
        quantity, unit, t_after, q = oq["quantity"], oq["unit"], t[oq["end"]:], None
    elif not q and (n := re.search(r"\b(\d[\d,.]*)\b", t)) and (u := re.search(r"\b(litres?|liters?|kg|kilos|kilograms|pcs|pieces|units)\b", t)):
        quantity, unit = _number(n.group(1)), UNITS.get(u.group(1), "PCS")  # "1000 ... milk liter": unit at the end
        t_after = t[n.end():]
        q = None
    elif not oq:
        t_after = None
    attrs = [code for pat, code in ATTRS.items() if re.search(pat, t)]
    deliver_by = None
    if m := re.search(r"\d{4}-\d{2}-\d{2}", t):
        try:
            deliver_by = date.fromisoformat(m.group())
        except ValueError:
            deliver_by = None  # not a real date: request_checks.date_problem reports it
    elif m := re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})?(?!\d)", t):  # Finnish style: 16.11. / 16.11.2026
        try:
            deliver_by = date(int(m.group(3) or today.year), int(m.group(2)), int(m.group(1)))
            if not m.group(3) and deliver_by < today:
                deliver_by = deliver_by.replace(year=today.year + 1)
        except ValueError:
            deliver_by = date(1900, 1, 1)  # not a real date: date_problem says so
    elif m := (re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(st|nd|rd|th)?\b", t)
               or re.search(r"\b(\d{1,2})(st|nd|rd|th)?\s+(?:of\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", t)):
        g = m.groups()
        mon, day = (g[0], g[1]) if not g[0].isdigit() else (g[2], g[0])
        months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
        try:
            deliver_by = date(today.year, months.index(mon) + 1, int(day))
            if deliver_by < today:
                deliver_by = deliver_by.replace(year=today.year + 1)
        except ValueError:
            deliver_by = date(1900, 1, 1)
    elif re.search(r"\b(today|tonight|asap|right now|immediately|this (morning|afternoon|evening))\b", t):
        deliver_by = today  # Valio's earliest delivery day is later: offered as a later delivery for a person to accept
    elif m := re.search(r"\bin (\d{1,2}) days?\b", t):
        deliver_by = today + timedelta(days=int(m.group(1)))
    elif "tomorrow" in t:
        deliver_by = today + timedelta(days=1)
    else:
        for i, d in enumerate(WEEKDAYS):
            if re.search(rf"\b{d}\b", t):
                deliver_by = today + timedelta(days=(i - today.weekday()) % 7 or 7)
    stripped = t
    if t_after is not None:
        stripped = re.split(r"\b(?:by|for|to|before|until|from|delivered|so|and|that|which)\b|[,;!?(]|(?<!\d)\.(?!\d)", t_after)[0]
    elif q:
        # The product is what follows the quantity, up to a clause like "by Monday" or "for the school".
        stripped = re.split(r"\b(?:by|for|to|before|until|from|delivered|so|and|that|which)\b|[,;!?]|(?<!\d)\.(?!\d)", t[q.end():])[0]
    for pat in ATTRS:
        stripped = re.sub(pat, " ", stripped)
    words = [w for w in re.findall(r"[a-zåäö]+", stripped)
             if w not in STOP and w not in WEEKDAYS and w not in UNITS and w not in QUESTION_WORDS
             and w not in ("fresh", "uht", "tuore")]  # shelf life is captured separately below
    fat = re.search(r"(\d+(?:[.,]\d+)?)\s*%", t)
    shelf = "fresh" if re.search(r"\b(not|no|non|without|except)[- (]*(uht|long[- ]?life)\b", t) else \
        "long_life" if re.search(r"\buht\b|long[- ]life|pitkään säilyvä", t) else \
        "fresh" if re.search(r"\bfresh\b|tuore", t) else None
    if re.search(r"\b(cancel|change|modify|amend)\b", t) and re.search(r"\border\b", t):
        request_type = "change"
    elif (t.rstrip().endswith("?") or re.match(r"\s*(what|how|which|is|are|do|does|can you tell)\b", t)) \
            and not re.search(r"\b(need|order|buy|deliver)\b", t):
        request_type = "question"
    elif quantity or any(w in catalog.vocabulary() or catalog._stem(w) in catalog.vocabulary() for w in words):
        request_type = "buy"
    else:
        request_type = "other"  # no quantity and no product word Valio sells: not a purchasing request
    # No guessing: if we can't tell what to buy, say so instead of defaulting to some product.
    return {"request_type": request_type, "ignored_terms": [], "product": " ".join(words[:3]), "quantity": quantity, "unit": unit,
            "attributes": attrs, "deliver_by": (deliver_by or today + timedelta(days=3)).isoformat(),
            "fat_pct": float(fat.group(1).replace(",", ".")) if fat else None, "shelf_life": shelf, "raw": text}


QUESTION_WORDS = {"what", "how", "much", "many", "which", "is", "are", "do", "does", "cost", "costs", "there", "any",
                  "available", "have", "you", "in", "stock", "tell", "me", "can", "price", "prices"}

# Words that describe the situation, not the product ("for the school breakfast", "hotel", "guests").
CONTEXT_WORDS = {"school", "breakfast", "lunch", "dinner", "hotel", "kitchen", "guests", "guest", "people", "event",
                 "weekend", "morning", "evening", "asap", "urgent", "urgently", "about", "around", "approximately",
                 "product", "products", "item", "items", "stuff", "things", "give", "get", "send", "bring",
                 "some", "more", "extra", "small", "top", "up", "also", "us", "me", "my", "is", "are", "be", "can",
                 "you", "it", "this", "with", "in", "at", "per", "day", "days", "total", "litre", "kilo", "piece",
                 "pack", "packs", "box", "boxes", "carton", "cartons", "bottle", "bottles"}


SPEC_WORDS = {"fresh", "chilled", "uht", "long", "life", "whole", "light", "skimmed", "semi", "fat", "low", "full",
              "tuore", "kevyt", "rasvaton", "taysmaito", "täysmaito"}


FUNCTION_WORDS = {"please", "kindly", "would", "could", "should", "like", "want", "wants", "need", "needs", "needed",
                  "order", "ordering", "buy", "send", "deliver", "delivered", "delivery", "deliveries", "bring", "make",
                  "with", "from", "into", "onto", "this", "that", "these", "those", "them", "they", "their", "there",
                  "here", "have", "has", "had", "will", "shall", "just", "only", "also", "very", "much", "many", "each",
                  "every", "plain", "regular", "normal", "standard", "basic", "ordinary", "usual", "same", "again",
                  "start", "starting", "next", "week", "weekly", "today", "tomorrow", "tonight", "morning", "afternoon",
                  "evening", "night", "date", "time", "later", "soon", "quickly", "fast", "liter", "litra", "litraa",
                  # courtesy: "Order 100 L milk by Monday, thanks" must not be sent back
                  "thanks", "thank", "thx", "cheers", "hello", "hey", "regards", "tack", "kiitos", "sure", "great"}
# Finnish and Swedish words that frame a request (weekdays, "order", "deliver"); matched as prefixes for inflection.
FRAME_PREFIXES = ("maanantai", "tiistai", "keskiviiko", "torstai", "perjantai", "lauantai", "sunnuntai", "huomen",
                  "toimitu", "tilaa", "tarvit", "mennessä", "måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag",
                  "söndag", "imorgon", "beställ", "behöver", "leverans", "kiitos", "please")


def _known(w, vocab):
    """In Valio's product vocabulary, allowing Finnish inflection ("maitoa" -> "maito")."""
    if w in vocab or catalog._stem(w) in vocab:
        return True
    return any(w.startswith(v) and len(w) - len(v) <= 4 for v in (w[:k] for k in range(5, len(w))) if v in vocab)


def unmatched_terms(text, intent, covered_texts=(), strict=False):
    """Words in the request that the reading dropped.

    A request like "chili cheese chicken nuggets and 100 litres of milk" must not quietly become an order for
    milk alone, and "dehydrated astronaut plutonium 50 kg butter" must not become a butter order. Flagged:
    (1) Valio product words that aren't covered by the products found, and (2) words in the order itself
    (before "by Monday", "for the school ...") that are neither product words nor ordinary request words.
    Words in exclusions ("do NOT send ...") and a "for ..." purpose clause are ignored."""
    t = catalog.fold(text)
    for e in request_checks.EXCLUDE.finditer(t):
        t = t.replace(e.group(0), " ")
    whole = t
    parts = re.split(r"\bfor\b", t, maxsplit=1)
    if len(parts) == 2 and re.search(r"\d", parts[0]):
        t = parts[0]  # "... by Wednesday for the school breakfast": the purpose clause isn't the product
    clause = re.split(r"\b(?:by|until|before|on)\b|[(\[.;!?—–]", t, maxsplit=1)[0]
    for pat in ATTRS:
        t = re.sub(pat, " ", t)
        clause = re.sub(pat, " ", clause)
    covered = {catalog._stem(w) for w in re.findall(r"[a-zåäö]+", catalog.fold(intent.get("product") or ""))}
    covered |= SPEC_WORDS  # fat level and fresh/long-life are captured as fat_pct and shelf_life
    cover_words = set()
    for ct in covered_texts:
        cover_words |= set(re.findall(r"[a-zåäö]+", catalog.fold(ct)))
    vocab = catalog.vocabulary()
    ignore = STOP | set(WEEKDAYS) | set(UNITS) | CONTEXT_WORDS | QUESTION_WORDS | FUNCTION_WORDS \
        | set(request_checks.VOLUME) | set(request_checks.MASS) | request_checks.COUNT | {"lactose", "free"}

    def is_covered(w):
        s_ = catalog._stem(w)
        return (s_ in covered or any(c and s_.startswith(c) for c in covered) or w in cover_words
                or any(w.startswith(c) and len(w) - len(c) <= 4 for c in cover_words if len(c) >= 4))

    out = set()
    for w in re.findall(r"[a-zåäö]+", t):  # (1) product words dropped anywhere in the request
        if len(w) < 3 or w in ignore or w.startswith(FRAME_PREFIXES) or is_covered(w):
            continue
        if w in vocab or catalog._stem(w) in vocab:
            out.add(w)
    for w in re.findall(r"[a-zåäö]+", clause):  # (2) unknown words inside the order itself
        if len(w) < 4 or w in ignore or w in request_checks.GENERIC or w.startswith(FRAME_PREFIXES) or is_covered(w):
            continue
        if not _known(w, vocab):
            out.add(w)
    if strict:  # (3) rule-based reading: every other sentence must be understood too (doubt is a no)
        sentences = [x for x in re.split(r"(?<!\d)[.!?;:\n]+|[.!?;:\n]+(?!\d)", whole) if x.strip()]
        order_at = next((i for i, x in enumerate(sentences) if re.search(r"\d", x)), 0)
        for x in sentences[:order_at] + sentences[order_at + 1:]:
            for w in re.findall(r"[a-zåäö]+", x):
                if len(w) < 4 or w in ignore or w in request_checks.GENERIC or w.startswith(FRAME_PREFIXES) \
                        or is_covered(w) or _known(w, vocab):
                    continue
                out.add(w)
    return sorted(out)


PACK_WORDS = {"cartons", "carton", "boxes", "box", "packs", "pack", "bottles", "bottle", "cups", "crates", "crate",
              "bags", "bag", "cans", "trays", "tray", "units", "unit"}


def count_kind(text):
    """For a piece count: "200 cartons/packs/boxes" counts sales units; "200 eggs" or "200 pcs" counts items."""
    oq = request_checks.order_quantity(text)
    word = (oq or {}).get("raw", "").split()[-1:] or [""]
    return "packs" if re.sub(r"^\d[\d,. ]*", "", word[0]) in PACK_WORDS else "items"


def _per_unit(p, price, unit, kind="packs"):
    """How much of the requested unit one sales unit holds (e.g. 20 L per carton, 10 eggs per pack)."""
    if unit == "PCS":
        if kind == "items":
            return float(catalog.pieces(p) or 1)  # unknown contents: 1, flagged below for a person to check
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
    per_unit = _per_unit(p, price, intent["unit"], intent.get("count_kind", "packs"))
    if intent["unit"] == "PCS" and intent.get("count_kind") == "items":
        n = catalog.pieces(p)
        row["pieces_assumed"] = n is None
        row["issues"].append(f"{n} pieces per {p['salesUnit']}" if n else "pieces per pack not in the data: assumed 1")
    if not per_unit:
        row["status"] = "unit_mismatch"
        row["issues"].append(f"can't convert {p['salesUnit']} to {intent['unit']}")
        return row
    if not intent.get("quantity") and (intent.get("request_type") or "buy") == "buy":
        # Defence in depth: gate 1 already asks back, but a purchase without a quantity is never "one pack".
        row["status"] = "no_quantity"
        row["issues"].append("no quantity given")
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
        # A later delivery is a compromise only a person may accept (gate 5), so remember the earliest one.
        later = [s for s in a.get("scheduleLines", []) if not s.get("salesBlock") and s.get("availableDate")
                 and s["availableDate"] > intent["deliver_by"]]
        if later and sum(s["availableAmount"] for s in later) >= need:
            row["late_date"] = min(s["availableDate"] for s in later)
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
        "cutoff": ((f"passed at {cut[:16].replace('T', ' ')}" if _passed(cut, now_s) else f"order by {cut[:16].replace('T', ' ')}")
                   + (" (demo clock)" if CLOCK.get() else "")) if cut else "no cut-off given",
        "price": f"{'customer net price' if price.get('netPriceFound') else 'catalog price'}, valid until {cp.get('validTo', '')[:10]}",
        "data_gaps": gaps,
        "reliability_warning": p.get("reliability") is not None and p["reliability"] < 0.95,
        "reliability": (f"{p['reliability']:.0%} on time" if p.get("reliability") is not None
                        else "unverified: no delivery history"),
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
            "stockConfirmed": r.get("stock_confirmed", True), "piecesAssumed": r.get("pieces_assumed", False)}


def _kind(r):
    """What makes two products interchangeable in one order line: fat %, shelf life, lactose-free or not."""
    p = r["product"]
    lactose = p.get("lactoseGPer100")
    return (p.get("fatPct"), p.get("longLife"), None if lactose is None else lactose == 0)


def _kind_text(k):
    fat, long_life, lactose_free = k
    parts = [f"fat {fat:g} %" if fat is not None else "fat not given", "long-life" if long_life else "fresh"]
    if lactose_free is not None:
        parts.append("lactose-free" if lactose_free else "contains lactose")
    return ", ".join(parts)


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
        # Split across products of the SAME kind (fat %, fresh/long-life, lactose-free or not), best score first.
        # Mixing 3.5 % low-lactose milk with fat-free and coffee milk to reach 1,000 L is not what anyone asked for.
        usable = [r for r in rows if r["status"] in ("ok", "partial")]
        groups = {}
        for r in usable:
            groups.setdefault(_kind(r), []).append(r)
        def cover(g):
            return sum(r.get("units", 0) * r["per_unit"] for r in g)
        need = intent["quantity"] or 0
        best = max(groups.values(), key=lambda g: (cover(g) >= need, -min(r["price_per_req_unit"] for r in g), cover(g)),
                   default=[])
        usable = sorted(best, key=lambda r: -r["score"])
        remaining, lines = need, []
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
            kind = _kind(usable[0])
            options.append({"label": "Split order", "why": "no single product covers the full quantity in time; "
                            f"split across products of the same kind ({_kind_text(kind)})",
                            "lines": lines, "total_eur": round(sum(l["lineTotal"] for l in lines), 2),
                            "covers": covers, "partial": covers < (intent["quantity"] or 0)})
    return options


def create_offer(mandate, option, intent):
    """Lock list prices and availability for a short time and sign the offer."""
    offer_id = "ofr_" + uuid.uuid4().hex[:12]
    # The price lock ends at the earliest of: 15 minutes, any line's order deadline, any line's price validity.
    limits = [(datetime.now(timezone.utc) + timedelta(minutes=OFFER_TTL_MINUTES), f"{OFFER_TTL_MINUTES} min offer window")]
    for l in option["lines"]:
        limits += [(t, why) for t, why in ((_deadline(l.get("orderDeadline")), f"order cut-off for {l['productId']}"),
                                           (_when(l.get("priceValidTo")), f"price validity for {l['productId']}")) if t]
    exp, reason = min(limits, key=lambda x: x[0])
    exp = exp.astimezone(timezone.utc)
    payload = {
        "offer_id": offer_id, "issuer": "valio-aimo-trade-gateway (demo)", "customer": mandate["customer"],
        "mandate_id": mandate["mandate_id"], "currency": "EUR", "price_basis": "catalog price, fixed (not negotiated)",
        "lines": [{k: l.get(k) for k in ("productId", "name", "units", "salesUnit", "perUnit", "unitPrice", "lineTotal",
                                         "deliveryDate", "reliability", "substitution", "orderDeadline",
                                         "priceValidTo", "stockConfirmed", "piecesAssumed", "trust", "evidence")}
                  for l in option["lines"]],
        "total_eur": option["total_eur"], "deliver_by": intent["deliver_by"],
        # What the buyer asked for, sealed into the signature: the order is checked against this, not against
        # whatever the agent claims later.
        "requested_quantity": intent.get("quantity"), "requested_unit": intent.get("unit"),
        "issued_at": store.now(), "expires_at": exp.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "expiry_reason": reason,
        # Said inside the signature, so nobody can mistake a demo for a real purchase:
        "clock": (f"replay of Valio's data snapshot as of {CLOCK.get()} (cut-offs shifted)" if CLOCK.get()
                  else "Valio's real clock"),
        "commitment": "simulated: the challenge has no Valio order API, so 'placed' means accepted by this gateway",
    }
    jws = signing.sign(payload)
    store.save_offer(offer_id, mandate["mandate_id"], payload, jws)
    store.log(offer_id, "gateway", "offer.signed", {"total_eur": payload["total_eur"], "lines": len(payload["lines"]),
                                                     "expires_at": payload["expires_at"]})
    return payload, jws


# One commit or approval at a time, across all server processes on the machine (store.commit_lock): the "offer
# used once", "24 h spend" and "still pending" checks read the store and then write to it. The database also
# refuses a second order for one offer, and approvals are a compare-and-set on "pending_approval".
_commit = store.commit_lock


def place_order(offer_jws, idempotency_key, mandate, context, client=None, actor="agent"):
    """Gateway side of an order: verify the signed offer, re-check prices, apply the mandate."""
    with _commit():
        return _place_order(offer_jws, idempotency_key, mandate, context, client, actor)


def _place_order(offer_jws, idempotency_key, mandate, context, client, actor):
    ok, payload = signing.verify(offer_jws)
    if not ok:
        return None, payload
    if existing := store.order_by_key(idempotency_key):
        if existing["offer_id"] != payload["offer_id"]:
            return None, "idempotency key already used for a different offer: use a new key"
        return existing, "duplicate request: returning the original order (idempotent)"
    if store.order_by_offer(payload["offer_id"]):
        # A signed offer can be committed once. A new idempotency key doesn't make it a new purchase.
        store.log(payload["offer_id"], actor, "security.offer_replayed", {"idempotency_key": idempotency_key})
        return None, "this signed offer was already used for an order: request a new offer"
    if payload["expires_at"] < store.now():
        return None, "offer expired: request a new one"
    client = client or ValioClient()
    fresh, err = _valio(client.prices, mandate["customer"], payload["deliver_by"], [l["productId"] for l in payload["lines"]])
    if err:
        return None, err
    drift = [l["productId"] for l in payload["lines"]
             if abs((_price((fresh or {}).get(l["productId"])) or -1) - l["unitPrice"]) > 0.005]
    if context.get("simulate_price_change"):
        drift = [payload["lines"][0]["productId"]]
    if drift:
        store.log(payload["offer_id"], "gateway", "order.rejected", {"reason": "price changed", "products": drift})
        return None, f"price changed since the offer for {', '.join(drift)}: request a new offer"
    # A quote is not a reservation: stock, cut-off and sales blocks are checked again at commit.
    got, err = _valio(client.availability, mandate["customer"], payload["deliver_by"], [l["productId"] for l in payload["lines"]])
    if err:
        return None, err
    now_s, avail = got
    gone = [p for l in payload["lines"] if (p := _line_problem(l, avail.get(l["productId"]) or {}, now_s, payload["deliver_by"]))]
    if gone:
        store.log(payload["offer_id"], "gateway", "order.rejected", {"reason": "availability changed", "problems": gone})
        return None, f"{'; '.join(gone)}: request a new offer"
    # How much the agent already ordered on its own under this mandate in the last 24 h (anti-smurfing): one SQL sum
    # over every order, not a scan of the latest few.
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    context["auto_spend_24h"] = store.auto_spend_since(mandate["mandate_id"], since)
    verdict = policy.evaluate(mandate, payload, context)
    status = {"auto_approve": "placed", "needs_approval": "pending_approval", "blocked": "blocked"}[verdict["outcome"]]
    order = {"id": "ord_" + uuid.uuid4().hex[:10], "offer_id": payload["offer_id"], "idempotency_key": idempotency_key,
             "status": status, "total": payload["total_eur"], "mandate_id": mandate["mandate_id"], "policy": verdict,
             "record": {"offer": payload, "context": context, "agent": mandate["agent"]}}
    try:
        store.save_order(order)
    except sqlite3.IntegrityError:  # another process got there first
        store.log(payload["offer_id"], actor, "security.offer_replayed", {"idempotency_key": idempotency_key})
        return None, "this signed offer was already used for an order: request a new offer"
    store.log(order["id"], actor, "order.submitted", {"offer_id": payload["offer_id"], "total_eur": payload["total_eur"]})
    store.log(order["id"], "gateway", f"policy.{verdict['outcome']}",
              {"failed_rules": [r["rule"] for r in verdict["rules"] if not r["ok"]],
               "approver": (verdict["approver"] or {}).get("role")})
    return order, None


def _line_problem(line, av, now_s, deliver_by):
    """Why a line can't be delivered as offered, or "". One check for quoting, committing and late approval."""
    pid = line["productId"]
    deliver_by = max(deliver_by, line.get("deliveryDate") or deliver_by)  # a later delivery the buyer's side accepted
    if av.get("salesBlock"):
        return f"{pid} has a sales block"
    if _passed(av.get("closingTime"), now_s):
        return f"{pid}: the order deadline (cut-off) passed at {av.get('closingTime')}"
    sched = [x for x in av.get("scheduleLines", []) if not x.get("salesBlock") and x.get("availableDate")
             and x["availableDate"] <= deliver_by]
    if not sched or sum(_amount(x) for x in sched) < line["units"]:
        return f"{pid} is no longer available in that quantity by {deliver_by}"
    return ""


def quote(mandate_id, deliver_by, lines, client=None, requested_quantity=None, requested_unit=None):
    """Signed fixed-price offer for explicit lines (used by /v1/quotes and the MCP tool).

    Returns (offer, jws, None) or (None, None, error)."""
    if err := caller_refusal(mandate_id):
        return None, None, err
    mandate = policy.load(mandate_id)
    if err := (_check_lines(lines) or check_quote_input(deliver_by, requested_quantity, requested_unit, lines, mandate)):
        store.log("security", "gateway", "security.quote_refused", {"mandate_id": mandate_id, "reason": err})
        return None, None, err
    ids = [str(l["productId"]) for l in lines]
    client = client or ValioClient()
    prices, err = _valio(client.prices, mandate["customer"], deliver_by, ids)
    got, err2 = (None, None) if err else _valio(client.availability, mandate["customer"], deliver_by, ids)
    if err or err2:
        return None, None, err or err2
    now_s, avail = got
    prices = prices or {}
    out = []
    for l in lines:
        pid = str(l["productId"])
        pr, av, p = prices.get(pid), avail.get(pid) or {}, catalog.get(pid)
        if not p or _price(pr) is None:
            return None, None, f"no valid price from Valio for {pid}"
        if problem := _line_problem({"productId": pid, "units": l["units"]}, av, now_s, deliver_by):
            return None, None, problem
        sched = [x for x in av.get("scheduleLines", []) if not x.get("salesBlock") and x.get("availableDate")
                 and x["availableDate"] <= deliver_by]
        # Pack size comes from Valio's data, never from the caller: a 2 L carton is 2 L whatever an agent says.
        unit = (requested_unit or "").upper()
        per = _per_unit(p, pr, unit) if unit in ("L", "KG") else 1.0
        if per is None:
            return None, None, f"{pid}: Valio's data doesn't say how many {unit} one {p['salesUnit']} holds: quote in sales units"
        claimed = l.get("perUnit")
        if claimed is not None and abs(float(claimed) - per) > 0.03 * per:
            store.log("security", "gateway", "security.pack_size_mismatch",
                      {"mandate_id": mandate_id, "productId": pid, "claimed": claimed, "from_valio": per})
            return None, None, f"{pid}: one {p['salesUnit']} holds {per:g} {unit or 'sales unit'}, not {claimed}: request a new offer"
        up = pr["catalogPrice"]["value"]
        out.append({"productId": pid, "name": p["name"], "units": l["units"], "salesUnit": p["salesUnit"],
                    "unitPrice": up, "lineTotal": round(up * l["units"], 2), "perUnit": per,
                    "deliveryDate": min(x["availableDate"] for x in sched), "reliability": p["reliability"],
                    "substitution": l.get("substitution"), "orderDeadline": av.get("closingTime"),
                    "priceValidTo": (pr["catalogPrice"].get("validTo") or "")[:10] or None,
                    "stockConfirmed": av.get("logisticModel") == "Stock",
                    "trust": trust_flags(p, pr, av, sum(_amount(x) for x in sched), now_s),
                    "evidence": catalog.evidence(p)})
    option = {"lines": out, "total_eur": round(sum(x["lineTotal"] for x in out), 2)}
    offer, jws = create_offer(mandate, option, {"deliver_by": deliver_by, "quantity": requested_quantity,
                                                "unit": (requested_unit or "").upper() or None})
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
        if not isinstance(l, dict):
            return "each line must be an object with productId and units"
        if extra := sorted(set(l) - {"productId", "units", "perUnit", "substitution"}):
            # Prices and totals come from Valio, never from the caller.
            return f"unexpected field(s) {', '.join(extra)}: send productId, units and optionally perUnit, substitution"
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
    if err := caller_refusal(payload["mandate_id"], payload.get("offer_id", "security")):
        return None, err
    mandate = policy.load(payload["mandate_id"])
    ctx = {"deliver_by": payload["deliver_by"], "delivery_date": max(l["deliveryDate"] for l in payload["lines"]),
           "rationale": rationale, "requested_units": requested_units,
           # The policy's "matches the buyer's request" rule reads requested_quantity (in the same units as
           # the offer lines). Before, external agents' orders skipped that rule entirely.
           "requested_quantity": payload.get("requested_quantity") or requested_units}
    if payload.get("requested_quantity") and requested_units and abs(requested_units - payload["requested_quantity"]) > 1e-9:
        store.log(payload["offer_id"], f"agent:{mandate['agent']}", "security.intent_mismatch",
                  {"signed": payload["requested_quantity"], "claimed": requested_units})
        return None, (f"the order claims {requested_units:g} requested, but the signed offer says "
                      f"{payload['requested_quantity']:g}: request a new offer")
    if not store.order_by_key(idempotency_key) and (twin := _waiting_twin(payload)):
        store.log(payload["offer_id"], f"agent:{mandate['agent']}", "order.duplicate_refused", {"waiting": twin["id"]})
        return None, (f"an identical order ({twin['id']}) is already waiting for "
                      f"{(twin['policy'].get('approver') or {}).get('name', 'a person')}'s approval: don't order twice")
    return place_order(jws, idempotency_key, mandate, ctx, actor=f"agent:{mandate['agent']}")


def _waiting_twin(offer):
    """A pending order from the last hour with the same mandate and the same lines, if there is one.
    Why: a scoreboard agent ordered, "asked a person", then ordered again, leaving two identical orders
    for the approver."""
    lines = sorted((l["productId"], l["units"]) for l in offer["lines"])
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    for o in store.list_orders(200):
        if (o["status"] == "pending_approval" and o["mandate_id"] == offer["mandate_id"] and o["created"] >= cutoff
                and sorted((l["productId"], l["units"]) for l in o["record"]["offer"]["lines"]) == lines):
            return o
    return None


def decide(order_id, decision, approver, comment="", client=None):
    """A person approves or rejects a pending order.

    The approver must be named in the buyer's mandate with authority for this total. If the price lock
    ran out while the order waited, the price is checked again before the order is placed: a changed
    price means a new offer, never a silent placement at an old price."""
    with _commit():
        return _decide(order_id, decision, approver, comment, client)


def _decide(order_id, decision, approver, comment, client):
    order = store.get_order(order_id)
    if not order or order["status"] != "pending_approval":
        return None, "order is not waiting for approval"
    if decision not in ("approve", "reject"):
        return None, "decision must be 'approve' or 'reject'"
    if order["mandate_id"] not in policy.mandates():
        return None, f"unknown mandate {order['mandate_id']}: nobody can approve under it"
    mandate = policy.load(order["mandate_id"])
    who = next((a for a in mandate["approvers"] if a["name"] == approver), None)
    if not who:
        store.log(order_id, f"unknown:{approver}", "security.unknown_approver", {"decision": decision})
        return None, f"{approver or 'this person'} is not an approver in the buyer's mandate"
    if decision == "approve" and order["total"] > who["up_to_eur"]:
        return None, f"{approver} can approve up to €{who['up_to_eur']:,}; this order is €{order['total']:,.2f}"
    gate = (order["policy"].get("ladder") or {}).get("decided_by")
    unproven = [r["rule"] + ": " + str(r.get("detail", "")) for r in order["policy"].get("rules", [])
                if not r["ok"] and gates.gate_of(r["rule"]) == "proven"]
    if decision == "approve" and gate == "proven" and len((comment or "").strip()) < 8:
        # Gate 4 is a fact check, not a sign-off: clicking Approve proves nothing about stock or allergens.
        return None, ("this order waits for a fact check (" + "; ".join(unproven) + "). Write what you checked, "
                      "for example “Valio confirmed 1,000 in stock by phone”, and approve again")
    offer = order["record"]["offer"]
    if decision == "approve":
        # The world can change while an order waits: re-check everything a fresh order would face, not only
        # the price after the lock ran out (benchmark APPROVAL_RECHECK: mandate revoked, allergen newly
        # reported, hard limit lowered, stock gone, item withdrawn).
        refusal = _recheck_before_approval(order, mandate, client)
        if refusal:
            kind, why = refusal
            if kind == "retry":
                return None, why  # Valio didn't answer: the order keeps waiting
            order["status"] = kind
            order["record"].setdefault("approvals", []).append(
                {"approver": approver, "decision": decision, "comment": comment, "at": store.now(), "note": why})
            if not store.save_order(order, expect_status="pending_approval"):
                return None, "someone else decided this order first"
            store.log(order_id, "gateway", "order.expired" if kind == "expired" else "order.blocked_at_approval", {"reason": why})
            return None, why + (": request a new offer" if kind == "expired" else "")
    order["status"] = "placed" if decision == "approve" else "rejected"
    order["record"].setdefault("approvals", []).append(
        {"approver": approver, "decision": decision, "comment": comment, "at": store.now(),
         # What the click means: a gate-4 approval confirms facts the data couldn't prove (the approver checks
         # the label or the supplier); a gate-5 approval uses the approver's authority. Gate 3 can't be approved.
         "gate": gate,
         **({"fact_check": {"unproven": unproven, "evidence": comment}} if gate == "proven" and decision == "approve" else {})})
    if not store.save_order(order, expect_status="pending_approval"):
        return None, "someone else decided this order first"
    store.log(order_id, f"human:{approver}", f"approval.{decision}", {"comment": comment})
    return order, None


def _recheck_before_approval(order, mandate, client):
    """Why a pending order can't be placed now, as (status, reason), or None.

    status "expired": price, stock, cut-off or Valio's stock confirmation changed (a new offer may work).
    status "blocked": a hard rule fails now (mandate expired or revoked, banned allergen, hard limit,
    product withdrawn). "retry": Valio didn't answer, nothing changes."""
    offer = order["record"]["offer"]
    gone = [l["productId"] for l in offer["lines"] if not catalog.get(l["productId"])]
    if gone:
        return "blocked", f"no longer in Valio's catalog: {', '.join(gone)}"
    verdict = policy.evaluate(mandate, offer, dict(order["record"].get("context") or {}))
    hard = [f"{r['rule']}: {r['detail']}" for r in verdict["rules"] if not r["ok"] and r["gate"] == "allowed"]
    if hard:
        return "blocked", "a hard rule fails now: " + "; ".join(hard)
    # A problem the approver was never shown (an allergen warning, a claim no longer backed by data) means
    # they'd be approving something else than what they were asked about.
    before = {r["rule"] for r in order["policy"].get("rules", []) if not r["ok"]}
    new = [f"{r['rule']}: {r['detail']}" for r in verdict["rules"] if not r["ok"] and r["rule"] not in before
           and r["rule"] != "Agent's own spend in 24 h"]
    if new:
        return "expired", "something changed since you were asked (" + "; ".join(new) + ")"
    client = client or ValioClient()
    ids = [l["productId"] for l in offer["lines"]]
    fresh, err = _valio(client.prices, mandate["customer"], offer["deliver_by"], ids)
    got, err2 = (None, None) if err else _valio(client.availability, mandate["customer"], offer["deliver_by"], ids)
    if err or err2:
        return "retry", (err or err2).replace("nothing was ordered, try again", "the order keeps waiting, approve again later")
    now_s, avail = got
    changed = [l["productId"] for l in offer["lines"]
               if abs((_price((fresh or {}).get(l["productId"])) or -1) - l["unitPrice"]) > 0.005]
    changed += [l["productId"] for l in offer["lines"] if _line_problem(l, avail.get(l["productId"]) or {}, now_s, offer["deliver_by"])]
    unconfirmed = [l["productId"] for l in offer["lines"]
                   if l.get("stockConfirmed") and (avail.get(l["productId"]) or {}).get("logisticModel") != "Stock"]
    if changed or unconfirmed:
        return "expired", f"the price, stock, cut-off or Valio's stock confirmation changed for {', '.join(dict.fromkeys(changed + unconfirmed))}"
    store.log(order["id"], "gateway", "price.reconfirmed", {"reason": "re-checked with Valio before placing"})
    return None


def run(text, mandate_id, overrides=None, simulate_price_change=False, inject=False, real_clock=False):
    """Run the whole buyer-agent flow and return a step-by-step trace for the demo UI.

    real_clock=True ignores the demo clock for this run, so the mock's passed cut-offs apply."""
    token = CLOCK.set("" if real_clock else DEMO_CLOCK)
    try:
        res = _run(text, mandate_id, overrides, simulate_price_change, inject)
    finally:
        CLOCK.reset(token)
    res["clock"] = {"demo_clock": "" if real_clock else DEMO_CLOCK,
                    "note": ("Demo clock: the mock's availability snapshot is replayed as of "
                             f"{DEMO_CLOCK[:16].replace('T', ' ')}; every order cut-off is shifted by the same amount."
                             if DEMO_CLOCK and not real_clock else "Valio's real clock: the mock's cut-offs apply as given.")}
    return res


def _run(text, mandate_id, overrides=None, simulate_price_change=False, inject=False):
    mandate = policy.load(mandate_id, overrides)
    client = ValioClient()
    steps = []
    run_id = "run_" + uuid.uuid4().hex[:10]
    raw = text
    ai_intent = ai.parse(raw)
    # The deterministic checks read a normalised copy: invisible characters and look-alike letters removed,
    # "no lactose" read as "lactose-free", "12 dozen" as 144 (request_checks.normalise).
    text = request_checks.normalise(raw)
    if ai_intent and "_error" not in ai_intent:
        intent, parser = ai_intent, f"Claude ({ai.MODEL})"
        # Guard, whatever the model: if Claude's product is just a shortened version of the buyer's own words
        # ("milk" from "chicken nugget milk"), keep the buyer's words. Never let the reader invent a nicer product.
        rules = parse_request(text)
        said, got = rules["product"].split(), intent.get("product", "").lower().split()
        if rules["quantity"] is not None and got and set(got) < set(said):
            intent["product_note"] = f"Claude shortened “{rules['product']}” to “{intent['product']}”; kept the buyer's words."
            intent["product"] = rules["product"]
    else:
        intent = parse_request(text)
        parser = "rules" + (f" (Claude unavailable: {ai_intent['_error']})" if ai_intent else " (no Claude key configured)")
    intent["parser"] = parser
    # Deterministic reading of the same text (request_checks.py). It only ever makes the agent more careful.
    forced, forced_why = request_checks.request_type(text)
    if forced:
        intent["request_type"], intent["request_type_reason"] = forced, forced_why
    oq = request_checks.order_quantity(text)
    quantity_mismatch = ""
    if oq and parser.startswith("rules"):
        intent["quantity"], intent["unit"] = oq["quantity"], oq["unit"]
    elif oq and intent.get("quantity") is not None and (abs(oq["quantity"] - intent["quantity"]) > 1e-6 or oq["unit"] != intent["unit"]):
        quantity_mismatch = (f"the quantity reads two ways: “{oq['raw']}” is {oq['quantity']:g} {oq['unit']}, "
                             f"but it was read as {intent['quantity']:g} {intent['unit']}")
    allergy_codes = request_checks.allergies(text)
    if allergy_codes:
        # An allergy in the request tightens this run's mandate. It can only add bans, never remove one.
        hard = mandate["hard_limits"]
        hard["forbidden_allergens"] = sorted(set(hard.get("forbidden_allergens", [])) | set(allergy_codes))
        ap = mandate.setdefault("allergen_policy", {})
        ap["may_contain"] = "block"
        if ap.get("no_data", "allow") == "allow":  # a stated allergy: missing allergen data needs a person, at least
            ap["no_data"] = "needs_approval"
        intent["allergies"] = [catalog.ALLERGENS.get(a, a) for a in allergy_codes]
    store.log(run_id, f"agent:{mandate['agent']}", "request.received", {"text": raw, "mandate_id": mandate_id, "parser": parser,
                                                                         "request_allergies": allergy_codes})
    steps.append({"step": "request", "title": "Understand the request", "intent": intent})

    def exit_at(gate, action, status, reason):
        """Leave the decision ladder at this gate: nothing is ordered, and the reason is recorded."""
        store.log(run_id, f"agent:{mandate['agent']}", f"gate.{gate}.{action}", {"reason": reason})
        return {"run_id": run_id, "steps": steps, "api_calls": client.calls, "mandate": mandate,
                "result": {"status": status, "error": reason}, "ladder": gates.ladder(gate, action, reason)}

    # Gate 0: is it a purchase?
    rtype = intent.get("request_type") or "buy"
    if rtype == "other":
        return exit_at("purchase", "decline", "not_a_purchase",
                       f"Nothing was ordered: {intent.get('request_type_reason') or 'this is not a purchasing request'}.")
    if rtype == "change":
        return exit_at("purchase", "decline", "change_not_supported",
                       "Changing, cancelling or returning a placed order goes to a person; the agent only places new orders.")
    # Gate 1: do we know exactly what is wanted?
    findings = security.scan_request(text)
    if gaming := request_checks.policy_gaming(text):
        findings.append({"pattern": "policy_gaming", "why": gaming})
    if findings:
        # A request says what to buy, never how the agent should behave. The agent doesn't follow such text,
        # doesn't half-follow it, and doesn't order the rest of the request without the requester confirming.
        store.log(run_id, f"agent:{mandate['agent']}", "security.injection_in_request",
                  {"findings": [f["why"] for f in findings], "text": text[:300]})
        steps[-1]["injection"] = [f["why"] for f in findings]
        return exit_at("clear", "ask_back", "instruction_in_request",
                       "The request contains instructions aimed at the agent (" + "; ".join(sorted({f["why"] for f in findings}))
                       + "). The agent takes only what to buy from a request and never follows such text. "
                         "Nothing was ordered: send the purchase on its own.")
    if problem := request_checks.allergy_unclear(text):
        return exit_at("clear", "ask_back", "allergy_unclear", f"Nothing was ordered: {problem}.")
    banned = set(mandate["hard_limits"].get("forbidden_allergens") or [])
    if clash := request_checks.inherent_allergen(intent.get("product") or "", banned):
        # "Butter" for a dairy-free menu: the product asked for IS the banned allergen. Searching on would only find
        # look-alikes (butter beans, sesame butter), so stop and let the requester name an alternative.
        word, code = clash
        return exit_at("allowed", "stop", "allergen_conflict",
                       f"Nothing was ordered: {word} contains {catalog.ALLERGENS.get(code, code).lower()}, which this "
                       f"request or the mandate rules out. Name a {catalog.ALLERGENS.get(code, code).lower()}-free "
                       "alternative (for example a plant-based spread or oat drink) if you want one.")
    problem, gate = request_checks.date_problem(text, intent.get("deliver_by"), date.today())
    if problem:
        return exit_at(gate, "ask_back", "invalid_date", f"Nothing was ordered: {problem}.")
    if problem := request_checks.quantity_problem(text):
        return exit_at("clear", "ask_back", "invalid_quantity", f"Nothing was ordered: {problem}.")
    if rtype == "buy" and request_checks.multi_line(text):
        return exit_at("clear", "ask_back", "several_lines",
                       "The request names several quantities. Send each product as its own line, so each one is "
                       "checked on its own; nothing was ordered.")
    if problem := request_checks.conditional(text):
        return exit_at("clear", "ask_back", "conditional", f"Nothing was ordered: {problem}.")
    if quantity_mismatch:
        return exit_at("clear", "ask_back", "quantity_unclear", f"Nothing was ordered: {quantity_mismatch}.")
    if not (intent.get("product") or "").strip():
        return exit_at("clear", "ask_back", "unclear_request", "Which product? The request doesn't name one.")
    if problem := request_checks.too_broad(intent["product"]):
        return exit_at("clear", "ask_back", "too_broad", f"Nothing was ordered: {problem}.")
    if rtype == "buy" and not intent.get("quantity"):
        return exit_at("clear", "ask_back", "unclear_request", f"How much {intent['product']}? The request gives no quantity.")

    if intent.get("unit") == "PCS":
        intent["count_kind"] = count_kind(text)
    spec = {"fat_pct": intent.get("fat_pct"), "shelf_life": intent.get("shelf_life")}
    candidates, trace = catalog.search(intent["product"], intent["attributes"], spec=spec,
                                       type_check=ai.type_check if ai.enabled() else None)
    # Products the buyer named by id are the candidates (still checked against the requirements).
    if named := request_checks.named_products(text):
        named_ok = []
        for pid in named:
            p = catalog.get(pid)
            ok, why, gaps = catalog.matches_attributes(p, intent["attributes"])
            if not ok:
                return exit_at("possible", "ask_back", "named_product_fails",
                               f"Product {pid} ({p['name']}) doesn't meet the request: {why}. Nothing was ordered.")
            p["_gaps"] = gaps
            named_ok.append(p)
        candidates, trace["named_products"] = named_ok, named
    # Products the buyer excluded ("do NOT send Arla Cafè milk 463380", "ei kahvimaitoa").
    asked_words = set(re.findall(r"[a-zåäö]+", catalog.fold(intent["product"])))
    for ex in request_checks.exclusions(text):
        words = {w for w in ex["words"] - asked_words if w in catalog.vocabulary()}
        if not words and not ex["ids"]:
            continue
        before = len(candidates)
        candidates = [p for p in candidates if p["id"] not in ex["ids"]
                      and not (words and words <= set(re.findall(r"[a-zåäö]+", p["_text"])))]
        if len(candidates) < before:
            trace.setdefault("excluded_by_request", []).append(" ".join(sorted(words | ex["ids"])))
    # Gate 1 again, now that we know what the words matched: nothing in the request may be silently dropped.
    intent["ignored_terms"] = sorted(set(intent.get("ignored_terms") or []) |
                                     set(unmatched_terms(text, intent, [p["_text"] for p in candidates],
                                                         strict=intent["parser"].startswith("rules"))))
    if rtype == "buy" and intent["ignored_terms"]:
        return exit_at("clear", "ask_back", "unclear_request",
                       f"Understood: {intent.get('quantity') or ''} {intent.get('unit', '')} {intent['product']}. "
                       f"Not understood or not covered: {', '.join(intent['ignored_terms'])}. "
                       "Send them as separate lines, or confirm the order without them.")
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
    # Unit sense check: 1,000 litres of chicken nuggets can't be converted, so stop before any API call.
    if intent["unit"] in ("L", "KG") and candidates:
        measures = Counter(m for m in (catalog.measure(p) for p in candidates) if m)
        if measures and intent["unit"] not in measures and sum(measures.values()) >= len(candidates) / 2:
            alt = measures.most_common(1)[0][0]
            words = {"L": "volume (litres)", "KG": "weight (kg)"}
            msg = (f"{intent['product'].capitalize()} are sold by {words[alt]}, not by {words[intent['unit']]}, so "
                   f"{intent['quantity']:,g} {UNIT_WORDS[intent['unit']]} can't be ordered. Nothing was ordered.")
            steps.append({"step": "lookup", "title": "Find candidates in the catalog (local, no API calls)",
                          "search": trace, "excluded_by_mandate": excluded, "official_substitutes_added": [],
                          "substitutes_rejected": [], "spec_gaps": [], "candidates": []})
            store.log(run_id, f"agent:{mandate['agent']}", "run.unit_mismatch", {"asked": intent["unit"], "sold_by": alt})
            res = exit_at("clear", "ask_back", "unit_mismatch", msg)  # gate 1: the quantity can't mean anything
            res["result"]["fix"] = {"from": intent["unit"], "to": alt, "label": f"Use {intent['quantity']:,g} {UNIT_WORDS[alt]} instead"}
            return res
    candidates = apply_profile(candidates, intent, mandate)
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
            return exit_at("allowed", "stop", "no_safe_product", msg)  # gate 3: every candidate breaks a hard rule
        # Gate 2 (possible?): the reality check failed. Say what doesn't exist and what does, but order nothing.
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
        res = exit_at("possible", "ask_back", "not_in_catalog", msg)
        res["result"]["suggestions"] = sugg
        return res

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
    if rtype == "question":  # Gate 0 said "not a purchase": answer with prices and trust flags, order nothing
        return {"run_id": run_id, "steps": steps, "api_calls": client.calls, "mandate": mandate,
                "result": {"status": "answered"},
                "ladder": gates.ladder("purchase", "answer", f"{len(usable)} products can be delivered by {intent['deliver_by']}")}
    if not options:
        # Nothing in time. A later delivery is still "possible" (gate 2) but it is a compromise, so the order goes
        # on and gate 5 ("Delivery on time") sends it to a person. Stated requirements are never relaxed here.
        late = []
        for r in rows:
            if r.get("late_date"):
                rr = dict(r, status="ok", delivery_date=r["late_date"], units=r["units_needed"],
                          total=round(r["units_needed"] * r["unit_price"], 2))
                rr["issues"] = r["issues"] + [f"earliest delivery {r['late_date']}"]
                late.append(rr)
        if late:
            _score(late, mandate["weights"])
            options = build_options(late, intent)
            for o in options:
                o["label"], o["why"] = f"Later delivery ({o['lines'][0]['deliveryDate']})", "nothing can arrive by the date asked"
            steps[-1]["options"] = options
            steps[-1]["note"] = f"Nothing arrives by {intent['deliver_by']}; the earliest option goes to a person to accept."
    if not options:  # Gate 2: Valio can't supply it as asked; say exactly why, per reason
        return exit_at("possible", "ask_back", "no_option", explain_no_option(rows, intent))

    # The agent knows the buyer's hard limit: it never asks for an offer it knows the gateway must block.
    hard = mandate["hard_limits"]["max_order_total_eur"]
    within = [o for o in options if o["total_eur"] <= hard]
    if not within:
        best = min(options, key=lambda o: o["total_eur"])
        line = best["lines"][0]
        max_units = math.floor(hard / line["unitPrice"]) if line["unitPrice"] else 0
        max_qty = max_units * (line["perUnit"] or 1)
        unit = UNIT_WORDS.get(intent["unit"], intent["unit"])
        msg = (f"The cheapest option ({line['units']:,} × {line['name']}, €{best['total_eur']:,.2f}) is above this buyer's "
               f"hard limit of €{hard:,} per order, so the agent didn't order. Nothing was ordered.")
        store.log(run_id, f"agent:{mandate['agent']}", "run.over_hard_limit", {"cheapest_eur": best["total_eur"], "hard_limit": hard})
        res = exit_at("allowed", "stop", "over_hard_limit", msg)  # gate 3: no person may approve above the hard limit
        if max_units > 0:
            res["result"]["fix"] = {"quantity": max_qty,
                                    "label": f"Order {max_qty:,g} {unit} instead (€{max_units * line['unitPrice']:,.2f}, within the limit)"}
        return res
    chosen = within[0]
    if chosen is not options[0]:
        steps[-1]["note"] = (f"{options[0]['label']} (€{options[0]['total_eur']:,.2f}) is above the hard limit of €{hard:,}; "
                             f"the agent took {chosen['label'].lower()} instead.")
    offer, jws = create_offer(mandate, chosen, intent)
    steps.append({"step": "offer", "title": "Get a signed, fixed-price offer", "offer": offer, "jws": jws})

    context = {"deliver_by": intent["deliver_by"], "delivery_date": max(l["deliveryDate"] for l in chosen["lines"]),
               "partial": chosen["partial"], "simulate_price_change": simulate_price_change,
               "request": raw, "run_id": run_id, "options_considered": len(options),
               "requested_quantity": intent["quantity"], "required_attributes": intent.get("attributes") or [],
               "from_profile": intent.get("from_profile") or [],
               "ignored_terms": intent.get("ignored_terms") or [],
               # No date in the request: the delivery day is the agent's assumption, so a person confirms it (rule 2).
               "date_assumed": not request_checks.date_stated(text),
               # Unstated details (fat %, fresh vs long-life) and how much they move the order.
               "spec_gaps": [{"field": g["field"], "cost_spread_eur": g.get("cost_spread_eur", 0)} for g in gaps],
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
        return exit_at("possible", "ask_back", "error", err)
    order["record"]["api_calls"] = client.calls
    order["record"]["rows"] = [{k: r.get(k) for k in ("productId", "name", "status", "issues", "total", "score",
                                                      "reliability", "earliest_alternative")} for r in rows]
    order["record"]["options"] = options
    # Display details only: never a full save, which could undo an approval made in the meantime.
    store.merge_record(order["id"], {k: order["record"][k] for k in ("api_calls", "rows", "options")})
    steps.append({"step": "policy", "title": "Check the order against the mandate", "policy": order["policy"]})
    store.log(run_id, f"agent:{mandate['agent']}", "run.completed",
              {"order_id": order["id"], "status": order["status"], "api_calls": len(client.calls)})
    return {"run_id": run_id, "steps": steps, "order": order, "api_calls": client.calls, "mandate": mandate,
            "result": {"status": order["status"]}, "ladder": order["policy"]["ladder"]}


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
    # Fat level and fresh vs long-life are buying decisions for milk, cream and other dairy drinks (category
    # 104xx). For muesli or butter a fat difference between products isn't a detail the buyer left open.
    dairy = [p for p in candidates if str(p.get("category", "")).startswith("104")]
    fats = sorted({p["fatPct"] for p in dairy if p.get("fatPct") is not None})
    if intent.get("fat_pct") is None and len(fats) > 1:
        gaps.append({"field": "fat level", "values": [f"{f:g} %" for f in fats]})
    shelves = {catalog.is_long_life(p) for p in dairy}
    if intent.get("shelf_life") is None and not intent.get("shelf_life_any") and len(shelves) > 1:
        gaps.append({"field": "fresh or long-life", "values": ["fresh", "long-life (UHT)"]})
    return gaps


def apply_profile(candidates, intent, mandate):
    """Settle details the buyer left open from the mandate's buyer profile ("our milk is 1.5 %, fresh or
    long-life both fine"), the way a purchasing manager would. A preference, not a filter: it only narrows
    the candidates when some of them match, it never overrides what the request says, and every value used
    is shown as coming from the profile."""
    words = {catalog._stem(w) for w in re.findall(r"[a-zåäö]+", catalog.fold(intent.get("product") or ""))}
    prof = next((v for k, v in (mandate.get("profile") or {}).items() if catalog._stem(k) in words), None)
    if not prof:
        return candidates
    used = []
    if intent.get("fat_pct") is None and prof.get("fat_pct") is not None:
        match = [p for p in candidates if p.get("fatPct") is not None and abs(p["fatPct"] - prof["fat_pct"]) <= 0.3]
        if match:
            candidates, intent["fat_pct"] = match, prof["fat_pct"]
            used.append(f"fat {prof['fat_pct']:g} %")
    shelf = prof.get("shelf_life")
    if intent.get("shelf_life") is None and shelf == "any":
        intent["shelf_life_any"] = True
        used.append("fresh or long-life both fine")
    elif intent.get("shelf_life") is None and shelf:
        match = [p for p in candidates if catalog.is_long_life(p) == (shelf == "long_life")]
        if match:
            candidates, intent["shelf_life"] = match, shelf
            used.append(shelf.replace("_", "-"))
    if used:
        intent["from_profile"] = used
    return candidates


def explain_no_option(rows, intent):
    """Say exactly why nothing can be ordered, per reason, instead of a generic error."""
    why = Counter(r["status"] for r in rows)
    parts = []
    if why["deadline_passed"]:
        cut = min((r.get("closing_time") or "") for r in rows if r["status"] == "deadline_passed")
        parts.append(f"{why['deadline_passed']} missed their order deadline for {intent['deliver_by']}"
                     + (f" ({cut[:16].replace('T', ' ')})" if cut else ""))
    if why["unavailable"]:
        parts.append(f"{why['unavailable']} can't be delivered by {intent['deliver_by']}")
    if why["blocked"]:
        parts.append(f"{why['blocked']} have a sales block")
    if why["no_price"]:
        parts.append(f"{why['no_price']} have no price for this customer")
    if why["unit_mismatch"]:
        parts.append(f"{why['unit_mismatch']} can't be measured in {UNIT_WORDS.get(intent['unit'], intent['unit'])}")
    head = f"None of the {len(rows)} matching {intent['product']} products can be ordered as asked"
    tail = " A later delivery date may work." if why["deadline_passed"] or why["unavailable"] else ""
    return f"{head}: {'; '.join(parts)}. Nothing was ordered.{tail}"
