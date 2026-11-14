"""Review of 14 Nov browser findings: budgets in the request (F1) and allergens a product IS (F3, F4)."""
import pytest

from gateway import agent, catalog, policy, request_checks as rc

from .conftest import FakeValio, product


@pytest.mark.parametrize("text, budget", [
    ("My total budget for this purchase is EUR 0.01", 0.01), ("Budget: €1.000,00.", 1000.0),
    ("budget of 1,000.50 euros", 1000.5), ("budget 5e", 5.0), ("Budget: EUR 1,5", 1.5), ("under 300 euros", 300.0)])
def test_budget_formats(text, budget):
    assert rc.stated_budget(text) == budget


@pytest.mark.parametrize("text, unread", [
    ("Order 30 L milk, €5 max.", True), ("Order 30 L milk for 5 euros or less.", True), ("budjetti 5 euroa", True),
    ("Order 30 L milk, budget €50.", False), ("Order 3 L of milk for the Euro 2026 party", False), ("Order 24 eggs", False)])
def test_money_the_gateway_cannot_read_is_never_ignored(text, unread):
    assert rc.unread_money(text) is unread


def test_unread_money_goes_back_to_the_requester():
    r = agent.run("Order 30 litres of lactose-free milk by Monday for 5 euros or less.", "mnd_hotel_helsinki")
    assert r.get("order") is None and r["result"]["status"] == "unread_budget"


BUTTER = product("900001", "Pro 1,8kg lactose free concentrated butter", contains=())
PEANUT = dict(product("900002", "PCD 500G SPREAD WITHOUT SUGAR", contains=()), allergenData=False,
              ingredients="MAAPÄHKINÄ (90%), AURINGONKUKKAÖLJY, SOKERI, SUOLA.")
OAT = product("900003", "Oddlygood oat drink 1 l", contains=())
NUTFREE = product("900004", "Zini Pesto nutfree frozen", contains=())


def test_a_product_is_what_its_name_says_whatever_its_data_says():
    hospital, school = policy.load("mnd_hospital_ward"), policy.load("mnd_school_espoo")
    assert policy.allergen_problem(hospital, BUTTER)[1] == "block"         # F4: butter declares no milk in the data
    assert "ingredients list peanuts" in policy.allergen_problem(school, PEANUT)[0]  # F3: no data, ingredients say peanut
    assert policy.allergen_problem(hospital, OAT) is None
    assert policy.allergen_problem(school, NUTFREE) is None


def test_an_outside_agent_cannot_order_butter_for_a_milk_ban(monkeypatch):
    butter = dict(BUTTER, id="464306")  # an id the fake Valio prices; its data declares no milk
    items = [butter if p["id"] == "464306" else p for p in catalog._catalog]
    monkeypatch.setattr(catalog, "_catalog", items)
    monkeypatch.setattr(catalog, "_by_id", {p["id"]: p for p in items})
    _, jws, _ = agent.quote("mnd_hospital_ward", "2026-11-16", [{"productId": "464306", "units": 1}], FakeValio())
    order, _ = agent.order_from_jws(jws, "k-review-butter")
    assert order["status"] == "blocked"
    rule = next(r for r in order["policy"]["rules"] if r["rule"] == "Forbidden allergens")
    assert not rule["ok"] and "butter" in rule["detail"]


def test_peanut_butter_request_is_a_peanut():
    assert rc.inherent_allergen("peanut butter", {"AP", "AN"}) == ("peanut", "AP")
    assert rc.inherent_allergen("peanut butter", {"AM"}) is None


def test_an_outside_agents_own_limits_are_sealed_and_enforced():
    """An outside agent's budget and allergy limits for this purchase travel inside the signed offer."""
    lines = [{"productId": "464306", "units": 50}]  # €50.50 with the fake prices
    offer, jws, err = agent.quote("mnd_hotel_helsinki", "2026-11-16", lines, FakeValio(), limits={"max_total_eur": 20})
    assert err is None and offer["request_limits"] == {"max_total_eur": 20.0}
    order, _ = agent.order_from_jws(jws, "k-review-limit-budget")
    assert order["status"] == "blocked"
    assert any(r["rule"] == "Within the buyer's stated budget" and not r["ok"] for r in order["policy"]["rules"])
    _, jws, _ = agent.quote("mnd_hotel_helsinki", "2026-11-16", [{"productId": "464306", "units": 51}], FakeValio(),
                            limits={"forbidden_allergens": ["milk"]})
    order, _ = agent.order_from_jws(jws, "k-review-limit-milk")
    assert order["status"] == "blocked"  # the hotel itself bans nothing; the agent's own milk ban holds


@pytest.mark.parametrize("bad", [{"max_total_eur": -1}, {"max_total_eur": "lots"}, {"forbidden_allergens": ["unicorn"]},
                                 {"raise_limit": 1}])
def test_request_limits_are_validated(bad):
    assert agent.request_limits(bad)[1]


def test_request_allergies_survive_to_the_approval_recheck(monkeypatch):
    """The approval re-check reads the stored context: request allergies must be in it, not only in the run's mandate."""
    from gateway import policy as pol
    seen = {}
    real = pol.evaluate
    monkeypatch.setattr(pol, "evaluate", lambda m, o, c, lookup=None: seen.update(c) or real(m, o, c, lookup))
    _, jws, _ = agent.quote("mnd_hotel_helsinki", "2026-11-16", [{"productId": "464306", "units": 52}], FakeValio(),
                            limits={"forbidden_allergens": ["peanuts"]})
    agent.order_from_jws(jws, "k-review-limit-ctx")
    assert seen.get("request_allergens") == ["AP"]
