"""Live food-recall check against the Finnish Food Authority (Ruokavirasto), with the source shown.

Before an order is committed, each product is checked against the recall notices published right now:
1. find notices: the Food Authority's recall list, plus (with Oxylabs) a live web search for the product's brand
   and type on ruokavirasto.fi;
2. read each candidate notice in full (Oxylabs scrape, or a direct fetch without Oxylabs credentials);
3. a notice from the last 90 days that names the exact product stops the order (hard rule, gate 3); one that
   names its brand and product type sends it to a person to check the notice (gate 4). The URL is the source.
If the recall source can't be reached, the order waits for a person (gate 4): no answer is never "not recalled".

The catalog has no EAN codes, so a brand + type match can't tell whether this exact product is included: that is
a question for a person with the notice open, not a guess either way.
"""
import contextvars
import html
import os
import re
import threading
import time
from datetime import date, timedelta

import httpx

LIST_URL = "https://www.ruokavirasto.fi/takaisinvedot/"
BASE = "https://www.ruokavirasto.fi"
MAX_AGE_DAYS = 90
TEST = contextvars.ContextVar("test_recall", default=False)  # demo switch: a labelled test recall

_cache, _lock = {}, threading.Lock()
MONTHS = {"tammikuuta": 1, "helmikuuta": 2, "maaliskuuta": 3, "huhtikuuta": 4, "toukokuuta": 5, "kesäkuuta": 6,
          "heinäkuuta": 7, "elokuuta": 8, "syyskuuta": 9, "lokakuuta": 10, "marraskuuta": 11, "joulukuuta": 12}
# Finnish product-type words worth matching (stems), checked against the product's Finnish name
TYPES = ["kananmun", "munat", "munia", "maito", "kerma", "voi ", "juusto", "jogurt", "rahka", "kaurajuom", "jäätelö",
         "liha", "makkara", "kana", "kala", "lohi", "leipä", "patonki", "pulla", "keksi", "suklaa", "mysli", "puuro",
         "salaatti", "kastike", "keitto", "pakaste", "mehu", "kahvi", "tee ", "pähkin", "siemen", "riisi", "pasta"]


def via():
    return "Oxylabs Web API" if os.environ.get("OXYLABS_USERNAME") and os.environ.get("OXYLABS_PASSWORD") else "direct fetch"


def _cached(key, ttl, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (time.time(), val)
    return val


def _oxylabs(payload):
    r = httpx.post(os.environ.get("OXYLABS_URL", "https://realtime.oxylabs.io/v1/queries"), json=payload, timeout=60,
                   auth=(os.environ["OXYLABS_USERNAME"], os.environ["OXYLABS_PASSWORD"]))
    r.raise_for_status()
    return r.json()["results"][0]["content"]


def _fetch(url):
    """Page HTML: Oxylabs scrape when configured, else a plain fetch."""
    if via().startswith("Oxylabs"):
        return _oxylabs({"source": "universal", "url": url})
    r = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": "valio-trade-gateway (Junction demo)"})
    r.raise_for_status()
    return r.text


def _search(query):
    """Live web search for recall notices (Oxylabs only). Returns notice URLs."""
    if not via().startswith("Oxylabs"):
        return []
    content = _oxylabs({"source": "google_search", "query": query, "parse": True})
    organic = (content.get("results") or {}).get("organic", []) if isinstance(content, dict) else []
    return [x["url"] for x in organic if "ruokavirasto.fi" in x.get("url", "") and "takaisinve" in x.get("url", "")][:5]


def _text(page):
    m = re.search(r"<main.*?</main>", page, re.S)
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", m.group(0) if m else page, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", t)).split())


def notices():
    """[(title, url)] from the Food Authority's recall list (cached 30 min)."""
    def load():
        page = _fetch(LIST_URL)
        out = []
        for href, title in re.findall(r'<a[^>]+href="([^"]*takaisinvedot[^"]*/[^"]+/)"[^>]*>(.*?)</a>', page, re.S):
            title = " ".join(html.unescape(re.sub(r"<[^>]+>", "", title)).split())
            if title and "määrät" not in title:
                out.append((title, href if href.startswith("http") else BASE + href))
        return out
    return _cached("list", 1800, load)


def notice(url):
    """{"title", "date", "text"} for one notice (cached 24 h)."""
    def load():
        t = _text(_fetch(url))
        m = re.search(r"(\d{1,2})\. (" + "|".join(MONTHS) + r") (\d{4})", t)
        d = date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1))) if m else None
        return {"date": d, "text": t[:6000]}
    return _cached("n:" + url, 86400, load)


def _title(text):
    """The notice's heading: the text before its date."""
    m = re.search(r"(\d{1,2})\. (" + "|".join(MONTHS) + r") \d{4}", text)
    head = text[:m.start()] if m else text[:120]
    words = head.split()
    half = len(words) // 2  # breadcrumbs repeat the heading: keep the last occurrence
    return " ".join(words[half:]) if half and words[:half][-half:] == words[half:] else " ".join(words[-12:])


def _types(p):
    name = " " + (p.get("name_fi") or "").lower() + " "
    return [t for t in TYPES if t in name]


def check(p, today=None):
    """{"status": "clear" | "possible" | "recalled" | "unknown", ...} for one catalog product.

    recalled: the notice names this exact product. possible: it names the brand and product type, so a person
    checks whether this product is included (the catalog has no EAN codes to settle it)."""
    if TEST.get():
        return {"status": "recalled", "test": True, "title": "Test recall (demo, not a real notice)", "source": LIST_URL,
                "via": via(), "date": str(today or date.today())}
    types, brand = _types(p), (p.get("brand") or "").strip()
    if not types or len(brand) < 3:
        return {"status": "clear", "checked": 0, "via": via(), "note": "no product type to match"}
    today = today or date.today()
    try:
        titles = {u: t for t, u in notices() if any(x.strip() in t.lower() for x in types)}
        for u in _search(f"site:ruokavirasto.fi takaisinveto {brand} {types[0].strip()}"):
            titles.setdefault(u, "")
        seen = []
        for url in titles:
            n = notice(url)
            if n["date"] and n["date"] < today - timedelta(days=MAX_AGE_DAYS):
                continue
            seen.append(url)
            low = n["text"].lower()
            if brand.lower() in low and any(x.strip() in low for x in types):
                core = re.sub(r"\b\d+([.,]\d+)?\s*(x|kg|g|l|dl|ml|kpl)\b|®", " ", (p.get("name_fi") or "").lower())
                core = " ".join(core.split())
                exact = len(core) > 8 and core in " ".join(low.replace("®", " ").split())
                return {"status": "recalled" if exact else "possible", "title": titles[url] or _title(n["text"]), "source": url,
                        "date": str(n["date"]), "via": via()}
        return {"status": "clear", "checked": len(seen), "via": via()}
    except Exception as e:  # noqa: BLE001 - unreachable source: a person checks, never "not recalled"
        return {"status": "unknown", "error": type(e).__name__, "source": LIST_URL, "via": via()}
