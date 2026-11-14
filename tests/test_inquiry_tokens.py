"""An inquiry-only agent token can look up and price, never order (Codex review: inquiry wasn't token-wide)."""
from gateway import agent, store

from .conftest import FakeValio

HOTEL = "mnd_hotel_helsinki"


def test_an_inquiry_token_can_price_but_never_order():
    t = agent.READ_ONLY.set(True)
    try:
        offer, jws, err = agent.quote(HOTEL, "2026-11-16", [{"productId": "464306", "units": 10}], FakeValio())
        assert offer and err is None  # pricing is an inquiry
        order, err = agent.order_from_jws(jws, "k-inquiry-only")
        assert order is None and "inquiries only" in err
    finally:
        agent.READ_ONLY.reset(t)
    assert any(e["type"] == "security.read_only_order_refused" for e in store.events(limit=50))
    assert not [o for o in store.list_orders(1000) if o.get("offer_id") == offer["offer_id"]]


def test_a_purchase_token_still_orders():
    _, jws, _ = agent.quote(HOTEL, "2026-11-16", [{"productId": "464306", "units": 10}], FakeValio())
    order, err = agent.order_from_jws(jws, "k-purchase-token")
    assert order and err is None
