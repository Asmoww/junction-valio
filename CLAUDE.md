# Trusted Trade Agents (Valio Aimo, Junction 2026)

Every Claude session on this team reads this file first. Keep it short and current.

## The challenge
Build a prototype that lets an AI purchasing agent buy food from a wholesaler safely,
openly and with the right human approval. Flow: **Request → Lookup → Order (fixed price) → Approval**.
Full brief: `docs/CHALLENGE.md`. Judging: working end-to-end demo 30 %, solving the user's need 20 %,
autonomy and guardrails 20 %, robustness to exceptions 15 %, openness and path to production 15 %.

## Architecture
```
Buyer agent (LLM) ──MCP / OpenAPI──> Trade Gateway ──> Valio mock APIs
                                       ├─ Policy engine (purchasing mandate)
                                       ├─ Quote + order service (quote → commit, idempotent)
                                       ├─ Approval service ──> human (web/mobile)
                                       └─ Audit log ──> decision record
```
Guardrails live in the gateway, never only in the prompt.

## Module ownership (fill in names)
| Module | Owner | Folder |
|---|---|---|
| Gateway + mock API wrappers | ? | `gateway/` |
| Policy engine / mandate | ? | `policy/` |
| Approval UI | ? | `ui/` |
| Buyer agent (LLM, MCP client) | ? | `agent/` |
| Demo script + pitch | ? | `docs/` |

Only edit another owner's folder after agreeing in chat. Shared contracts in `contracts/`
change only by agreement; record the change in `docs/DECISIONS.md`.

## Rules for every session
1. Start: `git pull`, read `docs/STATUS.md` and `docs/DECISIONS.md` (or run `/sync`).
2. Build against `contracts/`, not against another module's internals. Use stubs until it's ready.
3. Small commits, merge to `main` every 1–2 hours. Don't break the demo flow on `main`.
4. Never commit API keys or tokens. Use `.env` (gitignored); list variable names in `.env.example`.
5. Query the Valio APIs only when needed, never for all products.
6. Before finishing: update `docs/STATUS.md`, commit, push (or run `/handoff`).
7. Everything in English: code, comments, docs, commit messages, UI text.

## Stack and how to run
TBD: decide in the first hour and fill in here (language, framework, run and test commands).
