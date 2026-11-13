"""Attack and regression tests for the gateway's guardrails. Run: uv run pytest"""
from datetime import datetime, timedelta, timezone

import pytest

from gateway import agent, policy, security, store

from .conftest import FakeValio

HOTEL = "mnd_hotel_helsinki"
DATE = "2026-11-16"


def offer(lines, valio, mandate=HOTEL):
    o, jws, err = agent.quote(mandate, DATE, lines, valio)
    assert not err, err
    return o, jws


# ---- Malformed lines (the negative-quantity exploit) ----------------------------------------------

@pytest.mark.parametrize("lines, why", [
    ([{"productId": "464073", "units": 1000}, {"productId": "464306", "units": -900}], "positive"),
    ([{"productId": "464306", "units": 0.37}], "whole number"),
    ([{"productId": "464306", "units": 10}, {"productId": "464306", "units": 10}], "twice"),
    ([{"productId": "nope", "units": 10}], "unknown product"),
    ([{"productId": "464306", "units": float("nan")}], "positive"),
    ([{"productId": "464306", "units": True}], "positive"),
    ([], "at least one line"),
    ([{"productId": "464306", "units": 1}] * 21, "at most"),
])
def test_malformed_lines_are_refused_before_signing(lines, why, valio):
    o, jws, err = agent.quote(HOTEL, DATE, lines, valio)
    assert o is None and jws is None and why in err
    assert valio.calls == []  # refused before any Valio API call


# ---- Price lock ---------------------------------------------------------------------------------

def test_price_lock_ends_at_the_order_deadline():
    soon = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds")
    o, _ = offer([{"productId": "464306", "units": 100}], FakeValio(closing=soon))
    assert o["expires_at"] <= soon.replace("Z", "+00:00")
    assert o["lines"][0]["orderDeadline"] == soon


def test_offer_with_passed_deadline_is_refused():
    o, jws, err = agent.quote(HOTEL, DATE, [{"productId": "464306", "units": 10}],
                              FakeValio(closing="2026-11-12T15:00:00+0200"))
    assert o is None and "deadline" in err


# ---- Policy ------------------------------------------------------------------------------------

def test_unconfirmed_terminal_stock_needs_a_person(valio):
    o, jws = offer([{"productId": "999001", "units": 100}], valio)
    assert o["lines"][0]["stockConfirmed"] is False
    order, _ = agent.order_from_jws(jws, "k-terminal")
    assert order["status"] == "pending_approval"
    assert any(r["rule"] == "Stock confirmed by Valio" and not r["ok"] for r in order["policy"]["rules"])


def test_lactose_free_by_name_only_needs_a_person(valio):
    o, _ = offer([{"productId": "463203", "units": 10}], valio)
    v = policy.evaluate(policy.load(HOTEL), o, {"required_attributes": ["FREE_FROM_LACTOSE"]})
    assert any(r["rule"] == "Claims verified in product data" and not r["ok"] for r in v["rules"])
    o2, _ = offer([{"productId": "464306", "units": 10}], valio)
    v2 = policy.evaluate(policy.load(HOTEL), o2, {"required_attributes": ["FREE_FROM_LACTOSE"]})
    assert all(r["ok"] for r in v2["rules"] if r["rule"] == "Claims verified in product data")


def test_external_agent_cannot_order_far_more_than_it_asked_for(valio):
    _, jws = offer([{"productId": "464306", "units": 400}], valio)
    order, _ = agent.order_from_jws(jws, "k-overorder", requested_units=100)
    assert order["status"] == "blocked"
    assert any(r["rule"] == "Matches the buyer's request" and not r["ok"] for r in order["policy"]["rules"])


def test_idempotency_key_cannot_be_reused_for_another_offer(valio):
    _, jws1 = offer([{"productId": "464306", "units": 100}], valio)
    _, jws2 = offer([{"productId": "464306", "units": 200}], valio)
    first, _ = agent.order_from_jws(jws1, "k-same")
    again, note = agent.order_from_jws(jws1, "k-same")
    assert again["id"] == first["id"] and "idempotent" in note
    other, err = agent.order_from_jws(jws2, "k-same")
    assert other is None and "different offer" in err


def test_forged_offer_is_rejected(valio):
    _, jws = offer([{"productId": "464306", "units": 100}], valio)
    h, p, s = jws.split(".")
    order, err = agent.order_from_jws(f"{h}.{p}.{s[:-4]}AAAA", "k-forged")
    assert order is None and "signature" in err


# ---- Approvals -----------------------------------------------------------------------------------

def pending(valio, units=900):
    _, jws = offer([{"productId": "464306", "units": units}], valio)  # €909: above the €500 auto limit
    order, _ = agent.order_from_jws(jws, f"k-pending-{units}-{datetime.now().timestamp()}")
    assert order["status"] == "pending_approval"
    return order


def test_only_mandate_approvers_with_enough_authority_can_approve(valio):
    order = pending(valio)
    assert agent.decide(order["id"], "approve", "Mallory")[1].endswith("not an approver in the buyer's mandate")
    big = pending(valio, units=1450)  # €1,464.50 is fine for the head chef ...
    assert agent.decide(big["id"], "approve", "Aino Virtanen")[0]["status"] == "placed"
    _, jws = offer([{"productId": "464073", "units": 1000}], valio)  # ... €1,640 is not
    o, _ = agent.order_from_jws(jws, "k-big")
    assert "can approve up to" in agent.decide(o["id"], "approve", "Aino Virtanen")[1]
    assert agent.decide(o["id"], "approve", "Jari Laine")[0]["status"] == "placed"


def test_expired_lock_with_changed_price_is_not_placed(valio):
    order = pending(valio)
    rec = store.get_order(order["id"])
    rec["record"]["offer"]["expires_at"] = "2000-01-01T00:00:00+00:00"
    store.save_order(rec)
    o, err = agent.decide(order["id"], "approve", "Aino Virtanen", client=FakeValio(prices={"464306": 1.20}))
    assert o is None and "request a new offer" in err
    assert store.get_order(order["id"])["status"] == "expired"


def test_expired_lock_with_same_price_is_reconfirmed_and_placed(valio):
    order = pending(valio)
    rec = store.get_order(order["id"])
    rec["record"]["offer"]["expires_at"] = "2000-01-01T00:00:00+00:00"
    store.save_order(rec)
    o, _ = agent.decide(order["id"], "approve", "Aino Virtanen", client=FakeValio())
    assert o["status"] == "placed"
    assert any(e["type"] == "price.reconfirmed" for e in store.events(order["id"]))


def test_audit_chain_stays_intact():
    assert store.verify_chain()["valid"]


# ---- Request reading and units -------------------------------------------------------------------

@pytest.mark.parametrize("text, qty", [
    ("We need 1,000 litres of milk by Monday", 1000), ("We need 1.000 litres of milk", 1000),
    ("We need 1 000 l of milk", 1000), ("2,5 kg butter", 2.5), ("2.5 kg butter", 2.5),
])
def test_quantities_in_finnish_and_english_styles(text, qty):
    assert agent.parse_request(text)["quantity"] == qty


def test_unclear_request_does_not_default_to_milk():
    assert agent.parse_request("We need 300 litres by Monday")["product"] == ""


@pytest.mark.parametrize("catalog_price, per_litre, expected", [(31.31, 1.57, 20), (21.0, 2.0, 10.5), (0.9, 1.0, 0.9)])
def test_pack_size_from_prices(catalog_price, per_litre, expected):
    price = {"catalogPrice": {"value": catalog_price}, "comparisonPrice": {"value": per_litre}, "comparisonUnit": "L"}
    assert agent._per_unit({}, price, "L") == expected


# ---- Login redirect -----------------------------------------------------------------------------

@pytest.mark.parametrize("target, expected", [
    ("/valio", "/valio"), ("//evil.example", "/"), ("/\\evil.example", "/"), ("https://evil.example", "/"), ("", "/"),
])
def test_login_redirect_stays_on_site(target, expected):
    assert security.safe_next(target) == expected


# ---- End-to-end demo flow (CLAUDE.md: keep this green) ---------------------------------------------

def test_demo_flow_end_to_end():
    res = agent.run("We need 300 litres of lactose-free milk by Monday.", HOTEL)
    assert [s["step"] for s in res["steps"]][:5] == ["request", "lookup", "price_availability", "options", "offer"]
    order = res["order"]
    assert order["record"]["offer"]["lines"][0]["productId"] == "464306"  # cheapest verified milk, not the Terminal one
    assert order["status"] in ("placed", "pending_approval")
    assert store.verify_chain()["valid"]


def test_demo_flow_with_injection_is_still_contained():
    res = agent.run("We need 300 litres of lactose-free milk by Monday.", HOTEL, inject=True)
    attack = next(s for s in res["steps"] if s["step"] == "attack_result")
    assert attack.get("order") is None or attack["order"]["status"] == "blocked"


def test_unclear_request_stops_before_any_api_call():
    res = agent.run("We need 300 litres by Monday", HOTEL)
    assert res["result"]["status"] == "unclear_request" and res["api_calls"] == []


# ---- Reality check -------------------------------------------------------------------------------

def test_nonsense_product_stops_before_any_api_call():
    res = agent.run("We need 100 litres of chicken nugget milk by Monday.", HOTEL)
    assert res["result"]["status"] == "not_in_catalog" and res["api_calls"] == []
    assert "Nothing was ordered" in res["result"]["error"]
    assert "milk" in [s["phrase"] for s in res["result"]["suggestions"]]


def test_reality_check_can_only_narrow_never_add():
    from gateway import catalog
    seen = {}

    def judge(query, groups):
        seen.update(groups)
        return {"categories": ["10400", "99999"], "reason": "test"}  # 99999 isn't among the matches

    found, trace = catalog.search("milk", [], type_check=judge)
    assert seen and all(p["category"] == "10400" for p in found)
    assert trace["reality_check"]["kept_categories"] == ["10400", "99999"]

    found, trace = catalog.search("milk", [], type_check=lambda q, g: {"categories": [], "reason": "not milk"})
    assert found == [] and trace["reality_check"]["kept_categories"] == []


def test_unit_that_does_not_fit_the_product_stops_before_any_api_call():
    res = agent.run("We need 1,000 kg of lactose-free milk by Monday.", HOTEL)  # the test milks are sold by the litre
    assert res["result"]["status"] == "unit_mismatch" and res["api_calls"] == []
    assert res["result"]["fix"]["to"] == "L" and "Nothing was ordered" in res["result"]["error"]


def test_agent_does_not_ask_for_an_offer_above_the_hard_limit():
    res = agent.run("We need 4,000 litres of lactose-free milk by Monday.", HOTEL)  # €4,040+ ... fits
    assert res.get("order") is not None
    res = agent.run("We need 6,000 litres of lactose-free milk by Monday.", HOTEL)  # €6,060 > €5,000 hard limit
    assert res["result"]["status"] == "over_hard_limit" and "order" not in res
    assert res["result"]["fix"]["quantity"] <= 5000 / 1.01
