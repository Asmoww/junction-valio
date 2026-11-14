"""Agent trust profile: every offer line says, per claim, how far an agent can rely on it."""
from gateway import agent, signing

from .conftest import FakeValio

HOTEL = "mnd_hotel_helsinki"


def claims(pid, client=None):
    _, jws, err = agent.quote(HOTEL, "2026-11-16", [{"productId": pid, "units": 10}], client or FakeValio())
    assert err is None, err
    ok, payload = signing.verify(jws)
    assert ok
    return {c["claim"]: c for c in payload["lines"][0]["trust"]["claims"]}


def test_every_line_carries_signed_claims_with_levels():
    c = claims("464306")
    assert set(c) >= {"price", "stock", "order_cutoff", "allergens", "origin", "delivery_reliability"}
    assert c["price"]["level"] == "verified" and c["price"]["source"] == "Valio price API" and c["price"]["as_of"]
    assert all(x["level"] in agent.TRUST_LEVELS for x in c.values())


def test_supplier_warehouse_stock_is_unknown_not_verified():
    assert claims("999001")["stock"]["level"] == "unknown"


def test_placeholder_stock_is_called_a_placeholder():
    class Placeholder(FakeValio):
        def availability(self, *a):
            now, out = super().availability(*a)
            for v in out.values():
                v["scheduleLines"][0]["availableAmount"] = 999999
            return now, out
    assert claims("464306", Placeholder())["stock"]["level"] == "placeholder"


def test_allergen_data_is_declared_never_verified():
    assert claims("464306")["allergens"]["level"] in ("declared", "unknown")


def test_the_spec_is_published_with_every_level():
    spec = agent.trust_profile_spec()
    assert set(spec["levels"]) == set(agent.TRUST_LEVELS) and spec["claims"] and spec["rules"]
