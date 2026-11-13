# Status

Update at the end of every session. Newest first within each section.

## In progress
- (Rasmus's Claude, branch `fix/request-fidelity`, one commit ahead of `main`) Fixes from ChatGPT's red-team review: one order per signed offer, requested quantity sealed into the offer, fail-closed mandate defaults, stock recheck on late approval, CI workflow, `scripts/export_openapi.py`. 93 offline tests here; `tests/test_api.py` needs fastapi, so run `uv run pytest` before merging.
- (Rasmus's Claude, branch `fix/request-fidelity`) Decision ladder as the backbone + fixes from the live
  trace review. **On the server after merging:** set `DEMO_CLOCK=2026-11-12T12:00:00+02:00` in `.env` (or leave
  empty for the real clock) and restart. Run `uv run pytest` and `uv run pytest -m live tests/traps`: some
  trap texts may need updating because 100 L orders with fresh-vs-UHT left open now go to the approver.
- (Rasmus's Claude, branch `fix/gateway-hardening`) Attack review of v0.2 and fixes, with 36 offline tests in
  `tests/`. **After merging, rebuild the catalog on the server** (`python scripts/build_catalog.py`): the
  policy now reads each product's lactose value, and without the rebuild every lactose-free order goes to a
  person. Not touched here because Asmoww is on them: search shortlist (cheapest milks dropped) and allergen
  bans for MAY_CONTAIN / missing data.
- (Pyry) Demo live at https://valio.psavela.com (password in team chat). Next: pitch deck + demo script; Claude request reading needs `ANTHROPIC_API_KEY` in `.env` on the server.

## Blocked / needs a decision
- Repo is private: make it public (or add judges) before submission. Video + deck still to make.
- Ask Valio tech mentors: are the passed Terminal cut-offs and the ignored requested date intended? (docs/ASSUMPTIONS.md #3, #4)
- Naive-agent baseline for the trap suite (same requests through a plain LLM agent on the raw API): Rasmus's lane?

## Done
- (Pyry) Assessment fixes 1-5: eggs counted as eggs; buyer profiles + dairy-only unstated details (autonomy 2/12 → 5/12); agent-readiness card in the Valio view; README, contracts updated, projectoutline.md moved to docs/; DEMO.md script, Claude type-check cache, demo DB reset. Merged fix/request-fidelity (red-team fixes) with an offline-test clock fix. Tests: 100 offline, 25/25 live traps.
- v0.3 (Pyry): verified lookup (cheapest milk now found: €1,010 vs €1,400), allergen certainty (may-contain, missing data), trust flags + match evidence on every offer line, unstated-detail pricing, substitute fit check, hospital milk-allergy mandate, contracts/ updated to the real API, docs/ASSUMPTIONS.md, trap suite 19/19 on live data (shown in Valio view). Based on Rasmus's review, numbers re-checked.
- v0.2: MCP server at /mcp (6 tools, same checks as REST), Claude request reading (auto-on with ANTHROPIC_API_KEY, rule-based fallback), Valio view dashboard (/valio), Connect page (/connect), red-team prompt injection (scanner + compromised agent blocked by mandate + new "matches the buyer's request" rule), redesigned UI.
- Gateway v0.1 (Python/FastAPI, `gateway/`): catalog search over 17,546 products, live Valio price/availability for the shortlist only, good-better-best options, Ed25519-signed fixed-price offers (JWKS at /.well-known/jwks.json), mandate policy engine (spend limits, allergen bans, substitutions, reliability, delivery date), human approval, hash-chained audit log, decision record, demo UI, open /v1 API (/docs). Red-team toggle: price change between offer and order is caught.
- Repo scaffold: CLAUDE.md, docs, draft contracts, /sync and /handoff commands
