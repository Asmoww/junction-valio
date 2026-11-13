# Status

Update at the end of every session. Newest first within each section.

## In progress
- (Pyry) Demo live at https://valio.psavela.com (password in team chat). Next: MCP server, LLM intent parsing, Valio-side dashboard, red-team prompt injection.

## Blocked / needs a decision
-

## Done
- Gateway v0.1 (Python/FastAPI, `gateway/`): catalog search over 17,546 products, live Valio price/availability for the shortlist only, good-better-best options, Ed25519-signed fixed-price offers (JWKS at /.well-known/jwks.json), mandate policy engine (spend limits, allergen bans, substitutions, reliability, delivery date), human approval, hash-chained audit log, decision record, demo UI, open /v1 API (/docs). Red-team toggle: price change between offer and order is caught.
- Repo scaffold: CLAUDE.md, docs, draft contracts, /sync and /handoff commands
