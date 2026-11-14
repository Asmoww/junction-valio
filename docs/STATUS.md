# Status

Update at the end of every session. Newest first within each section.

## In progress
- (Codex, feat/preplanner-purpose) Purchase/Inquiry selector, shared read-only REST/MCP preplanner, typed schema, buyer-vs-agent limits and integration guide in docs/PREPLANNER.md. Independent review pending; do not deploy without validation. Existing request-budget enforcement gaps and lack of per-external-agent identity remain explicitly disclosed. No claim that preview allergies or purpose bind later low-level quotes.
- (Rasmus's Claude, branch `feat/week-plan`, on top of `fix/benchmark-1000`) Week plans + planner page `/plan`,
  plan from a recipe, learned-from-decisions panel, unlock-automation card on `/valio`, request builder on `/`.
  **Notes for Asmoww's Claude live on branch `coord/rasmus-claude` (`docs/HANDOFF-rasmus-claude.md`). That branch
  holds outside reviews and is never merged into `main`.**
- (Rasmus's Claude, branch `fix/benchmark-1000` off main 5a6b590) ChatGPT's 1,000-case benchmark: approval
  re-checks everything, quote input validation, Valio errors fail closed, **agent tokens bound to one mandate**.
  **On the server after merging:** the old `AGENT_TOKEN` now works only for `mnd_hotel_helsinki`
  (`AGENT_TOKEN_SCOPE=all` restores the old behaviour); per-buyer tokens are listed on /connect. Update MCP client
  configs. 188 offline tests here; `tests/test_api.py` needs fastapi: run `uv run pytest` before merging.
- (Rasmus's Claude, branch `fix/break-it` = main + `fix/red-team-2` + this) 1,000-prompt "break the demo" run and ChatGPT's hostile review: fixes and `tests/test_break_it.py`. **On the server after merging:** agents now need `AGENT_TOKEN` (shown on /connect when signed in), not the password; update any MCP client config. 159 offline tests here; run `uv run pytest` (test_api.py needs fastapi) and the live traps.
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

