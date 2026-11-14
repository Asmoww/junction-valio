"""Independent-review regressions: preplanning is read-only and inquiry never commits."""
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from gateway import agent, policy, preplanner


def body(**changes):
    return preplanner.PreplanRequest(**{
        "purpose": "purchase", "mandate_id": "mnd_hotel_helsinki", "product": "milk",
        "quantity": 10, "unit": "litres", "deliver_by": (date.today() + timedelta(days=2)).isoformat(),
        "attributes": ["FREE_FROM_LACTOSE"], **changes})


def test_preplanning_never_creates_offer_or_order(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("preplanning attempted a commitment")
    monkeypatch.setattr(agent, "quote", forbidden)
    monkeypatch.setattr(agent, "create_offer", forbidden)
    monkeypatch.setattr(agent, "place_order", forbidden)
    before = policy.load("mnd_hotel_helsinki")
    result = preplanner.prepare(body())
    assert result.creates_order is False
    assert result.request_text.startswith("Order 10 litres of lactose-free milk")
    assert result.guardrails["buyer"]["hard_limits"]["max_order_total_eur"] == 5000
    assert result.guardrails["agent"]["per_order_eur"] == 500
    assert result.guardrails["agent"]["per_24h_eur"] == 1500
    assert "shared per mandate" in result.guardrails["agent"]["scope"]
    assert policy.load("mnd_hotel_helsinki") == before


@pytest.mark.parametrize("quantity, text", [(1000000, "1000000"), (123456.7, "123456.7"), (0.000001, "0.000001")])
def test_quantity_text_never_rounds_or_uses_exponent_notation(quantity, text):
    result = preplanner.prepare(body(quantity=quantity))
    assert result.request_text.startswith(f"Order {text} litres of ")


def test_inquiry_can_omit_quantity_and_date():
    result = preplanner.prepare(body(purpose="inquiry", quantity=None, deliver_by=None))
    assert result.purpose == "inquiry"
    assert result.request_text == "What is the price of lactose-free milk?"
    assert result.creates_order is False
    assert "never call place_order" in result.next_steps[0]


@pytest.mark.parametrize("change", [
    {"purpose": "buy"}, {"quantity": 0}, {"quantity": -1}, {"quantity": float("inf")},
    {"quantity": None}, {"deliver_by": None}, {"deliver_by": "2026-02-30"},
    {"attributes": ["TRUST_ME"]}, {"max_total_eur": 5},
])
def test_invalid_or_unsupported_constraints_are_not_silently_dropped(change):
    with pytest.raises(ValidationError):
        body(**change)


def test_allergy_preview_does_not_mutate_registered_mandate_or_report_it_as_registered():
    result = preplanner.prepare(body(allergies=["milk"]))
    assert result.products
    assert all(p["mandate_check"]["level"] == "block" for p in result.products)
    assert result.guardrails["buyer"]["hard_limits"]["forbidden_allergens"] == []
    assert policy.load("mnd_hotel_helsinki")["hard_limits"]["forbidden_allergens"] == []
    assert any("request_limits.forbidden_allergens" in w for w in result.warnings)


def test_preplanner_respects_token_scope():
    token = agent.CALLER.set("mnd_school_espoo")
    try:
        with pytest.raises(PermissionError):
            preplanner.prepare(body())
    finally:
        agent.CALLER.reset(token)


def test_explicit_inquiry_never_orders_even_when_text_says_order_and_attack_toggle_is_on(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("inquiry attempted an offer or order")
    monkeypatch.setattr(agent, "create_offer", forbidden)
    monkeypatch.setattr(agent, "place_order", forbidden)
    monkeypatch.setattr(agent, "_injection_attack", forbidden)
    result = agent.run("Order 10 litres of lactose-free milk by Monday.",
                       "mnd_hotel_helsinki", purpose="inquiry", inject=True)
    assert result["result"]["status"] == "answered"
    assert result["ladder"]["action"] == "answer"
    assert result["purpose"] == "inquiry"


def test_purchase_mode_does_not_override_a_question(monkeypatch):
    monkeypatch.setattr(agent, "create_offer", lambda *a, **k: pytest.fail("question became purchase"))
    result = agent.run("What is the price of 10 litres of lactose-free milk by Monday?",
                       "mnd_hotel_helsinki", purpose="purchase")
    assert result["result"]["status"] == "answered"


def test_rest_preplanner_and_demo_purpose_validation():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from gateway import main
    client = TestClient(main.app, base_url="https://testserver")
    auth = {"Authorization": f"Bearer {main.mandate_token('mnd_hotel_helsinki')}"}
    request = body(purpose="inquiry", quantity=None, deliver_by=None).model_dump(mode="json")
    response = client.post("/v1/preplan", json=request, headers=auth)
    assert response.status_code == 200
    assert response.json()["creates_order"] is False
    request["mandate_id"] = "mnd_school_espoo"
    assert client.post("/v1/preplan", json=request, headers=auth).status_code == 403
    assert client.post("/api/run", json={"request": "milk"}, headers=auth).status_code == 403
    client.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    assert client.post("/api/run", json={"request": "milk", "purpose": "surprise"}).status_code == 422


def test_mcp_preplanner_matches_shared_contract():
    pytest.importorskip("mcp")
    from gateway.mcp_server import preplan_request
    result = preplan_request("inquiry", "milk", "mnd_hotel_helsinki", attributes=["FREE_FROM_LACTOSE"])
    assert result["creates_order"] is False
    assert result["purpose"] == "inquiry"
    assert result["guardrails"]["request_limits"]["external_quote_budget_supported"] is True

