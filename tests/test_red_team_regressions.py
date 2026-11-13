"""
Adversarial regression tests from an outside red-team review (ChatGPT, 13 Nov 2026, of main at 8af1a39).

Adapted to this branch where the review's fixtures didn't match the code (marked "adapted:"); the property
each test checks is unchanged.

Usage inside the REPOSITORY ROOT, after installing project dependencies:
    uv run pytest -q tests/test_red_team_regressions.py

These are deliberately *acceptance tests*: they assert the safety behavior claimed
in FRAMEWORK.md / ASSUMPTIONS.md. Several are expected to FAIL against the
inspected main branch. No Valio credentials or live API calls are needed.
"""
from copy import deepcopy

import pytest

from gateway import agent, catalog, policy


MANDATE = {
    "mandate_id": "test_hotel",
    "customer": "123",
    "agent": "test_agent",
    "valid_until": "2099-12-31",
    "auto_approve": {"max_order_total_eur": 500},
    "hard_limits": {
        "max_order_total_eur": 5000,
        "forbidden_allergens": [],
        "required_claims": [],
    },
    "substitutions": {
        "official_substitute": "with_approval",
        "other_product": "with_approval",
    },
    "min_delivery_reliability": 0.95,
    "approvers": [{"role": "Head chef", "name": "Aino", "up_to_eur": 5000}],
    "weights": {"price": 0.6, "reliability": 0.3, "carbon": 0.1},
}

PRODUCT = {
    "id": "testmilk",
    "name": "Test lactose-free milk",
    "salesUnit": "PCS",
    "contains": [],
    "claims": [],
    "reliability": None,
    "carbonKgPerKg": 1.1,
    # adapted: fields every real catalog entry has (build_catalog.py)
    "_text": "test lactose-free milk", "lot": 0.01, "labels": [], "substitutes": [], "category": "test",
    "allergenData": False, "mayContain": [], "undeclared": [], "lactose": None, "fatPct": None,
}

class FakeClient:
    def __init__(self, *, terminal=False):
        self.terminal = terminal

    def prices(self, customer, date, ids):
        return {pid: {"catalogPrice": {"value": 1.01, "validTo": "2099-12-31"},
                      "comparisonPrice": {"value": 1.01},
                      "comparisonUnit": "PCS"} for pid in ids}

    def availability(self, customer, date, ids):
        return ("2026-11-12T12:00:00+02:00",
                {pid: {"logisticModel": "Terminal" if self.terminal else "Stock",
                       "scheduleLines": [{
                           "availableDate": "2026-11-16",
                           "availableAmount": 999999 if self.terminal else 1000
                       }]} for pid in ids})


@pytest.mark.parametrize("bad_units", [-10, 0, 0.5])
def test_quote_rejects_negative_zero_and_fractional_sales_units(monkeypatch, bad_units):
    monkeypatch.setattr(agent.policy, "load", lambda mandate_id: deepcopy(MANDATE))
    monkeypatch.setattr(agent.catalog, "get", lambda pid: deepcopy(PRODUCT))
    # Avoid writing to disk; if an offer is reached it means invalid units got through.
    monkeypatch.setattr(agent, "create_offer", lambda m, option, intent: (option, "dummy.jws"))
    offer, jws, error = agent.quote(
        "test_hotel", "2026-11-16", [{"productId": "testmilk", "units": bad_units}],
        client=FakeClient()
    )
    assert offer is None and error, (
        f"Unsafe quote accepted for units={bad_units!r}: {offer}"
    )


def test_terminal_unknown_stock_requires_human_fact_check(monkeypatch):
    monkeypatch.setattr(agent.policy, "load", lambda _: deepcopy(MANDATE))
    monkeypatch.setattr(agent.catalog, "get", lambda _: deepcopy(PRODUCT))
    monkeypatch.setattr(agent, "create_offer", lambda m, option, intent: (option, "dummy.jws"))
    offer, jws, err = agent.quote(
        "test_hotel", "2026-11-16", [{"productId": "testmilk", "units": 50}],
        client=FakeClient(terminal=True)
    )
    # Quote may exist, but it must carry a trustworthy "unknown stock" condition
    # for gate 4 instead of representing 999,999 as verified stock.
    # adapted: this code marks it stockConfirmed=False on the offer line; policy rule "Stock confirmed by Valio"
    assert err or (offer and any(
        line.get("stock_status") == "unknown" or line.get("unconfirmed_stock") or line.get("stockConfirmed") is False
        for line in offer["lines"]
    )), f"Unknown Terminal stock passed without a trust flag: {offer}"
    monkeypatch.setattr(policy, "get", lambda _: deepcopy(PRODUCT))  # adapted: policy reads the catalog too
    v = policy.evaluate(deepcopy(MANDATE), {"total_eur": 50.5, "lines": offer["lines"]}, {})
    assert v["outcome"] != "auto_approve"


def test_unverified_human_name_cannot_approve(monkeypatch):
    pending = {
        "id": "ord_test", "status": "pending_approval",
        "policy": {"approver": {"name": "Aino", "role": "Head chef", "up_to_eur": 5000}},
        "record": {}, "total": 100, "mandate_id": "test_hotel"
    }
    saved = []
    monkeypatch.setattr(agent.store, "get_order", lambda oid: deepcopy(pending))
    monkeypatch.setattr(agent.store, "save_order", lambda order: saved.append(deepcopy(order)))
    monkeypatch.setattr(agent.store, "log", lambda *args, **kwargs: None)
    order, error = agent.decide("ord_test", "approve", "Mallory")
    assert error or order is None or order["status"] != "placed", (
        "A caller merely supplying 'Mallory' was accepted as an authorized approver"
    )


def test_same_signed_offer_cannot_be_committed_twice_with_different_keys(monkeypatch):
    payload = {
        "offer_id": "ofr_test",
        "expires_at": "2099-12-31T23:59:59+00:00",
        "deliver_by": "2026-11-16",
        "lines": [{"productId": "testmilk", "unitPrice": 1.01, "units": 5}],
        "total_eur": 5.05,
    }
    # adapted: uses the real (temporary) order store, because the check reads it; the review's version faked
    # save_order, so no implementation could have seen the first order.
    payload["offer_id"] = "ofr_test_replay"
    monkeypatch.setattr(agent.signing, "verify", lambda token: (True, deepcopy(payload)))
    monkeypatch.setattr(agent.policy, "evaluate", lambda *args: {
        "outcome": "auto_approve", "approver": None, "rules": [],
    })
    a, e1 = agent.place_order("signed", "key-one", deepcopy(MANDATE), {}, client=FakeClient())
    b, e2 = agent.place_order("signed", "key-two", deepcopy(MANDATE), {}, client=FakeClient())
    assert a and a["status"] == "placed"
    assert not (b and b["status"] == "placed"), "Same signed offer produced two placed orders"
    assert b is None and "already used" in e2


def test_missing_allergen_evidence_does_not_mean_allergen_free(monkeypatch):
    mandate = deepcopy(MANDATE)
    mandate["hard_limits"]["forbidden_allergens"] = ["AM"]
    monkeypatch.setattr(policy, "get", lambda pid: deepcopy(PRODUCT))
    offer = {"total_eur": 10, "lines": [{"productId": "testmilk", "units": 10,
                                         "reliability": 1.0}]}
    verdict = policy.evaluate(mandate, offer, {})
    assert verdict["outcome"] != "auto_approve", (
        "Unknown allergen data incorrectly treated as proven safe"
    )


def test_missing_delivery_reliability_requires_fact_check(monkeypatch):
    monkeypatch.setattr(policy, "get", lambda pid: deepcopy(PRODUCT))
    offer = {"total_eur": 10, "lines": [{"productId": "testmilk", "units": 10,
                                         "reliability": None}]}
    verdict = policy.evaluate(deepcopy(MANDATE), offer, {})
    assert verdict["outcome"] != "auto_approve", (
        "Unknown reliability bypassed 'Proven?' under a reliability-minimum mandate"
    )


def test_24_hour_cumulative_authority_cannot_be_split(monkeypatch):
    monkeypatch.setattr(policy, "get", lambda pid: deepcopy(PRODUCT))
    offer = {"total_eur": 400, "lines": [{"productId": "testmilk", "units": 400,
                                          "reliability": 1.0}]}
    # €400 already ordered autonomously in the last day; another €400 must
    # need approval if the auto authority is capped at €500 cumulatively.
    verdict = policy.evaluate(deepcopy(MANDATE), offer, {"auto_spend_24h": 400})  # adapted: this code's field name
    assert verdict["outcome"] != "auto_approve", (
        "Two individually small orders bypass the documented 24-hour authority"
    )


def test_external_order_preserves_quantity_for_buyer_intent_checks(monkeypatch):
    payload = {"mandate_id": "test_hotel", "deliver_by": "2026-11-16",
               "lines": [{"productId": "testmilk", "units": 500,
                          "deliveryDate": "2026-11-16"}]}
    seen = {}
    monkeypatch.setattr(agent.signing, "verify", lambda token: (True, deepcopy(payload)))
    monkeypatch.setattr(agent.policy, "load", lambda mid: deepcopy(MANDATE))
    def fake_place_order(jws, key, mandate, ctx, **kwargs):
        seen.update(ctx)
        return {"id": "ord_test"}, None
    monkeypatch.setattr(agent, "place_order", fake_place_order)
    agent.order_from_jws("signed", "key", requested_units=100)
    assert seen.get("requested_quantity") == 100, (
        "External order passes 'requested_units', but policy checks 'requested_quantity'"
    )


def test_volume_conversion_preserves_10_point_5_litre_pack():
    price = {"catalogPrice": {"value": 10.5},
             "comparisonPrice": {"value": 1.0}, "comparisonUnit": "L"}
    assert agent._per_unit(PRODUCT, price, "L") == 10.5, (
        "10.5 L became 10 L under the current 15% rounding tolerance"
    )


def test_unstated_quantity_is_not_silently_one_pack(monkeypatch):
    intent = agent.parse_request("milk")
    assert intent["quantity"] is None
    product = dict(PRODUCT, category="test", substitutes=[])
    monkeypatch.setattr(agent.catalog, "describe", lambda p: p)
    price = {"catalogPrice": {"value": 1.01, "validTo": "2099-12-31"},
             "comparisonPrice": {"value": 1.01}, "comparisonUnit": "PCS"}
    availability = {"scheduleLines": [{"availableDate": intent["deliver_by"],
                                       "availableAmount": 100}]}
    row = agent._assess(product, price, availability, intent,
                        "2026-11-12T12:00:00+02:00")
    assert row["status"] != "ok" or not row.get("units"), (
        "A buyer saying only 'milk' silently triggered a one-pack purchase"
    )


# ---- Further findings from the same review (not in its test file) ----------------------------------

def test_unknown_substitution_type_is_refused():
    offer = {"total_eur": 10, "lines": [{"productId": "464306", "units": 10, "reliability": 1.0,
                                         "substitution": "brand_new_type"}]}
    v = policy.evaluate(policy.load("mnd_hotel_helsinki"), offer, {})
    assert v["outcome"] == "blocked"


def test_external_agent_cannot_change_the_sealed_request(valio):
    _, jws, _ = agent.quote("mnd_hotel_helsinki", "2026-11-16", [{"productId": "464306", "units": 500}], valio,
                            requested_quantity=100)
    order, err = agent.order_from_jws(jws, "k-intent", requested_units=500)
    assert order is None and "signed offer says 100" in err
    order, _ = agent.order_from_jws(jws, "k-intent-2")  # with no claim, the sealed 100 is used
    assert order["status"] == "blocked"
    assert any(r["rule"] == "Matches the buyer's request" and not r["ok"] for r in order["policy"]["rules"])


def test_same_offer_sent_twice_at_the_same_moment_places_one_order(monkeypatch):
    """Added after the review's concurrency question: both calls must not pass the 'offer used once' check
    before either has saved its order."""
    import threading, time
    payload = {"offer_id": "ofr_test_race", "expires_at": "2099-12-31T23:59:59+00:00", "deliver_by": "2026-11-16",
               "lines": [{"productId": "testmilk", "unitPrice": 1.01, "units": 5}], "total_eur": 5.05}
    monkeypatch.setattr(agent.signing, "verify", lambda token: (True, deepcopy(payload)))
    monkeypatch.setattr(agent.policy, "evaluate", lambda *args: {"outcome": "auto_approve", "approver": None, "rules": []})

    class SlowClient(FakeClient):
        def prices(self, *args):
            time.sleep(0.05)  # widen the gap between the check and the save
            return super().prices(*args)

    results = []
    threads = [threading.Thread(target=lambda k=k: results.append(
        agent.place_order("signed", k, deepcopy(MANDATE), {}, client=SlowClient()))) for k in ("key-a", "key-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    placed = [o for o, _ in results if o and o["status"] == "placed"]
    assert len(placed) == 1, f"one signed offer placed {len(placed)} orders"
