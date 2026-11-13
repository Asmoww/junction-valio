# Contracts

The interfaces every module builds against. They describe what the running gateway does (v0.3).
Change them only by team agreement and log the change in `docs/DECISIONS.md`.

- `openapi.yaml`: the Trade Gateway API (`/v1`). The same operations are MCP tools at `/mcp`
  (`get_mandate`, `search_products`, `check_price_availability`, `request_offer`, `place_order`, `get_order`).
- `mandate.example.json`: the buyer's purchasing mandate. Live mandates: `gateway/mandates/*.json`.

Key semantics:
- **Prices are fixed** catalog prices, never negotiated. An offer is a signed price lock (Ed25519 JWS,
  key at `/.well-known/jwks.json`) that expires at the earliest of 15 min, the order cut-off and the
  price's validity.
- **Trust flags** come with every offer line: stock known or not, cut-off, price basis, data gaps,
  reliability warning. Unknown never means "safe": no allergen data is "unknown", not "allergen-free".
- **Allergens:** `CONTAINS` a banned allergen blocks. `MAY_CONTAIN` follows `allergen_policy.may_contain`
  (block / needs_approval / allow). No allergen data follows `allergen_policy.no_data`.
- **Unstated details** (fat level, fresh vs long-life) are never guessed. The agent compares across
  them and reports the price effect (`SpecGap`) to the approver.
- **Order = request:** an order covering more than the request +25 % (or one extra pack) is blocked.
- Mock quirks and how we treat them: `docs/ASSUMPTIONS.md`.
