# Preplanner and authority boundaries

This change adds a shared, **non-ordering** preplanner to the buyer UI, REST and MCP.
It does not add a new source of purchasing authority.

## External-agent integration

Authenticate with the buyer-bound agent token shown on /connect, not the sign-in password.

- REST: `POST /v1/preplan` with `Authorization: Bearer <agent token>`.
- MCP: `preplan_request`, with the same named fields.
- Required: `purpose` (`purchase` or `inquiry`), `mandate_id`, `product`.
- Purchase also requires positive `quantity` and future/today `deliver_by` (YYYY-MM-DD).
- Optional: `unit` (litres/kg/pieces), `attributes`, `allergies`, `limit` (1–20).
- Unsupported fields are rejected, not silently treated as enforced constraints.

Inquiry example (identical JSON field names for REST and MCP):
```json
{
  "purpose": "inquiry",
  "mandate_id": "mnd_hotel_helsinki",
  "product": "milk",
  "attributes": ["FREE_FROM_LACTOSE"]
}
```

Purchase preparation example (replace the date with the buyer's actual delivery date):
```json
{
  "purpose": "purchase",
  "mandate_id": "mnd_hotel_helsinki",
  "product": "milk",
  "quantity": 100,
  "unit": "litres",
  "deliver_by": "2026-11-16",
  "attributes": ["FREE_FROM_LACTOSE"],
  "allergies": []
}
```

Both return `creates_order: false`, `purpose`, `request_text`, catalog products/checks,
`guardrails`, `warnings` and `next_steps`. No signed offer, reservation or order is created.
Catalog matches are not a final safety/availability verdict.

For inquiry, use `search_products` and `check_price_availability` to answer. Do not call
`request_offer` or `place_order` to answer a question. For an authorized purchase, first inspect
the buyer's mandate, preserve every request constraint, then use `request_offer` and
`place_order`. Gateway checks still run at commit.

**Boundary:** purpose on this read-only call is not a token-wide restriction. An external agent's
separate call to place_order remains an explicit purchase operation under its mandate; the current
low-level quote/order API does not carry the preplanner's purpose or all its request-only constraints.
Do not claim that selecting inquiry disables the token's order capability.

The built-in buyer page sends purpose separately to /api/run. Inquiry there is forced onto the
non-ordering path even when its text sounds like an order, and cannot run the simulated malicious
order path. Purchase does not override a textual refusal or question. /api remains human-only;
external agents use /v1 and /mcp.

## Whose limits, where, and what happens

| Layer | Stored / supplied where | Enforcement | On failure |
|---|---|---|---|
| Buyer organization's hard policy | Registered gateway/mandates/*.json; buyer-signed credential | policy.evaluate, at gateway commit and approval | Block; ordinary approval cannot override |
| Agent authority delegated by that buyer | Same mandate, auto_approve | Per-order and rolling-24h checks at commit under store.commit_lock | Human approval |
| Authorized human's ceiling | Same mandate, approvers[].up_to_eur | Gateway approval endpoint; agents cannot approve | Approval refused |
| Pre-authorized week plan | Signed plan in gateway store | Gateway finds a covering signed plan and checks budget/units/window | Ordinary authority checks apply if not covered |
| Tighter instruction for one purchase | Request text / external agent's own state | **Incomplete in current base; see gaps below** | Must stop rather than assume enforcement |
| External agent operator's private cap | Outside gateway unless separately registered | **No independent per-external-agent policy exists** | Outside agent must not exceed it; gateway cannot claim to verify unseen limits |

Example registered Hotel mandate: **€5,000 buyer hard cap per order**, **€500 agent autonomous cap
per order**, **€1,500 autonomous spend over 24h**. Approvers: Aino up to €1,500, Jari up to €5,000.
These numbers are configuration, not universal defaults. Read the selected buyer's current response.

Built-in and external agents use the **same mandate**. The daily allowance is summed by mandate,
not token or individual external agent, so switching agents cannot reset it. The token binds the caller
to a buyer; it does not authenticate a separate, independent budget for each agent.

A signed covering plan substitutes prior human authority for the two autonomy caps; it never
removes buyer hard rules, fact checks or other policy conditions. The preplanner shows baseline
limits, not a current spending balance or guaranteed permission to purchase.

The buyer page's auto-limit field is explicitly labelled as a **demo override for that built-in run**.
It is not persisted to the registered mandate and does not change an external agent's authority.
A signed-in demo human may raise it up to the buyer hard cap; agents cannot use /api to do so.

## Gaps that are NOT fixed by adding a purpose selector

1. **Request budget:** current inspected base f5a59e0 has no explicit per-request total cap in the
   external quote signature. The previous browser review also observed a prose budget overrun.
   Coordinate with the existing fix/request-budget branch; do not duplicate or claim its validation.
2. **Request-only allergies:** this preview checks selected allergies, but low-level request_offer
   reloads the registered mandate and does not carry the preview's added bans. Before external
   ordering, such restrictions must be part of the registered buyer mandate, or the flow must stop.
3. **Independent outside-agent limit:** no agent identity/authority registry separate from buyer
   mandates. A future design must enforce the minimum of buyer policy, delegated agent authority and
   request constraints at commit, and bind those constraints into the signed offer.
4. **Inquiry binding across calls:** the preplanner is read-only, but low-level order APIs do not
   enforce a previous call's purpose. A stronger workflow needs a signed request context and a
   deliberate purchase transition, not prompt guidance alone.

The response exposes these gaps under guardrails.request_limits and warnings. This is a disclosure
of actual enforcement, not a promise that agent instructions themselves are a security boundary.

## Validation and handoff

Tests/test_preplanner.py covers pure preparation, purpose validation, missing fields, precise quantity
formatting, shared limits, request-only allergy isolation, caller scope, inquiry with order-like text,
the simulated attack toggle, REST and MCP. Independent agent review is required before merge/deploy.
Also click through both choices and verify /docs exposes the request schema and bearer authorization.

Independent agent review passed isolated offline checks of inquiry safety (including order-like text
and attack mode), question preservation, quantity formatting, and allergy snapshot isolation, plus
Python and browser JavaScript syntax. Full pytest, REST/MCP execution, and browser interaction remain
pending: this Windows environment lacked test/server dependencies and fcntl. The offline harness
stubbed networking and locking; it does not validate concurrency or deployment behavior.

Application source changes belong on the feature branch. Coordination/review material stays on
coord/rasmus-claude, never merged into main.

