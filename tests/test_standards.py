"""Open standards: the buyer-signed mandate credential and the Peppol BIS Ordering 3 export."""
import json
from xml.etree import ElementTree as ET

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from gateway import agent, main, policy, standards  # noqa: E402

from .conftest import FakeValio  # noqa: E402

NS = {"cbc": standards.CBC, "cac": standards.CAC}


def _tamper(token, edit):
    h, p, s = token.split(".")
    vc = json.loads(standards._unb64(p))
    edit(vc)
    return f"{h}.{standards._b64(json.dumps(vc).encode())}.{s}"


def test_mandate_credential_verifies_and_rejects_edits():
    m = policy.mandates()["mnd_hotel_helsinki"]
    token = standards.credential(m)
    ok, vc = standards.verify(token, m)
    assert ok and vc["issuer"]["id"] == "did:web:valio.psavela.com:buyers:hotel_helsinki"

    raised = _tamper(token, lambda vc: vc["credentialSubject"]["mandate"]["hard_limits"].update(max_order_total_eur=1e6))
    assert standards.verify(raised) == (False, "signature does not match the buyer's key")

    other_buyer = standards.credential(policy.mandates()["mnd_school_espoo"])
    assert standards.verify(other_buyer, m) == (False, "mandate differs from what the buyer signed")


def test_an_edited_mandate_blocks_orders_until_the_buyer_signs_again(monkeypatch):
    real = policy.mandates()
    edited = json.loads(json.dumps(real))
    edited["mnd_hotel_helsinki"]["auto_approve"]["max_order_total_eur"] = 4000
    monkeypatch.setattr(policy, "mandates", lambda: edited)
    _, jws, _ = agent.quote("mnd_hotel_helsinki", "2026-11-16", [{"productId": "464306", "units": 10}], FakeValio())
    order, _ = agent.order_from_jws(jws, "k-std-edited")
    assert order["status"] == "blocked"
    rule = next(r for r in order["policy"]["rules"] if r["rule"] == "Mandate valid")
    assert "differs from what the buyer signed" in rule["detail"]


def test_verify_endpoint_is_open_and_catches_forgery():
    c = TestClient(main.app, base_url="https://testserver")
    token = standards.credential(policy.mandates()["mnd_hospital_ward"])
    assert c.post("/v1/mandates/verify", json={"credential": token}).json()["valid"] is True
    forged = _tamper(token, lambda vc: vc["credentialSubject"]["mandate"]["hard_limits"].update(forbidden_allergens=[]))
    r = c.post("/v1/mandates/verify", json={"credential": forged}).json()
    assert r["valid"] is False and "signature" in r["error"]
    assert c.get("/buyers/hospital_ward/did.json").json()["id"].endswith(":buyers:hospital_ward")


def test_placed_order_exports_as_peppol_order():
    _, jws, _ = agent.quote("mnd_hotel_helsinki", "2026-11-16", [{"productId": "464306", "units": 12}], FakeValio())
    order, _ = agent.order_from_jws(jws, "k-std-peppol")
    assert order["status"] == "placed"
    c = TestClient(main.app, base_url="https://testserver")
    c.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    r = c.get(f"/v1/orders/{order['id']}/peppol.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(r.content)
    assert root.tag == f"{{{standards.UBL}}}Order"
    assert root.findtext("cbc:ProfileID", namespaces=NS) == "urn:fdc:peppol.eu:poacc:bis:ordering:3"
    line = root.find("cac:OrderLine/cac:LineItem", NS)
    assert line.find("cbc:Quantity", NS).text == "12" and line.find("cbc:Quantity", NS).get("unitCode") == "H87"
    assert line.findtext("cac:Item/cac:SellersItemIdentification/cbc:ID", namespaces=NS) == "464306"
    total = float(root.findtext("cac:AnticipatedMonetaryTotal/cbc:PayableAmount", namespaces=NS))
    assert total == pytest.approx(order["total"])


def test_pending_order_has_no_peppol_document_yet():
    _, jws, _ = agent.quote("mnd_hotel_helsinki", "2026-11-16", [{"productId": "464306", "units": 900}], FakeValio())
    order, _ = agent.order_from_jws(jws, "k-std-pending")
    c = TestClient(main.app, base_url="https://testserver")
    c.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
    assert c.get(f"/v1/orders/{order['id']}/peppol.xml").status_code == 409
