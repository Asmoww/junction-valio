"""HTTP-level checks: an agent's Bearer token can't approve; a signed-in person can."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from gateway import agent, main  # noqa: E402

from .conftest import FakeValio  # noqa: E402


def test_agent_token_cannot_approve_but_a_person_can():
    _, jws, _ = agent.quote("mnd_hotel_helsinki", "2026-11-16", [{"productId": "464306", "units": 900}], FakeValio())
    order, _ = agent.order_from_jws(jws, "k-api-approve")
    assert order["status"] == "pending_approval"
    body = {"decision": "approve", "approver": "Aino Virtanen"}
    agent_auth = {"Authorization": f"Bearer {main.AGENT_TOKEN}"}

    c = TestClient(main.app, base_url="https://testserver")
    r = c.post(f"/v1/approvals/{order['id']}", json=body, headers=agent_auth)
    assert r.status_code == 403

    c.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    r = c.post(f"/v1/approvals/{order['id']}", json=body)
    assert r.status_code == 200 and r.json()["status"] == "placed"


def test_login_does_not_redirect_off_site():
    c = TestClient(main.app, base_url="https://testserver")
    r = c.post("/login", data={"password": "test-password", "next": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/"


def test_the_agent_token_is_not_the_sign_in_password():
    """ChatGPT's hostile review: with one shared secret an agent could sign in as a person and approve its own order."""
    assert main.AGENT_TOKEN and main.AGENT_TOKEN != "test-password"
    c = TestClient(main.app, base_url="https://testserver")
    r = c.post("/login", data={"password": main.AGENT_TOKEN, "next": "/"}, follow_redirects=False)
    assert "err=1" in r.headers["location"]
    r = c.get("/v1/products/search?q=milk", headers={"Authorization": "Bearer test-password"})
    assert r.status_code == 401  # the password is not an agent token either


def test_an_agent_cannot_raise_its_own_auto_limit(monkeypatch):
    """Agents can't reach the demo's /api at all (only /v1 and /mcp), so they can't run with a raised limit."""
    seen = {}
    monkeypatch.setattr(agent, "run", lambda text, m, overrides, **kw: seen.update(overrides) or {"ok": True})
    c = TestClient(main.app, base_url="https://testserver")
    r = c.post("/api/run", json={"request": "x", "mandate_id": "mnd_hotel_helsinki", "auto_limit": 1200},
               headers={"Authorization": f"Bearer {main.AGENT_TOKEN}"})
    assert r.status_code == 403 and not seen


def test_an_agent_can_order_by_offer_id():
    """A live Claude Code agent mangled the long JWS when copying it; the offer id is enough."""
    offer, _, _ = agent.quote("mnd_hotel_helsinki", "2026-11-16", [{"productId": "464306", "units": 5}], FakeValio())
    c = TestClient(main.app, base_url="https://testserver")
    auth = {"Authorization": f"Bearer {main.AGENT_TOKEN}", "Idempotency-Key": "k-by-offer-id"}
    r = c.post("/v1/orders", json={"offer_id": offer["offer_id"]}, headers=auth)
    assert r.status_code == 200 and r.json()["offer_id"] == offer["offer_id"]
    assert c.post("/v1/orders", json={"offer_id": "ofr_nope"}, headers=auth | {"Idempotency-Key": "k2"}).status_code == 422


@pytest.mark.twin_check
def test_an_identical_order_is_refused_while_the_first_waits_for_approval():
    """Scoreboard finding: an agent ordered, 'asked a person', then ordered the same again."""
    lines = [{"productId": "464073", "units": 777}]
    _, jws1, _ = agent.quote("mnd_hotel_helsinki", "2026-11-16", lines, FakeValio())
    first, _ = agent.order_from_jws(jws1, "k-twin-1")
    assert first["status"] == "pending_approval"
    _, jws2, _ = agent.quote("mnd_hotel_helsinki", "2026-11-16", lines, FakeValio())
    second, err = agent.order_from_jws(jws2, "k-twin-2")
    assert second is None and first["id"] in err
    assert agent.order_from_jws(jws1, "k-twin-1")[0]["id"] == first["id"]  # same key still returns the original


def test_inquiry_token_prices_but_gets_insufficient_scope_on_order(monkeypatch):
    """Codex review (inquiry wasn't token-wide) + MCP authorization spec: an inquiry token has no orders:place."""
    monkeypatch.setattr(agent, "ValioClient", lambda: FakeValio())
    c = TestClient(main.app, base_url="https://testserver")
    inquiry = {"Authorization": f"Bearer {main.mandate_token('mnd_hotel_helsinki', 'inquiry')}"}
    r = c.post("/v1/quotes", json={"mandate_id": "mnd_hotel_helsinki", "deliver_by": "2026-11-16",
                                   "lines": [{"productId": "464306", "units": 10}]}, headers=inquiry)
    assert r.status_code == 200, r.text
    r = c.post("/v1/orders", json={"offer_id": r.json()["offer"]["offer_id"]},
               headers={**inquiry, "Idempotency-Key": "k-api-inquiry"})
    assert r.status_code == 403 and 'error="insufficient_scope"' in r.headers["www-authenticate"]


def test_protected_resource_metadata_and_401_challenge():
    c = TestClient(main.app, base_url="https://testserver")
    meta = c.get("/.well-known/oauth-protected-resource").json()
    assert "orders:place" in meta["scopes_supported"] and meta["resource"].endswith("/mcp")
    r = c.get("/v1/products/search?q=milk")
    assert r.status_code == 401 and "resource_metadata=" in r.headers["www-authenticate"]
