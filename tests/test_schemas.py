"""Typed /v1 request bodies (review finding F7): the OpenAPI document tells agents what to send, and malformed
bodies get a 422 that names the field instead of a server error."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from gateway import main  # noqa: E402

AUTH = {"Authorization": f"Bearer {main.AGENT_TOKEN}"}


def _client():
    return TestClient(main.app, base_url="https://testserver")


def test_the_openapi_document_types_the_quote_order_and_approval_bodies():
    spec = _client().get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    quote = schemas["QuoteRequest"]
    assert set(quote["required"]) == {"mandate_id", "deliver_by", "lines"}
    line = schemas["QuoteLine"]
    assert line["additionalProperties"] is False and "units" in line["required"]
    assert {"OrderRequest", "ApprovalRequest", "VerifyOfferRequest"} <= set(schemas)
    body = spec["paths"]["/v1/quotes"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert body["$ref"].endswith("/QuoteRequest")


@pytest.mark.parametrize("body, field", [
    ({"mandate_id": "mnd_hotel_helsinki", "lines": [{"productId": "464306", "units": 5}]}, "deliver_by"),
    ({"mandate_id": "mnd_hotel_helsinki", "deliver_by": "next monday", "lines": [{"productId": "464306", "units": 5}]}, "deliver_by"),
    ({"mandate_id": "mnd_hotel_helsinki", "deliver_by": "2026-11-16", "lines": [{"productId": "464306", "units": "5"}]}, "units"),
    ({"mandate_id": "mnd_hotel_helsinki", "deliver_by": "2026-11-16", "lines": [{"productId": "464306", "units": True}]}, "units"),
    ({"mandate_id": "mnd_hotel_helsinki", "deliver_by": "2026-11-16", "lines": [{"productId": "464306", "units": 5, "unitPrice": 0.01}]}, "unitPrice"),
    ({"mandate_id": "mnd_hotel_helsinki", "deliver_by": "2026-11-16", "lines": []}, "lines"),
])
def test_a_malformed_quote_is_refused_with_the_field_named(body, field):
    r = _client().post("/v1/quotes", json=body, headers=AUTH)
    assert r.status_code == 422
    assert field in str(r.json()["detail"])


def test_a_non_numeric_requested_units_is_a_422_not_a_crash():
    r = _client().post("/v1/orders", json={"offer_id": "ofr_x", "requested_units": "lots"},
                       headers=AUTH | {"Idempotency-Key": "k-schema"})
    assert r.status_code == 422


def test_the_phone_approval_page_gives_the_whole_trust_reason():
    failed = [{"rule": "Earned trust", "detail": "New built-in agent (0 clean orders): may order alone up to €125 of the "
                                                  "buyer's €500; Proven after 5 clean orders"}]
    why = main._why_you(failed, {"total": 1010}, {"auto_approve": {"max_order_total_eur": 500}})
    assert why.endswith("up to €125 of the buyer's €500.")
