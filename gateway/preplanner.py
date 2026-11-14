"""Read-only preplanner shared by the buyer page, REST and MCP.

Preparing a purchase is not permission to place it. Limits shown here are a snapshot;
the gateway checks the registered mandate again when an offer is committed.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import agent, catalog, policy, request_checks, security

Purpose = Literal["purchase", "inquiry"]
Attribute = Literal["FREE_FROM_LACTOSE", "FREE_FROM_GLUTEN", "ORGANIC", "VEGAN", "FINNISH"]
Allergen = Literal["milk", "gluten", "eggs", "peanuts", "tree nuts", "sesame", "fish", "soy"]
Unit = Literal["litres", "kg", "pieces"]

ATTRIBUTE_WORDS = {"FREE_FROM_LACTOSE": "lactose-free", "FREE_FROM_GLUTEN": "gluten-free",
                   "ORGANIC": "organic", "VEGAN": "vegan", "FINNISH": "Finnish"}


class PreplanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    purpose: Purpose = Field(description="purchase prepares an order request; inquiry asks for information only.")
    mandate_id: str = Field(min_length=1, max_length=100, description="Must belong to the calling agent's token.")
    product: str = Field(min_length=1, max_length=200, description="One product noun, e.g. milk or oat drink.")
    quantity: float | None = Field(default=None, gt=0, description="Required for purchase; optional for inquiry.")
    unit: Unit = "litres"
    deliver_by: date | None = Field(default=None, description="YYYY-MM-DD. Required for purchase.")
    attributes: list[Attribute] = Field(default_factory=list, max_length=5)
    allergies: list[Allergen] = Field(default_factory=list, max_length=8)
    limit: int = Field(default=6, ge=1, le=20)

    @model_validator(mode="after")
    def purchase_details(self):
        if self.purpose == "purchase" and (self.quantity is None or self.deliver_by is None):
            raise ValueError("purchase needs quantity and deliver_by; use inquiry to explore")
        if self.deliver_by and self.deliver_by < date.today():
            raise ValueError("deliver_by cannot be in the past")
        return self


class PreplanResponse(BaseModel):
    purpose: Purpose
    mandate_id: str
    request_text: str
    creates_order: Literal[False] = False
    products: list[dict]
    guardrails: dict
    next_steps: list[str]
    warnings: list[str]


def guardrails(mandate):
    """Describe current controls without claiming unimplemented per-agent or request-budget enforcement."""
    auto = mandate["auto_approve"]
    return {
        "enforced_by": "Valio Trade Gateway at order commit and human approval",
        "buyer": {"source": "registered buyer mandate", "hard_limits": mandate["hard_limits"],
                  "failure": "blocked; ordinary order approval cannot override a hard rule",
                  "allergen_policy": mandate.get("allergen_policy", {}),
                  "substitutions": mandate.get("substitutions", {})},
        "agent": {"source": "authority delegated by the buyer in the same mandate",
                  "per_order_eur": auto["max_order_total_eur"],
                  "per_24h_eur": auto.get("max_auto_eur_per_24h", auto["max_order_total_eur"]),
                  "scope": "shared per mandate across built-in and external agents, not per API token",
                  "failure": "human approval required",
                  "plan_exception": "a covering signed week plan replaces these two autonomy caps only"},
        "approvers": mandate["approvers"],
        "request_limits": {
            "external_quote_budget_supported": False,
            "note": "No request-specific budget field is currently sealed into the external quote API. "
                    "Do not assume a budget in prose or an external agent's private policy is enforced here.",
            "allergies": "preplanner candidate checks include selected allergies; low-level quotes use the "
                         "registered mandate, so request-only bans must be registered before external ordering"},
        "snapshot_only": True,
    }


def prepare(body: PreplanRequest) -> PreplanResponse:
    if err := agent.caller_refusal(body.mandate_id):
        raise PermissionError(err)
    if body.mandate_id not in policy.mandates():
        raise ValueError("unknown mandate")
    if security.scan_request(body.product):
        raise ValueError("product must name food, not instructions to an agent")
    mandate = policy.load(body.mandate_id)
    controls = deepcopy(guardrails(mandate))
    allergy_text = ", for guests allergic to " + " and ".join(body.allergies) if body.allergies else ""
    banned = request_checks.allergies(allergy_text)
    if banned:
        hard = mandate["hard_limits"]
        hard["forbidden_allergens"] = sorted(set(hard.get("forbidden_allergens", [])) | set(banned))
        ap = mandate.setdefault("allergen_policy", {})
        ap["may_contain"] = "block"
        if ap.get("no_data", "allow") == "allow":
            ap["no_data"] = "needs_approval"
    attrs = " ".join(ATTRIBUTE_WORDS[a] for a in dict.fromkeys(body.attributes))
    noun = f"{attrs} {body.product}".strip()
    quantity_text = format(Decimal(str(body.quantity)), "f") if body.quantity is not None else ""
    if "." in quantity_text:
        quantity_text = quantity_text.rstrip("0").rstrip(".")
    amount = f"{quantity_text} {body.unit} of " if body.quantity is not None else ""
    deadline = f" by {body.deliver_by.isoformat()}" if body.deliver_by else ""
    text = (f"Order {amount}{noun}{deadline}{allergy_text}." if body.purpose == "purchase"
            else f"What is the price of {amount}{noun}{deadline}{allergy_text}?")
    found, _ = catalog.search(body.product, body.attributes, body.limit)
    out = []
    for p in found:
        problem = policy.allergen_problem(mandate, p)
        inherent = request_checks.inherent_allergen(body.product, set(mandate["hard_limits"]["forbidden_allergens"]))
        if inherent:
            problem = (f"{inherent[0]} is forbidden by the buyer's or request's allergy rules", "block")
        missing = [c for c in mandate["hard_limits"].get("required_claims", [])
                   if c not in p["claims"] and not (c == "FREE_FROM_LACTOSE" and p.get("lactose") == 0)]
        check = ({"level": "block", "why": problem[0]} if problem and problem[1] == "block" else
                 {"level": "block", "why": "missing required claims: " + ", ".join(missing)} if missing else
                 {"level": "check", "why": problem[0]} if problem else
                 {"level": "ok", "why": "fits catalog checks; price, stock and authority checked later"})
        out.append(dict(catalog.describe(p), mandate_check=check))
    warnings = ["Catalog checks are not an offer, reservation, purchase approval or proof of availability.",
                "External agent-specific limits are not registered separately; this mandate's allowance is shared."]
    if body.allergies:
        warnings.append("Selected allergies tighten these candidate checks, but are not automatically carried "
                        "into low-level request_offer: register them in the buyer mandate before external ordering.")
    steps = ["Use search_products/check_price_availability for information; never call place_order for an inquiry."] \
        if body.purpose == "inquiry" else [
            "Preserve the buyer's requirements and check get_mandate before obtaining an offer.",
            "If tighter request-only limits cannot be enforced by the registered mandate, stop for the buyer; "
            "do not drop them when calling request_offer.",
            "Only an authorized purchase continues through request_offer and explicit place_order; "
            "the gateway rechecks the mandate and may block or require human approval."]
    return PreplanResponse(purpose=body.purpose, mandate_id=body.mandate_id, request_text=text,
                           products=out, guardrails=controls, next_steps=steps, warnings=warnings)

