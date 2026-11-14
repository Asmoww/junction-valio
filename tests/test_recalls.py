"""Live recall check: a current Food Authority notice naming the product's brand and type stops the order."""
from datetime import date

import pytest

from gateway import recalls

pytestmark = pytest.mark.recalls

LIST = """<main><a href="/elintarvikkeet/ohjeita-kuluttajille/takaisinvedot-2023-/kananmunien-takaisinveto2/">Kananmunien takaisinveto</a>
<a href="/elintarvikkeet/ohjeita-kuluttajille/takaisinvedot-2023-/vanha-maito/">Maidossa vanha ongelma</a>
<a href="/elintarvikkeet/ohjeita-kuluttajille/takaisinvedot-2023-/pastakauha/">Pastakauhasta siirtyy amiineja</a></main>"""
EGGS = "<main><h1>Kananmunien takaisinveto</h1> 9. lokakuuta 2026 Munax Oy ilmoittaa... Laitilan Kanatarha Ulko L6 kananmunat</main>"
OLD = "<main>Maidossa vanha ongelma 3. tammikuuta 2025 Valio maito</main>"
EGG_PRODUCT = {"name_fi": "Laitilan Kanatarha kananmunat L6", "brand": "Laitilan Kanatarha"}
MILK = {"name_fi": "Valio kevytmaitojuoma 1 l laktoositon", "brand": "Valio"}


@pytest.fixture
def web(monkeypatch):
    pages = {recalls.LIST_URL: LIST}
    pages[recalls.BASE + "/elintarvikkeet/ohjeita-kuluttajille/takaisinvedot-2023-/kananmunien-takaisinveto2/"] = EGGS
    pages[recalls.BASE + "/elintarvikkeet/ohjeita-kuluttajille/takaisinvedot-2023-/vanha-maito/"] = OLD
    monkeypatch.setattr(recalls, "_fetch", lambda url: pages[url])
    monkeypatch.setattr(recalls, "_cache", {})
    return pages


def test_a_notice_naming_brand_and_type_sends_it_to_a_person(web):
    r = recalls.check(EGG_PRODUCT, today=date(2026, 11, 14))
    assert r["status"] == "possible" and r["source"].endswith("kananmunien-takaisinveto2/")
    assert r["title"] == "Kananmunien takaisinveto"


def test_a_notice_naming_the_exact_product_recalls(web, monkeypatch):
    exact = {"name_fi": "Laitilan Kanatarha Ulko L6 kananmunat", "brand": "Laitilan Kanatarha"}
    web[recalls.BASE + "/elintarvikkeet/ohjeita-kuluttajille/takaisinvedot-2023-/kananmunien-takaisinveto2/"] = (
        "<main><h1>Kananmunien takaisinveto</h1> 9. lokakuuta 2026 Tuotteet: Laitilan Kanatarha Ulko L6 kananmunat 408 g</main>")
    assert recalls.check(exact, today=date(2026, 11, 14))["status"] == "recalled"


def test_an_old_notice_does_not_count(web):
    assert recalls.check(MILK, today=date(2026, 11, 14))["status"] == "clear"


def test_an_unreachable_source_is_unknown_not_clear(monkeypatch):
    monkeypatch.setattr(recalls, "_cache", {})
    monkeypatch.setattr(recalls, "_fetch", lambda url: (_ for _ in ()).throw(OSError("down")))
    assert recalls.check(MILK)["status"] == "unknown"


def test_a_recall_stops_the_order_at_the_hard_rules(web, monkeypatch):
    from gateway import agent
    from .conftest import FakeValio
    token = recalls.TEST.set(True)
    try:
        _, jws, _ = agent.quote("mnd_hotel_helsinki", "2026-11-16", [{"productId": "464306", "units": 10}], FakeValio())
        order, _ = agent.order_from_jws(jws, "k-recall-test")
    finally:
        recalls.TEST.reset(token)
    assert order["status"] == "blocked"
    rule = next(r for r in order["policy"]["rules"] if r["rule"] == "Not recalled")
    assert not rule["ok"] and "TEST RECALL" in rule["detail"]
