"""Run one gateway call in a separate process (used by tests/test_processes.py).

Arguments: <action> <arg> <start-time>. The parent's TG_STATE_DIR is inherited, so all workers share one
database, like several server processes would. Each worker waits until <start-time> so they really collide."""
import json
import sys
import time

from gateway import agent, store

action, arg, start = sys.argv[1], sys.argv[2], float(sys.argv[3])


class SlowValio:
    def prices(self, customer, date, ids):
        time.sleep(0.2)  # widen the gap between the checks and the save
        return {i: {"catalogPrice": {"value": 1.0}} for i in ids}

    def availability(self, customer, date, ids):
        return "2026-11-13T20:00:00+0200", {i: {"closingTime": "2099-01-01T00:00:00+0200", "logisticModel": "Stock",
                                                "scheduleLines": [{"availableAmount": 10**6, "availableDate": "2026-11-16"}]}
                                            for i in ids}


CAP = 500


def evaluate(mandate, payload, ctx):  # stands in for the policy: €500 of autonomous spend per 24 h
    auto = ctx["auto_spend_24h"] + payload["total_eur"] <= CAP
    return {"outcome": "auto_approve" if auto else "needs_approval", "approver": None, "rules": []}


agent.policy.evaluate = evaluate
read = store.get_order
store.get_order = lambda oid: (read(oid), time.sleep(0.2))[0]  # read, then pause before deciding
time.sleep(max(0, start - time.time()))

if action == "commit":  # arg = "offer id/mandate id"; every worker uses its own idempotency key
    arg, mandate_id = arg.split("/")
    MANDATE = {"mandate_id": mandate_id, "agent": "test", "customer": "1"}
    payload = {"offer_id": arg, "expires_at": "2099-12-31T23:59:59+00:00", "deliver_by": "2026-11-16",
               "lines": [{"productId": "p", "unitPrice": 1.0, "units": 400}], "total_eur": 400.0}
    agent.signing.verify = lambda jws: (True, dict(payload))
    order, err = agent.place_order("jws", f"key-{arg}-{time.time_ns()}", MANDATE, {}, client=SlowValio())
    print(json.dumps({"status": order and order["status"], "error": err}))
else:  # action = approve/reject, arg = order id
    order, err = agent.decide(arg, action, "Aino Virtanen")
    print(json.dumps({"status": order and order["status"], "error": err}))
