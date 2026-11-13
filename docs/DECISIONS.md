# Decisions

One line each: date, decision, why. Don't reopen without a reason.

- 2026-11-13: Guardrails enforced in the gateway (policy engine), not only in the agent prompt. Why: judged on controls; a jailbroken agent must still be blocked.
- 2026-11-13: Quote-then-commit with quote expiry and idempotency keys. Why: fixed prices, no double orders on retry.
- 2026-11-13: Python 3.12+, FastAPI + Pydantic gateway, Anthropic SDK agent, MCP Python SDK, Jinja/htmx approval UI, SQLite, pytest, uv. Why: one language for the whole team, one process for the demo.
- 2026-11-13: Only a signed-in person can approve; an agent's Bearer token gets 403, and the approver must be in the mandate with authority for the total. Why: a hijacked agent must not sign off its own order.
- 2026-11-13: The price lock ends at the earliest of 15 min, any line's order deadline and any line's price validity; an approval after expiry re-checks the price and never places at a stale one. Why: "fixed price" has to stay true until the order is placed.
- 2026-11-13: Stock Valio can't confirm (Terminal, placeholder quantities) and lactose-free claims backed only by the product name need a person. Why: the agent must not treat unknowns as facts.
- 2026-11-13: Order lines are validated before pricing (positive, whole units for pieces/boxes, lot size, no duplicates, max 20 lines). Why: a negative line could pull a large order under the auto-approve limit.
