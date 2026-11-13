"""Run the trap suite and save the results for the Valio view (state/trap_results.json).

Usage: python scripts/run_traps.py
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("GATEWAY_DB", str(ROOT / "state" / "traps.db"))
sys.path.insert(0, str(ROOT))

from tests.traps.cases import CASES  # noqa: E402

results = []
for category, title, fn in CASES:
    t = time.monotonic()
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001 - a crash is a failed trap
        ok, detail = False, f"error: {type(e).__name__}: {e}"
    results.append({"category": category, "title": title, "case": fn.__name__,
                    "result": "skip" if ok is None else "pass" if ok else "fail",
                    "detail": detail, "ms": round((time.monotonic() - t) * 1000)})
    print(f"{results[-1]['result'].upper():4}  {title}\n      {detail}")

summary = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "passed": sum(r["result"] == "pass" for r in results),
           "failed": sum(r["result"] == "fail" for r in results),
           "skipped": sum(r["result"] == "skip" for r in results),
           "results": results}
(ROOT / "state" / "trap_results.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
print(f"\n{summary['passed']} passed, {summary['failed']} failed, {summary['skipped']} skipped")
sys.exit(1 if summary["failed"] else 0)
