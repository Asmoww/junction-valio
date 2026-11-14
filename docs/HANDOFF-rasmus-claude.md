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

## Learned from decisions (`gateway/learning.py`, panel 5 on `/plan`)
- Reads approvals, rejections and ask-backs already in the store. Same check approved ≥3 times for the same
  product with no rejection → "put it in the week plan" (one-click add) or a named mandate setting; rejected
  ≥2 times → "make it a hard rule"; an ask-back followed within 30 min by a request that went through → a
  phrasing for the request reader to learn.
- Suggestions only: nothing changes a mandate or a plan (test checks that). `GET /api/learning`.
  Tests: `tests/test_learning.py`.

## Outside review Rasmus forwarded (14 Nov 04:42, of `main`, not these branches): worth acting on
1. `readiness.py` percentages measure data completeness (allergen data, delivery history), not "can the
   agent order this alone". Suggest renaming to "data completeness under this buyer's policy". Your file:
   your call.
2. Show how much autonomy survives the guardrails: completion rate, escalation reasons, manual corrections,
   time. The week plan is our answer to "too many escalations"; a before/after count (orders needing a
   person without vs with a signed plan) would make it measurable. I can add that to the scoreboard if you
   agree.
3. Keep standards claims exact: UBL XSD-valid is not full Peppol conformance; buyer credentials are signed
   with keys the gateway holds (shows the flow, not independent buyer authority).
4. "Signed" means authentic, not correct: show the trust flags (placeholder stock, missing attributes)
   in the demo.
5. Demo: one autonomous purchase, one approved purchase, one stock/delivery exception that ends in a
   useful outcome (not just a refusal).

## Unlock automation (`gateway/unlock.py`, new card at the top of `/valio`)
Direction from a second outside review (14 Nov 04:45): the distinctive product is turning purchasing
uncertainty into a specific, resolvable task, and showing how resolving it unlocks automation.
- Groups every order that went to a person because the data couldn't prove something (gate-4 checks:
  allergen data, lactose value, delivery history, pieces per pack, stock confirmation) by product and
  missing field. Then re-runs the policy on those same orders as if Valio had added that field
  (`policy.evaluate(..., lookup=...)`, a patched copy for one evaluation) and counts how many would have
  gone through alone, with € value and what still needs a person.
- Answers Valio's question "which data improvements remove the most manual approvals?", measured on real
  held orders instead of catalog completeness. Nothing is changed (test checks that).
- I renamed the old readiness card to "Data completeness · share of the catalog with the data each buyer's
  rules need" (outside review point 1). Your file, your call: revert if you disagree.
- `GET /api/unlock[?mandate_id=]`. Tests: `tests/test_unlock.py`.

## Request builder on the buyer page (`/`, "Build the request step by step", folded by default)
- Rasmus's original pre-planner idea for single orders: product words + attribute chips → "See what Valio has"
  (catalog search with each product checked against the selected buyer's mandate: matches, ruled out, needs a
  check) → quantity, unit, date, allergy chips → "Write the request" fills the request box with a plain
  sentence ("Order 40 kg of muesli by 2026-11-17, for guests allergic to peanuts and tree nuts.").
- It deliberately writes text instead of bypassing the reader: the request goes through the same six checks.
  Your file (`index.html`); the block is self-contained (`b*` functions), revert freely.
- Reader gap found while building it: "Allergies: peanuts." as its own sentence bounces as an ask-back
  (unread words), while "for a guest allergic to peanuts" works. Worth a look in `request_checks`.
- Also fixed: `agent.py` crashed if Valio's price had no `validTo`.

## Asks for Asmoww's Claude (you have more usage than we do)
1. Review and merge as above, carefully.
2. **Actionable escalation.** On the approval screens (`/valio` waiting list, `/approve/...` phone page),
   for a gate-4 order show the one precise uncertainty ("no allergen data for X: the label must show no
   peanuts"), the evidence we have, and up to 3 alternatives from the same search that have no data gap
   and fit the mandate, with their price. The approver resolves one question instead of "approve this order".
   `unlock.GAPS` has the field wording; `policy.allergen_problem` and the search already exist.
3. **Live interoperability proof.** One external agent configured only from `/connect` (e.g. Claude Code
   with the MCP URL and a per-buyer token, no prompt from us) doing lookup → offer → order, plus one case
   where it is held. Record it for the demo; the controls must hold without our prompt.
4. **Demo flow** (both outside reviews): one autonomous order, one approved order, one exception that ends
   in a useful outcome; then fix one data gap (or sign a week plan) and rerun the same requests to show
   what now completes alone. `/plan` + `/valio` unlock card support this.

## Next steps (Rasmus's Claude)
- [x] 1. Plan model, signing, commit-time enforcement, tests
- [x] 2. Planner page `/plan` + endpoints, nav tab on every page
- [x] 3. "Plan from a recipe": paste a recipe + number of covers, Claude (if `ANTHROPIC_API_KEY`) or a
      simple rule reader turns it into editable plan lines; text goes through the injection scanner;
      inferred quantities are marked as assumed and the chef confirms them in the form
- [x] 4. "Learned from decisions" panel: suggestions from the audit log (ask-backs, approvals, rejections)
      that the buyer can turn into a mandate or plan change; the agent never widens its own authority
- [x] 5. Buyer page: the "Auto-approve spend limit" rule line already names the plan and who signed it
- [x] 7. Autonomy measured (review point 2): 30 ordinary hotel requests, each judged alone, rule-based
      reading, simulated Valio with the real catalog. Without a plan: 24 ordered alone, 3 to the approver
      (spend limit), 1 fact check (pack contents), 2 ask-backs. With one signed week plan (2 products,
      €3,515): 27 alone; the 3 spend-limit escalations disappear; the fact check and ask-backs stay, as they
      should. Script in Rasmus's workspace (`autonomy.py`, uses the scratch simulator); happy to port it to
      `scripts/` if you want it in the scoreboard.
- [x] 6. FRAMEWORK.md: where the plan sits on the ladder (gate 5 authority, pre-given)

## Review notes
(Asmoww's Claude: add yours here, signed and dated.)
