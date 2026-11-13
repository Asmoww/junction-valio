# Challenge brief (Valio Aimo, Junction 2026: Trusted Trade Agents)

Ordering is moving from people clicking through a webshop to AI agents buying on their behalf.
Valio Aimo serves 20,000+ professional kitchens with 25,000 products from 700 suppliers. Goal: define
how an outside agent can work with their systems on open standards, not inside someone else's black box.

**Flow:** 1 Request (agent asks for what the buyer needs) → 2 Lookup (products, prices, availability)
→ 3 Order (commit at a fixed price) → 4 Approval (agent decides, or a human signs off).

**Example:** a hotel chain's agent asks: "I need 1,000 litres of lactose-free milk by Monday. Find an
option that meets our requirements based on price and availability."

**Must build:** structured lookup; request → order at fixed prices; permissions and approval
(what the agent decides alone, what a human signs off).

**Bonus:** compare more than price (delivery reliability, sustainability labels); handle exceptions
(partial availability, substitutions); explain decisions (human-readable record of what and why).

**Judging:** end-to-end demo 30 %, solving the user's need 20 %, autonomy and controls 20 %,
robustness to exceptions 15 %, openness and path to production 15 %. Not a production system in 48 h.

**Provided:** pricing and availability mock APIs; product data, delivery reliability and carbon
footprint files; mentors (AI, wholesale, data governance).

**Good to know:** they mock today's webshop, not the marketplace. It is not a chatbot. Query the APIs
only when needed, never for all products.
