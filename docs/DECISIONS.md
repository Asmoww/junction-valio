# Decisions

One line each: date, decision, why. Don't reopen without a reason.

- 2026-11-13: Guardrails enforced in the gateway (policy engine), not only in the agent prompt. Why: judged on controls; a jailbroken agent must still be blocked.
- 2026-11-13: Quote-then-commit with quote expiry and idempotency keys. Why: fixed prices, no double orders on retry.
- 2026-11-13: Python 3.12+, FastAPI + Pydantic gateway, Anthropic SDK agent, MCP Python SDK, Jinja/htmx approval UI, SQLite, pytest, uv. Why: one language for the whole team, one process for the demo.
- 2026-11-13: Lookup verifies attributes from structured data (lactose 0 g/100 g, fat %), never from names alone; the shortlist keeps every qualifying product (no cut by name or file order). Why: the old cut dropped the €1.01 milk and overpaid €390 on the demo.
- 2026-11-13: Allergens: CONTAINS a banned allergen blocks; MAY_CONTAIN and missing data follow `allergen_policy` per mandate; missing data is "unknown", never "allergen-free". Why: 46 % of products have no allergen data.
- 2026-11-13: Every offer line carries signed trust flags (stock known?, cut-off, price basis, data gaps, reliability). Why: Valio's "trust protocol" question; agents can't verify claims otherwise.
- 2026-11-13: Unstated details (fat level, fresh vs long-life) are never guessed and never asked in a chat; the agent compares across them and shows the cost of each choice to the approver. Why: "not a chatbot"; one approval instead of two interruptions.
- 2026-11-13: Official substitutes must pass a fit check (same category). Why: Valio's own substitute for coffee milk is cream.
- 2026-11-13: Trap suite (`tests/traps`, `scripts/run_traps.py`) runs real-data cases against the live API; results shown in the Valio view. Why: proof, not claims. Mock quirks documented in `docs/ASSUMPTIONS.md`.
- 2026-11-13: Main repo is Asmoww/hackathon-1 again; `review/rasmus-context` stays a branch, never merged.
