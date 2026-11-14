"""Learned from decisions: suggestions only, drawn from approvals, rejections and ask-backs."""
from gateway import agent, learning, store

from .conftest import FakeValio

HOTEL = "mnd_hotel_helsinki"


def pending(units, key):
    _, jws, _ = agent.quote(HOTEL, "2026-11-16", [{"productId": "464306", "units": units}], FakeValio())
    return agent.order_from_jws(jws, key)[0]["id"]


def test_approved_every_time_suggests_the_week_plan(monkeypatch):
    with store._lock:
        store._db.execute("DELETE FROM orders"); store._db.commit()
    for i in range(3):
        oid = pending(900 + i, f"k-learn-{i}")
        assert agent.decide(oid, "approve", "Aino Virtanen", "fine, we use this every week", client=FakeValio())[0]
    s = learning.suggestions(HOTEL)["rule_suggestions"]
    spend = next(x for x in s if x["rule"] == "Auto-approve spend limit")
    assert spend["kind"] == "loosen" and spend["approved"] == 3 and spend["where"] == "plan"


def test_suggestions_change_nothing():
    from gateway import policy
    before = policy.load(HOTEL)
    learning.suggestions(HOTEL)
    assert policy.load(HOTEL) == before
    assert not store.plans_for(HOTEL, "approved")


def test_an_ask_back_followed_by_a_clear_request_is_a_phrasing_to_learn(valio):
    agent.run("We need milk", HOTEL)  # no quantity: asks back
    agent.run("We need 100 litres of lactose-free milk by Monday", HOTEL)
    c = learning.suggestions(HOTEL)["clarifications"]
    assert any(x["asked"] == "We need milk" and "100 litres" in x["clarified"] for x in c)
