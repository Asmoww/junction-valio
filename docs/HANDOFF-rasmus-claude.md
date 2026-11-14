# For Asmoww's Claude: work in progress from Rasmus's Claude

Rasmus asked (14 Nov 04:29) that updates between our two sessions live here in GitHub, not in chat.

**Please be extra careful and critical before merging anything to `main`.** None of this was run under
FastAPI or MCP (neither is installed in my workspace; I test the gateway functions directly and the pages
through a stdlib stand-in server with Playwright). Before merging, on your machine:
1. `git fetch && git checkout feat/week-plan` (it contains `fix/benchmark-1000`, so review both).
2. `uv run pytest` (all of it, including `tests/test_api.py`), then `uv run pytest -m live tests/traps`.
3. Start the server and click through: `/plan` (search, price, send, sign, revoke), then `/` with
   "1,000 litres of lactose-free milk by Monday" before and after signing a plan, `/valio`, `/connect`,
   one MCP call with a per-buyer token from `/connect`.
4. Check that the scoreboard and the live demo still work with the token change (see STATUS.md).
5. If anything here conflicts with how you built something, your version wins on `main`; tell me here.
Disagree with the design? Write it under "Review notes" below, signed. I read this file at the start of
every session.

## Branches
| Branch | Base | What | State |
|---|---|---|---|
| `fix/benchmark-1000` | main 5a6b590 | ChatGPT 1,000-case benchmark fixes: approval re-checks everything, quote validation, Valio errors fail closed, agent tokens bound to one mandate | Done, needs your review |
| `feat/week-plan` | `fix/benchmark-1000` | Week plans + planner page `/plan` (below) | In progress |

## Week plan: what it does
- `gateway/plans.py`: a plan names products, units per week, a delivery window and a budget. Draft → an
  approver from the mandate with authority for the budget signs it (Ed25519) → approved; revocable.
  Hard rules (banned allergens, required claims) are checked when the plan is drafted.
- At commit (`agent._place_order`, under the commit lock) the gateway looks for an approved plan that covers
  every line, units left, budget left and the delivery window. If one does, only the per-order and 24 h
  autonomy limits are lifted for that order (rule detail names the plan and who signed it). Every other
  rule still runs, so only exceptions reach a person. An agent can't claim a plan. A plan edited in the
  database without re-signing is ignored and logged (`security.plan_signature_invalid`).
- Orders inside a plan don't count towards the agent's own 24 h allowance (the plan's budget limits them).
- Endpoints: `/plan` page; `/api/planner/search`, `/api/planner/prices`, `/api/plans` (+ `/approve`,
  `/revoke`), people only; `GET /v1/plans?mandate_id=` read-only for agents (token-bound).
- Tests: `tests/test_week_plan.py` (18).

## Plan from a recipe (`gateway/recipe.py`, card on `/plan`)
- Paste a recipe + covers (+ "serves" if the text doesn't say). Claude reads the ingredients when
  `ANTHROPIC_API_KEY` is set, otherwise a simple line reader ("500 g butter"). Amounts are scaled to the covers;
  candidates come from the catalog search (with Claude's type check when available) minus products the
  mandate forbids; packs are counted from Valio's price data (catalog / comparison price), never from names.
- Untrusted text: the recipe goes through `security.scan_request`; any finding means nothing is read.
- It only fills the planner form, marked "from the recipe: check the amount". Nothing is ordered or signed.
- Known weakness in rule-reading mode: "cream" finds ice creams first (the Claude type check fixes this on
  the server). Pieces ("1 onion") aren't converted to packs; the chef sets those.
- `POST /api/planner/from-recipe` (people only). Tests: `tests/test_recipe.py`.

## Next steps (Rasmus's Claude)
- [x] 1. Plan model, signing, commit-time enforcement, tests
- [x] 2. Planner page `/plan` + endpoints, nav tab on every page
- [x] 3. "Plan from a recipe": paste a recipe + number of covers, Claude (if `ANTHROPIC_API_KEY`) or a
      simple rule reader turns it into editable plan lines; text goes through the injection scanner;
      inferred quantities are marked as assumed and the chef confirms them in the form
- [ ] 4. "Learned from decisions" panel: suggestions from the audit log (ask-backs, approvals, rejections)
      that the buyer can turn into a mandate or plan change; the agent never widens its own authority
- [ ] 5. Buyer page: show "inside week plan X" on an order that a plan covered
- [ ] 6. FRAMEWORK.md: where the plan sits on the ladder (gate 5 authority, pre-given)

## Review notes
(Asmoww's Claude: add yours here, signed and dated.)
