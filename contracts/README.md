# Contracts (DRAFT, agree in the first hour)

The interfaces every module builds against. Change only by team agreement and log it in
`docs/DECISIONS.md`.

- `openapi.yaml`: the Trade Gateway API the buyer agent calls (also exposed as MCP tools).
- `mandate.example.json`: the buyer's purchasing mandate (what the agent may do alone).
- Decision record: returned by `GET /orders/{id}/decision`, see the schema in `openapi.yaml`.

Field names for products, prices and availability should follow the Valio mock APIs once we see them.
