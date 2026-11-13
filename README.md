# Trusted Trade Gateway

**Valio Aimo · Junction 2026 · Trusted Trade Agents**

Valio's open front door for AI purchasing agents. An outside agent can look up products, take a signed
fixed-price offer and order, and the gateway decides, the same way for every agent, what the agent may do
alone and what a person must check or approve.

**Live demo:** https://valio.psavela.com (demo password in the submission) ·
**Connect your own agent:** https://valio.psavela.com/connect · **API docs:** https://valio.psavela.com/docs

## Why
Valio's catalog wasn't built for agents. Of 13,546 food products, **30 % have no allergen data** and only
**27 % have a delivery history**. A school kitchen's agent can therefore decide alone on 70 % of the catalog,
a hospital ward's on **17 %**. Keyword search also misleads: "cream" finds ice cream, "200 eggs" can become
200 packs of eggs. The gateway turns these gaps into rules, and the Valio view turns them into a to-do list.

## How it works: one decision ladder
Every request ends at exactly one gate. The first "no" decides who acts next ([docs/FRAMEWORK.md](docs/FRAMEWORK.md)).

| # | Question | If no |
|---|---|---|
| 0 | Purchase? | Answer the question, or decline |
| 1 | Clear: do we know exactly what is wanted? | Ask the requester |
| 2 | Possible: can Valio supply it at a fixed price? | Ask back, with what does exist |
| 3 | Allowed: does it keep every hard rule? | Stop: nobody can approve it |
| 4 | Proven: is every fact backed by data? | A person checks |
| 5 | Agent's call: within the agent's authority? | A person with authority approves |
| | All yes | The agent orders |

Gates 3-5 run in Valio's gateway on every order (our agent, any MCP client, the `/v1` API), so a fooled or
hostile agent can't skip them. Only a signed-in person named in the buyer's mandate can approve.

## What's in it
- **Lookup without API calls:** structured search over the static catalog, claims checked against product data
  (lactose 0 g/100 g, fat %, allergens), a reality check that keeps only products that *are* what was asked.
  Live price and availability for the shortlist only: 2-3 calls per request.
- **Signed fixed-price offers:** Ed25519 JWS with trust flags on every line (stock confirmed or not, order
  cut-off, price basis, data gaps). Public key at `/.well-known/jwks.json`. One order per offer; the requested
  quantity is sealed in.
- **Mandates:** spend limits, allergen bans, substitution rules, a buyer profile for details requests leave
  open, a 24 h cap on what the agent orders alone ([contracts/mandate.example.json](contracts/mandate.example.json)).
- **Exceptions:** partial stock, same-kind split orders, late delivery, Valio's substitutes with a fit check,
  wrong units, over the hard limit, "not in the catalog", each with a specific answer and a next step.
- **Audit trail:** a plain-English decision record per order on a hash-chained log, the same for buyer and Valio.
- **Valio view** (`/valio`): agent traffic, escalations, security events, agent readiness of the catalog, and
  the trap-suite results.
- **Open interfaces:** REST with OpenAPI ([contracts/openapi.yaml](contracts/openapi.yaml)) and an MCP server at `/mcp`.

## Proof
- **Trap suite** ([tests/traps/cases.py](tests/traps/cases.py)): 25 real-data cases where buying agents go
  wrong, run against the live Valio mock API. Results are shown in the Valio view.
- **100 offline tests** (fake catalog and fake Valio, no keys needed), run in CI on every push.
- Every quirk of the mock data and how we handle it: [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md).

## Run it
```bash
cp .env.example .env            # VALIO_API_KEY, DEMO_PASSWORD; ANTHROPIC_API_KEY optional (rules-only without)
# download the challenge data files into data/, then:
python scripts/build_catalog.py
uv sync
uv run uvicorn gateway.main:app --reload       # http://localhost:8000
uv run pytest                                  # offline tests
uv run pytest -m live tests/traps              # trap suite against the live mock API
```

Python, FastAPI, Claude Sonnet 5.5 (reads free-text requests and checks product types; it never makes the
decisions), MCP Python SDK, Ed25519 / JWS, SQLite. Data is synthetic and used only for this challenge.
