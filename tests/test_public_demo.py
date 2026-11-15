"""Open demo for judging (PUBLIC_DEMO): pages open without a password, the API still needs a session or an agent token."""
from fastapi.testclient import TestClient

from gateway import main


def test_pages_open_and_agents_still_need_a_token(monkeypatch):
    monkeypatch.setattr(main, "PUBLIC_DEMO", True)
    c = TestClient(main.app, base_url="https://testserver")
    r = c.get("/checks")
    assert r.status_code == 200 and main._COOKIE in r.cookies
    bare = TestClient(main.app, base_url="https://testserver")  # an agent with no token and no browser session
    assert bare.post("/v1/approvals/ord_x", json={"decision": "approve", "approver": "Aino Virtanen"}).status_code == 401
    assert bare.post("/v1/orders", json={"offer_id": "x"}, headers={"Idempotency-Key": "k"}).status_code == 401
    assert bare.get("/login?next=/valio", follow_redirects=False).headers["location"] == "/valio"


def test_the_password_gate_is_back_when_the_switch_is_off(monkeypatch):
    monkeypatch.setattr(main, "PUBLIC_DEMO", False)
    r = TestClient(main.app, base_url="https://testserver").get("/checks", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
