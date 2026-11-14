"""Typed request bodies for the open /v1 API, so the OpenAPI document (/docs, /openapi.json) tells an agent
exactly what to send.

The models check shape and types only. Every business check (known product, whole packs, delivery date not in
the past, request limits that may only tighten the mandate) still runs in agent.py, with the same error
messages for REST, MCP and A2A. Numbers are strict: "5" or true is refused rather than silently converted.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt

Number = StrictInt | StrictFloat


class QuoteLine(BaseModel):
    model_config = ConfigDict(extra="forbid")  # prices and totals come from Valio, never from the caller

    productId: str = Field(min_length=1, max_length=40, description="Valio product id from /v1/products/search.")
    units: Number = Field(gt=0, description="How many sales units (e.g. cartons). Whole packs for most products.")
    perUnit: Number | None = Field(default=None, gt=0, description="Optional: what one sales unit holds (litres, kg) "
                                   "as the agent understood it. The gateway refuses the line if Valio's data disagrees.")
    substitution: str | None = Field(default=None, max_length=40, description="Optional: the substitution type, if this "
                                     "line replaces what was asked for (e.g. official_substitute, other_product).")


class RequestLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_total_eur: Number | None = Field(default=None, ge=0, description="The most this purchase may cost.")
    forbidden_allergens: list[str] = Field(default_factory=list, max_length=20,
                                           description="Allergen codes (AM, AP, …) or names (milk, peanuts).")


class QuoteRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "mandate_id": "mnd_hotel_helsinki", "deliver_by": "2026-11-16",
        "lines": [{"productId": "463335", "units": 1000}], "requested_quantity": 1000, "requested_unit": "L"}]})

    mandate_id: str = Field(min_length=1, max_length=100, description="The buyer this agent acts for. Must match the "
                            "agent's token.")
    deliver_by: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="Latest delivery date, YYYY-MM-DD.")
    lines: list[QuoteLine] = Field(min_length=1, max_length=20)
    requested_quantity: Number | None = Field(default=None, gt=0, description="What the kitchen asked for, e.g. 1000. "
                                              "Sealed into the offer; the order may not claim a different amount.")
    requested_unit: str | None = Field(default=None, max_length=10, description="Unit of requested_quantity: L, KG or PCS.")
    request_limits: RequestLimits | None = Field(default=None, description="The agent's own limits for this purchase. "
                                                 "They can only tighten the buyer's signed rules.")


class OrderRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"offer_id": "ofr_cd03b74d5344", "requested_units": 1000,
                                                               "rationale": "Weekly milk, cheapest that arrives Monday"}]})

    offer_id: str | None = Field(default=None, max_length=60, description="The signed offer to commit (or send jws).")
    jws: str | None = Field(default=None, max_length=20000, description="The signed offer itself, as returned by /v1/quotes.")
    requested_units: Number | None = Field(default=None, gt=0, description="Optional: must equal the signed requested_quantity.")
    rationale: str = Field(default="", max_length=1000, description="Why the agent chose this offer, for the audit trail.")


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"decision": "approve", "approver": "Aino Virtanen"}]})

    decision: Literal["approve", "reject"]
    approver: str = Field(min_length=1, max_length=80, description="A person named in the buyer's signed rules.")
    comment: str = Field(default="", max_length=500)


class VerifyOfferRequest(BaseModel):
    jws: str = Field(min_length=1, max_length=20000, description="A signed offer from /v1/quotes.")
