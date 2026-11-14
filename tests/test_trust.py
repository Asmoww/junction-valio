"""Earned trust: a new agent orders alone only up to part of the buyer's limit; clean orders raise it; an outside
agent that tries something a hard rule forbids is put on hold."""
import pytest

from gateway import agent, policy, store, trust

from .conftest import FakeValio

pytestmark = pytest.mark.trust
HOTEL = "mnd_hotel_helsinki"


@pytest.fixture(autouse=True)
def clean():
    before = {o["id"] for o in store.list_orders(100000)}
    with store._lock:
        store._db.execute("UPDATE orders SET mandate_id = 'old-' || mandate_id WHERE mandate_id = ?", (HOTEL,))
        store._db.commit()
    yield
    with store._lock:
        for o in store.list_orders(100000):
            if o["id"] not in before:
                store._db.execute("DELETE FROM orders WHERE id = ?", (o["id"],))
        store._db.execute("UPDATE orders SET mandate_id = substr(mandate_id, 5) WHERE mandate_id LIKE 'old-%'")
        store._db.commit()


def order(units, key):
    _, jws, err = agent.quote(HOTEL, "2026-11-16", [{"productId": "464306", "units": units}], FakeValio())
    assert err is None, err
    return agent.order_from_jws(jws, key)[0]


def test_a_new_agent_orders_alone_only_a_quarter_of_the_buyers_limit():
    m = policy.load(HOTEL)
    s = trust.status(m, "external")
    assert s["level"] == "New" and s["limit_eur"] == 125
    assert order(100, "k-trust-small")["status"] == "placed"     # €101, under €125
    o = order(200, "k-trust-big")                                 # €202: under the buyer's €500, over the new agent's €125
    assert o["status"] == "pending_approval"
    assert any(r["rule"] == "Earned trust" and not r["ok"] for r in o["policy"]["rules"])


def test_clean_orders_raise_trust():
    for i in range(5):
        assert order(10 + i, f"k-trust-clean-{i}")["status"] == "placed"
    assert trust.status(policy.load(HOTEL), "external")["level"] == "Proven"


def test_an_attempt_a_hard_rule_forbids_puts_an_outside_agent_on_hold():
    assert order(4999, "k-trust-ceiling")["status"] == "blocked"   # €5,049 over the €5,000 hard limit
    s = trust.status(policy.load(HOTEL), "external")
    assert s["level"] == "On hold" and s["limit_eur"] == 0
    assert order(5, "k-trust-after")["status"] == "pending_approval"
    assert trust.status(policy.load(HOTEL), "built-in")["level"] != "On hold"  # the built-in agent isn't punished
