"""A receipt anyone can verify: every claim re-checked live, and a tampered order shows."""
from fastapi.testclient import TestClient

from gateway import agent, main, receipt, store

from .conftest import FakeValio


def placed(units):
    _, jws, _ = agent.quote("mnd_hotel_helsinki", "2026-11-16", [{"productId": "464306", "units": units}], FakeValio())
    order, err = agent.order_from_jws(jws, f"k-receipt-{units}")
    assert order and order["status"] == "placed", err
    return order


def test_a_receipt_verifies_and_needs_its_link():
    o = placed(7)
    r = receipt.verify(o["id"])
    assert r["all_ok"] and {c["check"] for c in r["checks"]} >= {"Price signed by Valio", "Rules signed by the buyer", "Audit trail intact"}
    c = TestClient(main.app, base_url="https://testserver")
    assert c.get(f"/r/{o['id']}?t={receipt.token(o['id'])}").status_code == 200   # public, no sign-in
    assert c.get(f"/r/{o['id']}?t=guess").status_code == 404


def test_a_tampered_total_shows_on_the_receipt():
    o = placed(8)
    stored = store.get_order(o["id"])
    stored["total"] = 1.0  # edited in the database
    store.save_order(stored)
    r = receipt.verify(o["id"])
    assert not r["all_ok"]
    assert not next(c for c in r["checks"] if c["check"] == "Order matches the signed offer")["ok"]
