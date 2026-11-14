"""Benchmark a request reader against Claude's readings on the held-out test set (data/reader/test.jsonl).

Scores what the gateway depends on, field by field: request type, product, quantity, unit, attributes, delivery
date, and whether the reader flagged unread words (ignored_terms empty or not, which decides ask-back at gate 1).
Also latency and schema failures. The reader under test is whatever gateway/ai.py is configured to use:

  READER=eu EU_LLM_BASE_URL=https://<verda endpoint>/v1 EU_LLM_MODEL=<model> python scripts/reader_bench.py
  python scripts/reader_bench.py            # Claude itself (sanity check: should be close to 100 %)

Writes state/reader_bench_<reader>.json.
"""
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gateway import ai  # noqa: E402

TODAY = date(2026, 11, 14)
FIELDS = ["request_type", "product", "quantity", "unit", "attributes", "deliver_by", "flags_unread"]


def norm(intent):
    return {"request_type": intent.get("request_type"), "product": (intent.get("product") or "").strip().lower(),
            "quantity": intent.get("quantity"), "unit": intent.get("unit"),
            "attributes": sorted(intent.get("attributes") or []), "deliver_by": intent.get("deliver_by"),
            "flags_unread": bool(intent.get("ignored_terms"))}


def one(row):
    t = time.monotonic()
    got = ai.parse(row["request"], today=TODAY)
    ms = (time.monotonic() - t) * 1000
    if not got or got.get("_error"):
        return {"request": row["request"], "ms": ms, "error": (got or {}).get("_error", "no answer")}
    want, have = norm(row["intent"]), norm(got)
    return {"request": row["request"], "ms": ms, "match": {f: want[f] == have[f] for f in FIELDS},
            "diff": {f: [want[f], have[f]] for f in FIELDS if want[f] != have[f]}}


def main():
    rows = [json.loads(line) for line in open(ROOT / "data" / "reader" / "test.jsonl")]
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(one, rows))
    ok = [r for r in res if "match" in r]
    score = {f: round(100 * sum(r["match"][f] for r in ok) / len(res), 1) for f in FIELDS}
    exact = round(100 * sum(all(r["match"].values()) for r in ok) / len(res), 1)
    summary = {"reader": ai.reader_name(), "cases": len(res), "schema_failures": len(res) - len(ok),
               "all_fields_match_pct": exact, "field_match_pct": score,
               "latency_ms_p50": round(statistics.median(r["ms"] for r in res)),
               "examples_of_differences": [r for r in ok if r["diff"]][:15]}
    name = "".join(c if c.isalnum() else "_" for c in ai.reader_name()).strip("_")
    (ROOT / "state" / f"reader_bench_{name}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps({k: v for k, v in summary.items() if k != "examples_of_differences"}, indent=2))


if __name__ == "__main__":
    main()
