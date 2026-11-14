"""Several products in one request: every line climbs gates 1-2 on its own, one signed offer covers the basket,
gates 3-5 check it once, and the order takes the earliest "no" of any line. Any line that fails sends the whole
list back: nothing is ordered."""
import pytest

from gateway import agent, catalog, policy, request_checks, signing, store

from .conftest import PRODUCTS, FakeValio, product

HOTEL = "mnd_hotel_helsinki"
BASKET = PRODUCTS + [
    product("500001", "Butter 500 g", lot=1, lactose=None),
    product("410001", "Whipping cream 1 l lactose free"),
]
PRICES = {"500001": 4.75, "410001": 3.2}


class Valio(FakeValio):
    """Butter is priced per pack and compared per kg (2 packs = 1 kg)."""

    def prices(self, customer, date, ids):
        out = super().prices(customer, date, ids)
        if "500001" in out:
            out["500001"].update(comparisonPrice={"value": 9.5}, comparisonUnit="KG")
        return out


@pytest.fixture(autouse=True)
def basket_catalog(monkeypatch):
    monkeypatch.setattr(catalog, "_catalog", BASKET)
    monkeypatch.setattr(catalog, "_by_id", {p["id"]: p for p in BASKET})
    monkeypatch.setattr(catalog, "_vocab", None)
    monkeypatch.setattr(agent, "ValioClient", lambda: Valio(prices=PRICES))


@pytest.fixture(autouse=True)
def forget_orders_made_here():
    """Orders placed here would count towards the hotel's 24 h autonomy in later test files: remove them."""
    before = {o["id"] for o in store.list_orders(100000)}
    yield
    with store._lock:
        for o in store.list_orders(100000):
            if o["id"] not in before:
                store._db.execute("DELETE FROM orders WHERE id = ?", (o["id"],))
        store._db.commit()


def run(text, mandate=HOTEL, **kw):
    return agent.run(text, mandate, **kw)


def test_a_list_is_cut_into_lines():
    parts = request_checks.split_lines("We need 100 L lactose-free milk, 20 kg butter and 30 L cream by Monday.")
    assert [p["quantities"] for p in parts] == [1, 1, 1]
    assert parts[1]["text"] == "20 kg butter"
    assert [p["quantities"] for p in request_checks.split_lines("1,000 L milk; 1,5 kg butter")] == [1, 1]  # number commas stay


def test_a_basket_becomes_one_signed_offer_and_one_order():
    res = run("We need 100 L lactose-free milk and 20 kg butter by Monday")
    assert res["ladder"]["action"] == "order", res["result"]
    assert [b["n"] for b in res["basket"]] == [1, 2]
    lines = res["order"]["record"]["offer"]["lines"]
    assert {l["productId"] for l in lines} == {"464306", "500001"}
    assert res["order"]["total"] == pytest.approx(101 + 190)
    ok, payload = signing.verify(res["steps"][-2]["jws"])
    assert ok and [b["requested_quantity"] for b in payload["basket"]] == [100, 20]  # each line's request is sealed
    match = next(r for r in res["order"]["policy"]["rules"] if r["rule"] == "Matches the buyer's request")
    assert match["ok"] and "line 1" in match["detail"] and "line 2" in match["detail"]


def test_one_bad_line_sends_the_whole_list_back():
    res = run("We need 100 L lactose-free milk, 20 kg butter and 50 L goat milk by Monday")
    assert (res["ladder"]["action"], res["ladder"]["decided_by"]) == ("ask_back", "possible")
    assert res["result"]["status"] == "basket_line_failed"
    assert "line 3" in res["result"]["error"] and "other 2 lines" in res["result"]["error"]
    assert "order" not in res and not any(s["step"] == "offer" for s in res["steps"])


def test_the_earliest_failing_gate_decides():
    res = run("100 L lactose-free milk, -5 kg butter by Monday")  # a negative amount is caught for the whole request
    assert res["ladder"]["decided_by"] == "clear"
    res = run("100 L lactose-free milk, 50 L goat milk and 20 kg chili cheese chicken mc-nuggel by Monday")
    assert res["ladder"]["decided_by"] == "clear"  # line 3 is unclear (gate 1), line 2 impossible (gate 2)


def test_limits_apply_to_the_basket_total():
    # 300 L milk (€303) + 30 kg butter (€285): each under the €500 the agent may spend alone, together not.
    res = run("We need 300 L lactose-free milk and 30 kg butter by Monday")
    assert (res["ladder"]["action"], res["ladder"]["decided_by"]) == ("person_approves", "authority")
    auto = next(r for r in res["order"]["policy"]["rules"] if r["rule"] == "Auto-approve spend limit")
    assert not auto["ok"]


def test_hard_limit_on_the_total_stops_before_an_offer():
    res = run("We need 300 L lactose-free milk and 30 kg butter by Monday",
              overrides={"hard_limits.max_order_total_eur": 400})
    assert (res["ladder"]["action"], res["result"]["status"]) == ("stop", "over_hard_limit")
    assert "line 1" in res["result"]["error"] and not any(s["step"] == "offer" for s in res["steps"])


def test_requirements_stay_on_their_own_line():
    # Lactose-free is asked for the milk only: the butter (which states no lactose value) isn't held to it.
    res = run("We need 100 L lactose-free milk and 20 kg butter by Monday")
    claims = next(r for r in res["order"]["policy"]["rules"] if r["rule"] == "Required claims")
    assert claims["ok"], claims


def test_a_requirement_that_belongs_to_no_line_goes_back():
    res = run("100 L milk and 30 L cream, both lactose-free, by Monday")
    assert res["result"]["status"] == "unclear_request" and "both lactose-free" in res["result"]["error"]


def test_different_delivery_days_go_back():
    res = run("100 L lactose-free milk by Monday and 20 kg butter by Wednesday")
    assert res["result"]["status"] == "several_dates"


def test_lines_that_land_on_one_product_go_back():
    res = run("100 L lactose-free milk and 50 L Eila lactose-free milk by Monday")
    assert res["result"]["status"] == "same_product_twice"


def test_allergy_rules_cover_every_line():
    res = run("100 L oat drink and 20 kg butter by Monday", mandate="mnd_hospital_ward")
    assert res["ladder"]["action"] == "ask_back" and res["ladder"]["decided_by"] == "allowed"
    assert "line 2" in res["result"]["error"] and "butter" in res["result"]["error"]


def test_an_offer_line_outside_the_basket_is_refused():
    res = run("We need 100 L lactose-free milk and 20 kg butter by Monday")
    offer = dict(res["order"]["record"]["offer"])
    offer["lines"] = offer["lines"] + [dict(offer["lines"][0], productId="463380", name="Arla Cafè milk")]
    verdict = policy.evaluate(policy.load(HOTEL), offer, {"deliver_by": offer["deliver_by"]})
    bad = [r for r in verdict["rules"] if r["rule"] == "Matches the buyer's request" and not r["ok"]]
    assert bad and "463380" in bad[0]["detail"] and verdict["outcome"] == "blocked"
