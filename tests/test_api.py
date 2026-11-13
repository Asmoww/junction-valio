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
    seen = {}
    monkeypatch.setattr(agent, "run", lambda text, m, overrides, **kw: seen.update(overrides) or {"ok": True})
    c = TestClient(main.app, base_url="https://testserver")
    c.post("/api/run", json={"request": "x", "mandate_id": "mnd_hotel_helsinki", "auto_limit": 1200},
           headers={"Authorization": f"Bearer {main.AGENT_TOKEN}"})
    assert seen["auto_approve.max_order_total_eur"] == 500
