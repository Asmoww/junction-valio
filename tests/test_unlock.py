"""Which missing data would let the most held orders through alone (what-if on real orders, nothing changed)."""
from gateway import agent, catalog, store, unlock

from .conftest import FakeValio

HOTEL = "mnd_hotel_helsinki"


def held(units, key):
    _, jws, err = agent.quote(HOTEL, "2026-11-16", [{"productId": "999001", "units": units}], FakeValio())
    assert err is None, err
    return agent.order_from_jws(jws, key)[0]


def test_missing_stock_confirmation_is_ranked_with_what_it_unlocks():
    with store._lock:
        store._db.execute("DELETE FROM orders"); store._db.commit()
    small = held(10, "k-unlock-1")          # held only because stock isn't confirmed
    big = held(900, "k-unlock-2")           # also over the agent's own limit
    assert small["status"] == big["status"] == "pending_approval"
    r = unlock.report(HOTEL)
    g = next(x for x in r["gaps"] if x["productId"] == "999001" and x["check"] == "Stock confirmed by Valio")
    assert g["held_orders"] == 2 and g["unlocked"] == 1
    assert "Auto-approve spend limit" in g["still_needs_a_person_because"]
    assert r["held_for_missing_data"] == 2 and r["unlocked_if_all_fixed"] == 1


def test_the_what_if_changes_nothing():
    before = (catalog.get("999001"), [o["status"] for o in store.list_orders(100)])
    unlock.report(HOTEL)
    assert (catalog.get("999001"), [o["status"] for o in store.list_orders(100)]) == before
