# Status

Update at the end of every session. Newest first within each section.

## In progress
- (Pyry) Demo live at https://valio.psavela.com (password in team chat). Next: pitch deck + demo script; Claude request reading needs `ANTHROPIC_API_KEY` in `.env` on the server.

## Blocked / needs a decision
- Repo is private: LICENSE (Apache-2.0) added and full history scanned clean for secrets on 2026-11-14; Pyry flips it public. Video + deck still to make.
- Ask Valio tech mentors: are the passed Terminal cut-offs and the ignored requested date intended? (docs/ASSUMPTIONS.md #3, #4)
- Naive-agent baseline for the trap suite (same requests through a plain LLM agent on the raw API): Rasmus's lane?

## Done
- (Asmoww's Claude, branch `claude/epic-bell-5b8o70`) Multi-product orders: "100 L milk, 20 kg butter and 300 eggs by Monday" becomes one basket. Each line runs gates 1-2 alone (`agent._resolve_line`, split out of `_run` unchanged); any failing line sends the list back, nothing ordered; otherwise one signed offer, policy per line (`basket` in the offer) and on the total. Buyer page shows each line; Photo of a list has "One order for all". `tests/test_basket.py` (12), 339 offline tests. Not yet run on live data: run `uv run pytest -m live tests/traps` with the real catalog before deploying.
- (Pyry) Judging-criteria pass: typed /v1 schemas + public OpenAPI (F7 fixed), docs/PRODUCTION.md, Apache-2.0 licence, pitch with phone approval of the 1,000 L example, phone approval text fix. 327 offline tests.
- (Pyry) Assessment fixes 1-5: eggs counted as eggs; buyer profiles + dairy-only unstated details (autonomy 2/12 → 5/12); agent-readiness card in the Valio view; README, contracts updated, projectoutline.md moved to docs/; DEMO.md script, Claude type-check cache, demo DB reset. Merged fix/request-fidelity (red-team fixes) with an offline-test clock fix. Tests: 100 offline, 25/25 live traps.
- v0.3 (Pyry): verified lookup (cheapest milk now found: €1,010 vs €1,400), allergen certainty (may-contain, missing data), trust flags + match evidence on every offer line, unstated-detail pricing, substitute fit check, hospital milk-allergy mandate, contracts/ updated to the real API, docs/ASSUMPTIONS.md, trap suite 19/19 on live data (shown in Valio view). Based on Rasmus's review, numbers re-checked.
- v0.2: MCP server at /mcp (6 tools, same checks as REST), Claude request reading (auto-on with ANTHROPIC_API_KEY, rule-based fallback), Valio view dashboard (/valio), Connect page (/connect), red-team prompt injection (scanner + compromised agent blocked by mandate + new "matches the buyer's request" rule), redesigned UI.
- Gateway v0.1 (Python/FastAPI, `gateway/`): catalog search over 17,546 products, live Valio price/availability for the shortlist only, good-better-best options, Ed25519-signed fixed-price offers (JWKS at /.well-known/jwks.json), mandate policy engine (spend limits, allergen bans, substitutions, reliability, delivery date), human approval, hash-chained audit log, decision record, demo UI, open /v1 API (/docs). Red-team toggle: price change between offer and order is caught.
- Repo scaffold: CLAUDE.md, docs, draft contracts, /sync and /handoff commands

