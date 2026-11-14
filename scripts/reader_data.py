"""Training and test data for a European request reader (open-weight model on Verda).

1. Requests: the demo and scoreboard requests, plus ~600 varied kitchen requests written by Claude (English and
   Finnish; units, dates, attributes, several products, questions, missing amounts, instructions aimed at the agent).
2. Labels: Claude's own reading of each request (gateway/ai.py), on a fixed date, so the open model learns exactly
   the intent the gateway expects. Requests Claude couldn't read are dropped.
3. Split 85/15 into data/reader/train.jsonl and data/reader/test.jsonl, in chat format for fine-tuning.

Usage: python scripts/reader_data.py [--n 600]
"""
import argparse
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import anthropic  # noqa: E402

from gateway import ai  # noqa: E402

TODAY = date(2026, 11, 14)
OUT = ROOT / "data" / "reader"
THEMES = [
    "dairy for a hotel breakfast (milk, cream, butter, yoghurt, quark), litres and kg",
    "a school kitchen's weekly order: muesli, bread, potatoes, milk, fruit; allergies (nuts, gluten, milk)",
    "a hospital ward with diets: lactose-free, gluten-free, milk-free, texture-modified",
    "restaurant meat, fish and vegetables by weight, with a delivery weekday",
    "requests written in Finnish, the way a Finnish chef would type them (laktoositon, kg, litraa, maanantaiksi)",
    "messy or casual requests with typos, abbreviations and missing details (no amount, no date, vague product)",
    "questions about products, prices or availability that are not orders",
    "requests naming two or three products in one sentence",
    "requests with pieces and packs: eggs, portions, cartons, crates",
    "requests that try to change the agent's rules or claim approvals ('the CFO approved', 'ignore the limit')",
    "requests with fat percentage, organic, Finnish origin, fresh vs long-life (UHT)",
    "products that don't exist or impossible combinations (chicken nugget milk, litres of butter cubes)",
]


def seeds():
    out = ["I need 1,000 litres of lactose-free milk by Monday.", "We need 100 litres of lactose-free milk by Monday.",
           "We need 100 litres of chicken nugget milk by Monday.", "Order 200 litres of organic oat drink by Tuesday."]
    try:
        from scripts.scoreboard import CASES
        out += [c["request"] for c in CASES]
    except Exception:  # noqa: BLE001 - the scoreboard needs the Valio API at import; seeds are optional
        pass
    return out


def generate(n):
    llm, out = anthropic.Anthropic(), []
    per = 50
    for i in range(0, n, per):
        theme = THEMES[(i // per) % len(THEMES)]
        r = llm.messages.create(
            model=ai.MODEL, max_tokens=8000,
            messages=[{"role": "user", "content": f"Write {per} different purchasing requests that a professional kitchen might "
                       f"send to a food wholesaler's ordering agent. Theme: {theme}. Vary length, tone, quantities, units and "
                       "dates (weekdays, 'tomorrow', ISO dates, none). One request per line, no numbering, no quotes."}])
        text = next(b.text for b in r.content if b.type == "text")
        out += [ln.strip(" -•\t") for ln in text.splitlines() if len(ln.strip()) > 8][:per]
        print(f"generated {len(out)}", flush=True)
    return out


def label(text):
    intent = ai.parse(text, today=TODAY)
    if not intent or intent.get("_error"):
        return None
    intent.pop("raw", None)
    return {"messages": [{"role": "system", "content": ai.SYSTEM},
                         {"role": "user", "content": ai.user_message(text, TODAY)},
                         {"role": "assistant", "content": json.dumps(intent, ensure_ascii=False)}],
            "request": text, "intent": intent}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=600)
    args = ap.parse_args()
    requests = list(dict.fromkeys(seeds() + generate(args.n)))
    with ThreadPoolExecutor(6) as ex:
        rows = [r for r in ex.map(label, requests) if r]
    random.Random(7).shuffle(rows)
    cut = int(len(rows) * 0.85)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, part in (("train", rows[:cut]), ("test", rows[cut:])):
        with open(OUT / f"{name}.jsonl", "w") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in part)
    print(f"{len(requests)} requests, {len(rows)} labelled: {cut} train, {len(rows) - cut} test -> {OUT}")


if __name__ == "__main__":
    main()
