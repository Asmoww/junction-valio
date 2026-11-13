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

    c = TestClient(main.app, base_url="https://testserver")
    r = c.post(f"/v1/approvals/{order['id']}", json=body, headers={"Authorization": "Bearer test-password"})
    assert r.status_code == 403

    c.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    r = c.post(f"/v1/approvals/{order['id']}", json=body)
    assert r.status_code == 200 and r.json()["status"] == "placed"


def test_login_does_not_redirect_off_site():
    c = TestClient(main.app, base_url="https://testserver")
    r = c.post("/login", data={"password": "test-password", "next": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/"
