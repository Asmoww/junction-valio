# Status

Update at the end of every session. Newest first within each section.

## In progress
- (Rasmus's Claude, branch `fix/gateway-hardening`) Attack review of v0.2 and fixes, with 36 offline tests in
  `tests/`. **After merging, rebuild the catalog on the server** (`python scripts/build_catalog.py`): the
  policy now reads each product's lactose value, and without the rebuild every lactose-free order goes to a
  person. Not touched here because Asmoww is on them: search shortlist (cheapest milks dropped) and allergen
  bans for MAY_CONTAIN / missing data.
- (Pyry) Demo live at https://valio.psavela.com (password in team chat). Next: pitch deck + demo script; Claude request reading needs `ANTHROPIC_API_KEY` in `.env` on the server.

## Blocked / needs a decision
- Ask Valio tech mentors: are the passed Terminal cut-offs and the ignored requested date intended? (docs/ASSUMPTIONS.md #3, #4)
- Naive-agent baseline for the trap suite (same requests through a plain LLM agent on the raw API): Rasmus's lane?

## Done
- v0.3 (Pyry): verified lookup (cheapest milk now found: €1,010 vs €1,400), allergen certainty (may-contain, missing data), trust flags + match evidence on every offer line, unstated-detail pricing, substitute fit check, hospital milk-allergy mandate, contracts/ updated to the real API, docs/ASSUMPTIONS.md, trap suite 19/19 on live data (shown in Valio view). Based on Rasmus's review, numbers re-checked.
- v0.2: MCP server at /mcp (6 tools, same checks as REST), Claude request reading (auto-on with ANTHROPIC_API_KEY, rule-based fallback), Valio view dashboard (/valio), Connect page (/connect), red-team prompt injection (scanner + compromised agent blocked by mandate + new "matches the buyer's request" rule), redesigned UI.
- Gateway v0.1 (Python/FastAPI, `gateway/`): catalog search over 17,546 products, live Valio price/availability for the shortlist only, good-better-best options, Ed25519-signed fixed-price offers (JWKS at /.well-known/jwks.json), mandate policy engine (spend limits, allergen bans, substitutions, reliability, delivery date), human approval, hash-chained audit log, decision record, demo UI, open /v1 API (/docs). Red-team toggle: price change between offer and order is caught.
- Repo scaffold: CLAUDE.md, docs, draft contracts, /sync and /handoff commands
