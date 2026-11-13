# Challenge brief (Valio Aimo, Junction 2026: Trusted Trade Agents)

Resource portal (datasets, API docs, mock backend access): https://junction26.valioaimo.fi/
The portal password and the API key are shared in the team chat. **Never commit them**; the API key
goes in `.env` as `VALIO_API_KEY`. All provided data is synthetic.

## The question
How can a food-service wholesaler let autonomous purchasing agents place an order (finding products,
comparing offers and getting the right approval in a fraction of the time it takes today) without
exposing real systems, confidential data or the marketplace roadmap?

## Context
Valio Aimo serves 20,000+ professional kitchens with 25,000 products from 700 suppliers and plans a
digital B2B marketplace. For Junction they mock **today's webshop and backend**, not the marketplace.
Prototype the **interface and operating model** that lets an external purchasing agent interact with
Valio Aimo's systems, on open, verifiable standards rather than one platform's black box. How the
wholesaler exposes itself to agents (what they can see, decide and be trusted with) is existential
for data ownership, trust and who owns the customer relationship.

## Core: the solution must enable
- **Product, price and availability lookup:** a structured way for an external agent to search and retrieve what's available and at what price.
- **Request price and availability, then place an order:** a defined interaction from inquiry to commitment, at **fixed prices, not a negotiated quote**.
- **Permission and human-approval management:** what the agent may decide alone, what goes to a human.

## Optional (not required to win)
- Delivery time and sustainability comparison: delivery reliability, certificates/labels already shown in the webshop. Category-level carbon ranges are a stretch goal.
- Substitution and other exception handling. Not scored as core (last year's Zero-Fail Logistics challenge covered failed deliveries).
- Traceability and explainability: a human-readable record of what the agent did and why.

## Example request
"I need 1,000 litres of lactose-free milk by Monday. Find an option that meets our requirements based on
price and availability." The solution returns products, prices and availability in structured form; the
agent orders within its authority or sends it to a human for approval.

**Not a chatbot.** The point is how a European food-service wholesaler can be safely and openly readable
by autonomous agents, before someone else's platform defines that interface.

## Problems they'd love to see tackled
- **Trust protocol:** minimum data/verification standard for an external agent to trust Valio Aimo's claims on stock, price and origin without a manual check.
- **Guardrails model:** which decisions need human sign-off (spend threshold, substitutions outside stated preferences) vs. fully autonomous.
- **Response logic:** partial availability and substitution ranking ("good-better-best") within Valio Aimo's own assortment.
- **Audit trail:** a usable, human-readable log of the agent's purchasing decisions, for both the buyer and Valio Aimo.

## Example scenarios
- Need → recipe → order: chef plans next week's menu around a seasonal ingredient; agent suggests recipes, works out what's missing, orders.
- Last-minute event: booking for tomorrow; agent flags what can't be sourced in time and substitutes through recipe logic, not a plain SKU swap.
- Supplier stock-out mid-week: agent proposes a recipe-compatible substitute before the kitchen notices and explains the trade-off.
- Proactive selling agent: Valio Aimo's own agent suggests a new or seasonal product to the buyer's agent.
- Allergen-safe substitution under pressure: school/hospital kitchen; substitutes must still meet strict allergen and nutrition rules.
- Standing order drift detection: agent learns a recurring weekly order and flags anomalies (portions creeping up, cost drifting) before auto-renewing.

Scope: a mocked structured data/API interface. Whether production becomes a REST API, MCP-style
interface or a full service agent is out of scope.

## Resources
Synthetic catalog, simulated suppliers, mock APIs (prices, availability behaviour, delivery lead times,
sustainability certificates/labels; carbon ranges as stretch). No live or confidential data.
Webshop test accounts: shared separately later.

**Preferred tech:** AI agents (agentic/tool-use frameworks), open data/interop standards,
recommendation logic, optional verifiable-credential or trust-layer approaches. Agentic payment
standards (x402, Nevermined) are relevant context, not required.

## Mentors
- Marketplace/Commercial: Hanna Forsström, Pirjo Kaartinen
- Online Sales: Minni Ruhtinas
- IT/Tech integrations and data governance: Tommi Nurmi-Aro, Ville Takanen, Riku Tapper

## Judging (from the printed flyer)
Working end-to-end demo 30 %, solving the user's need 20 %, autonomy and controls 20 %, robustness to
exceptions 15 %, openness and path to production 15 %.
