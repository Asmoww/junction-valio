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
   Ideas still under discussion are in `docs/IDEAS.md`; add your feedback there, signed.
2. Build against `contracts/`, not against another module's internals. Use stubs until it's ready.
3. Small commits, merge to `main` every 1–2 hours. Don't break the demo flow on `main`.
4. Never commit API keys or tokens. Use `.env` (gitignored); list variable names in `.env.example`.
5. Query the Valio APIs only when needed, never for all products.
6. Before finishing: update `docs/STATUS.md`, commit, push (or run `/handoff`).
7. Post a one-line Discord update on real milestones only (feature works, deployed, blocked):
   `python scripts/notify.py "..."`. Webhook URL is in `.env` (from team chat), never in git.
8. Everything in English: code, comments, docs, commit messages, UI text.

## Stack and how to run
- Python 3.12+, dependencies in `pyproject.toml`, managed with `uv` (`pip install uv` if missing).
- Gateway: FastAPI + Pydantic. Pydantic models mirror `contracts/openapi.yaml`; FastAPI serves its own
  OpenAPI docs at `/docs`.
- Buyer agent: Anthropic Python SDK. Gateway also exposed as MCP tools via the `mcp` Python SDK.
- Approval UI: served by the gateway (Jinja templates + htmx), so the whole demo is one Python process.
- Storage: SQLite (audit log, quotes, orders). Tests: pytest.

Data: log in to the resource portal and download the 4 data files into `data/` (gitignored, challenge-only
data), then `python scripts/build_catalog.py` builds `data/catalog.json`.

```bash
uv sync                                   # install
uv run uvicorn gateway.main:app --reload  # run gateway + demo UI on :8000 (DEMO_PASSWORD in .env)
uv run pytest tests/traps                  # trap suite on live data (state/traps.db, not the demo DB)
python scripts/run_traps.py               # same, and publishes results to the Valio view
```

## Code map
- `gateway/agent.py` buyer agent flow: parse request → search → price/availability → options → signed offer → order
- `gateway/catalog.py` local product search (no API calls), allergen/label names
- `gateway/policy.py` + `gateway/mandates/*.json` mandate rules (the guardrails)
- `gateway/signing.py` Ed25519 JWS offers · `gateway/store.py` SQLite + hash-chained audit log
- `gateway/mcp_server.py` MCP tools (streamable HTTP at /mcp, `Authorization: Bearer <password>`)
- `gateway/ai.py` optional Claude request reading (`ANTHROPIC_API_KEY`), `gateway/security.py` supplier-text injection scanner
- `tests/traps/cases.py` trap suite (each case: a way buying agents go wrong + the safe outcome); `docs/ASSUMPTIONS.md` mock quirks
- `gateway/main.py` FastAPI: password gate, `/v1` open API, `/api` demo endpoints · `gateway/templates/` pages (index = buyer agent, valio = dashboard, connect) + shared style.css
- Deployed on Pyry's server: user service `valio-gateway` on port 5201 behind Cloudflare tunnel → valio.psavela.com
