"""Test setup: a temporary state folder, a tiny fixed catalog and a fake Valio client (no network)."""
import os
import tempfile

os.environ["TG_STATE_DIR"] = tempfile.mkdtemp(prefix="tg-test-")
os.environ["DEMO_PASSWORD"] = "test-password"
os.environ["DEMO_CLOCK"] = ""  # offline tests use Valio's real clock, whatever the server's .env says

import pytest  # noqa: E402

from gateway import agent, ai, catalog  # noqa: E402

NOW = "2026-11-13T20:00:00+0200"


def product(pid, name, unit="ST", lactose=0.0, contains=("AM",), lot=0.01):
    return {"id": pid, "name": name, "name_fi": name, "brand": "Test", "vendor": "Test", "category": "10400",
            "salesUnit": unit, "baseUnit": unit, "lot": lot, "units": {}, "origin": "fi", "contains": list(contains),
            "freeFrom": [], "claims": [], "labels": [], "netWeightKg": None, "lactose": lactose,
            "reliability": 1.0, "carbonKgPerKg": 1.1, "substitutes": [], "_text": catalog.fold(name),
            "allergenData": True, "mayContain": [], "undeclared": [], "fatPct": None}


PRODUCTS = [
    product("464306", "Eila light milk drink 1 l lactose free"),
    product("464073", "Whole milk 1 l lactose free UHT"),
    product("463203", "Portion milk 100x2 cl lactose free", lactose=None),
    product("999001", "Terminal oat drink 1 l lactose free"),
    product("463380", "Arla Cafè milk 1 l lactose free UHT"),
    product("700001", "Chili cheese chicken nuggets 1 kg", unit="KG", lactose=None),
]


@pytest.fixture(autouse=True)
def fixed_catalog(monkeypatch):
    monkeypatch.setattr(catalog, "_catalog", PRODUCTS)
    monkeypatch.setattr(catalog, "_by_id", {p["id"]: p for p in PRODUCTS})
    monkeypatch.setattr(catalog, "_vocab", None)


class FakeValio:
    """Answers like the Valio mock: price per sales unit, availability with schedule lines."""

    def __init__(self, prices=None, closing="2026-11-14T15:00:00+0200", terminal=("999001",)):
        self.price = {"464306": 1.01, "464073": 1.64, "463203": 20.0, "999001": 1.50, "463380": 2.65, "700001": 9.0,
                      **(prices or {})}
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
def no_claude(monkeypatch):
    """Offline tests never call the Claude API (the server's .env may hold a real key): rules only."""
    monkeypatch.setattr(ai, "parse", lambda text, today=None: None)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Code paths that create their own Valio client get the fake one."""
    monkeypatch.setattr(agent, "ValioClient", FakeValio)


@pytest.fixture
def valio():
    return FakeValio()


@pytest.fixture(autouse=True)
def no_twin_check(request, monkeypatch):
    """Tests share one database, so earlier tests leave identical orders waiting. The duplicate-order check is
    tested on its own (marker twin_check)."""
    if not request.node.get_closest_marker("twin_check"):
        monkeypatch.setattr(agent, "_waiting_twin", lambda offer: None)
