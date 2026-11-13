# Decisions

One line each: date, decision, why. Don't reopen without a reason.

- 2026-11-13: Guardrails enforced in the gateway (policy engine), not only in the agent prompt. Why: judged on controls; a jailbroken agent must still be blocked.
- 2026-11-13: Quote-then-commit with quote expiry and idempotency keys. Why: fixed prices, no double orders on retry.
