"""Several server processes sharing one database (round-2 red-team review, T05-T07).

A threading lock only covers one process. These start real separate Python processes."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from gateway import store
from gateway.config import DB_PATH

ROOT = Path(__file__).resolve().parent.parent


def race(*calls):
    start = time.time() + 1.5  # time for every process to import the gateway
    env = {**os.environ, "GATEWAY_DB": str(DB_PATH),  # the same database file as this test process
           "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])}
    procs = [subprocess.Popen([sys.executable, str(ROOT / "tests" / "_worker.py"), *c, str(start)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env) for c in calls]
    out = []
    for p in procs:
        stdout, stderr = p.communicate(timeout=60)
        assert p.returncode == 0, stderr
        out.append(json.loads(stdout.strip().splitlines()[-1]))
    return out


def test_two_processes_commit_one_signed_offer_once():
    results = race(("commit", "ofr_proc_same/mnd_proc_1"), ("commit", "ofr_proc_same/mnd_proc_1"))
    assert sorted(r["status"] or "refused" for r in results) == ["placed", "refused"]
    assert sum(o["offer_id"] == "ofr_proc_same" for o in store.list_orders(500)) == 1


def test_two_processes_cannot_both_spend_the_24h_autonomy():
    results = race(("commit", "ofr_proc_a/mnd_proc_2"), ("commit", "ofr_proc_b/mnd_proc_2"))  # €400 each, €500 cap
    assert sorted(r["status"] for r in results) == ["pending_approval", "placed"]


def test_approve_and_reject_at_the_same_moment_one_wins():
    oid = "ord_proc_race"
    store.save_order({"id": oid, "offer_id": "ofr_proc_pending", "idempotency_key": "k-proc-pending",
                      "status": "pending_approval", "total": 100.0, "mandate_id": "mnd_hotel_helsinki",
                      "policy": {"outcome": "needs_approval", "rules": []},
                      "record": {"offer": {"expires_at": "2099-12-31T23:59:59+00:00", "lines": []}}})
    results = race(("approve", oid), ("reject", oid))
    winners = [r for r in results if r["status"]]
    assert len(winners) == 1
    assert store.get_order(oid)["status"] == winners[0]["status"]
