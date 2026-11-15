"""Regenerate the deck's numbers from real runs (never type them by hand). Writes numbers.json next to this file.

- traps: the live trap suite (tests/traps), passed / total
- everyday: 36 everyday requests (everyday36.json) on a fresh database, how many the agent placed alone
- tests: the offline test count
Run from the repo root:  .venv/bin/python docs/presentation/build/deck_numbers.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PY = str(ROOT / ".venv" / "bin" / "python")


def pytest_counts(args):
    out = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args], cwd=ROOT, capture_output=True, text=True).stdout
    tail = out.strip().splitlines()[-1]
    passed = int((re.search(r"(\d+) passed", tail) or [0, 0])[1])
    failed = int((re.search(r"(\d+) failed", tail) or [0, 0])[1])
    return passed, passed + failed, tail


def everyday():
    env = dict(os.environ, TG_STATE_DIR=tempfile.mkdtemp(prefix="deck-numbers-"))
    code = ("import json, sys\nfrom gateway import agent\nn = ok = 0\n"
            "for buyer, text in json.load(open(sys.argv[1])):\n"
            "    r = agent.run(text, buyer); n += 1\n"
            "    ok += (r.get('order') or {}).get('status') == 'placed'\n"
            "print(json.dumps({'placed': ok, 'total': n}))\n")
    out = subprocess.run([PY, "-c", code, str(HERE / "everyday36.json")], cwd=ROOT, env=env, capture_output=True, text=True).stdout
    return json.loads(out.strip().splitlines()[-1])


if __name__ == "__main__":
    started = datetime.now().astimezone()
    traps = pytest_counts(["-m", "live", "tests/traps"])
    tests = pytest_counts([])
    day = everyday()
    reader = subprocess.run([PY, "-c", "from gateway import ai; print(ai.reader_name())"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    data = {"run_at": started.strftime("%a %d %b %Y %H:%M %Z"), "reader": reader,
            "traps": {"passed": traps[0], "total": traps[1]}, "tests": tests[0],
            "everyday": day}
    (HERE / "numbers.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps(data, ensure_ascii=False))
    sys.exit(0)
