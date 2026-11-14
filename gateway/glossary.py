"""A buyer's glossary: phrasings a person has taught the gateway, so the next request is read the way they meant.

The learning loop. When the agent has to ask back ("How much milk?") and the buyer's next request goes through,
learning.py suggests the pair. A person (never an agent) accepts it here. From then on, when that buyer writes the
taught phrase, the request is read with its meaning, and the result says so.

What it can and can't do:
- It only says *what* is meant ("milk, the usual" = "300 litres of lactose-free milk by Monday"). The expanded
  request runs every gate as if typed out: hard rules, allergies, budgets, approvals. Nothing is loosened.
- The injection scanner reads both the original and the expanded text; a meaning that the scanner flags, or that
  claims an approval or a limit, is refused when it's taught.
- One buyer's glossary never applies to another buyer. Every change is logged; entries can be revoked.
"""
import re
import uuid

from . import catalog, policy, security, store

MAX_PHRASE = 120
MAX_MEANING = 400


def _key(text):
    """Comparable form of a phrase: folded, lower case, punctuation and extra spaces removed."""
    t = catalog.fold(str(text or "")).lower()
    t = re.sub(r"[^\w\s%€.,-]", " ", t)
    t = re.sub(r"[.,!?;:]+(\s|$)", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def entries(mandate_id, active_only=True):
    return [e for e in store.glossary_for(mandate_id) if e["status"] == "active" or not active_only]


def teach(mandate_id, phrase, meaning, by, source=None):
    """Add or replace one entry. Returns (entry, None) or (None, why)."""
    if mandate_id not in policy.mandates():
        return None, "unknown mandate"
    phrase, meaning = str(phrase or "").strip(), str(meaning or "").strip()
    if not phrase or not meaning:
        return None, "give the phrase and what it means"
    if len(phrase) > MAX_PHRASE or len(meaning) > MAX_MEANING:
        return None, f"keep the phrase under {MAX_PHRASE} and the meaning under {MAX_MEANING} characters"
    if _key(phrase) == _key(meaning):
        return None, "the meaning is the same as the phrase"
    for label, text in (("phrase", phrase), ("meaning", meaning)):
        if findings := security.scan_request(text):
            store.log("security", f"human:{by}", "security.glossary_refused",
                      {"mandate_id": mandate_id, "why": findings[0]["why"]})
            return None, f"the {label} can't be taught: it {findings[0]['why']}"
    key = _key(phrase)
    for old in entries(mandate_id):
        if old["key"] == key:  # one meaning per phrase: the new one replaces the old
            old.update(status="replaced")
            store.save_glossary(old)
    e = {"id": "gls_" + uuid.uuid4().hex[:10], "mandate_id": mandate_id, "phrase": phrase, "key": key,
         "meaning": meaning, "status": "active", "taught_by": str(by)[:80], "created": store.now(),
         "source": source or {}}
    store.save_glossary(e)
    store.log(e["id"], f"human:{by}", "glossary.taught", {"mandate_id": mandate_id, "phrase": phrase, "meaning": meaning})
    return e, None


def revoke(entry_id, by):
    e = store.get_glossary(entry_id)
    if not e or e["status"] != "active":
        return None, "not an active glossary entry"
    e.update(status="revoked", revoked_by=str(by)[:80], revoked_at=store.now())
    store.save_glossary(e)
    store.log(entry_id, f"human:{by}", "glossary.revoked", {"mandate_id": e["mandate_id"], "phrase": e["phrase"]})
    return e, None


def apply(mandate_id, text):
    """(text to read, entries used). A request that *is* a taught phrase becomes its meaning; a taught phrase
    inside a longer request is replaced by its meaning where it stands, as whole words."""
    used, key = [], _key(text)
    if not key:
        return text, used
    for e in sorted(entries(mandate_id), key=lambda e: -len(e["key"])):
        if key == e["key"]:
            return e["meaning"], [e]
    out = text
    for e in sorted(entries(mandate_id), key=lambda e: -len(e["key"])):
        words = r"\s+".join(re.escape(w) for w in e["key"].split())
        pattern = re.compile(r"(?<![\w])" + words + r"(?![\w])", re.I)
        if pattern.search(catalog.fold(out)):
            out = pattern.sub(e["meaning"], catalog.fold(out))
            used.append(e)
    return (out if used else text), used
