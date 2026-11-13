"""Test setup: a temporary state folder, a tiny fixed catalog and a fake Valio client (no network)."""
import os
import tempfile

os.environ["TG_STATE_DIR"] = tempfile.mkdtemp(prefix="tg-test-")
os.environ["DEMO_PASSWORD"] = "test-password"

import pytest  # noqa: E402

from gateway import agent, catalog  # noqa: E402

NOW = "2026-11-13T20:00:00+0200"


def product(pid, name, unit="ST", lactose=0.0, contains=("AM",), lot=0.01):
    return {"id": pid, "name": name, "name_fi": name, "brand": "Test", "vendor": "Test", "category": "10400",
            "salesUnit": unit, "baseUnit": unit, "lot": lot, "units": {}, "origin": "fi", "contains": list(contains),
            "freeFrom": [], "claims": [], "labels": [], "netWeightKg": None, "lactose": lactose,
            "reliability": 1.0, "carbonKgPerKg": 1.1, "substitutes": [], "_text": name.lower()}


PRODUCTS = [
    product("464306", "Eila light milk drink 1 l lactose free"),
    product("464073", "Whole milk 1 l lactose free UHT"),
    product("463203", "Portion milk 100x2 cl lactose free", lactose=None),
    product("999001", "Terminal oat drink 1 l lactose free"),
]


@pytest.fixture(autouse=True)
def fixed_catalog(monkeypatch):
    monkeypatch.setattr(catalog, "_catalog", PRODUCTS)
    monkeypatch.setattr(catalog, "_by_id", {p["id"]: p for p in PRODUCTS})


class FakeValio:
    """Answers like the Valio mock: price per sales unit, availability with schedule lines."""

    def __init__(self, prices=None, closing="2026-11-14T15:00:00+0200", terminal=("999001",)):
        self.price = {"464306": 1.01, "464073": 1.64, "463203": 0.20, "999001": 1.50, **(prices or {})}
        self.closing, self.terminal, self.calls = closing, set(terminal), []

    def prices(self, customer, date, ids):
        self.calls.append(("price", list(ids)))
        return {i: {"productId": i, "catalogPrice": {"value": self.price[i], "validTo": "2026-12-31T00:00:00"},
                    "comparisonPrice": {"value": self.price[i]}, "comparisonUnit": "L"} for i in ids}

    def availability(self, customer, date, ids):
        self.calls.append(("availability", list(ids)))
        out = {}
        for i in ids:
            term = i in self.terminal
            out[i] = {"productId": i, "closingTime": self.closing, "logisticModel": "Terminal" if term else "Stock",
                      "scheduleLines": [{"availableAmount": 999999 if term else 5000, "availableDate": "2026-11-16",
                                         "salesUnit": "ST"}]}
        return NOW, out


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Code paths that create their own Valio client get the fake one."""
    monkeypatch.setattr(agent, "ValioClient", FakeValio)


@pytest.fixture
def valio():
    return FakeValio()
