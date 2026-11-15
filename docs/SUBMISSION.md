# Junction submission text

## Punchline

Any AI agent can buy from Valio Aimo. Vahti decides what it may do alone.

## Description

## Vahti: the guard between AI buying agents and Valio Aimo

Valio Aimo can't choose which AI agents its 20,000 professional kitchens will buy through: an in-house agent, ChatGPT or Claude, a procurement vendor's agent, or one someone has fooled. Rules written into an agent's own prompt are invisible to Valio, and a wrong order arrives looking like any other.

**Vahti is the interface and operating model on Valio's side.** Any agent connects over **MCP, REST (OpenAPI) or A2A**, looks up products, gets a **signed fixed-price offer** and places an order. On every order, Vahti decides whether the agent may do it alone, whether a named person must sign off, or whether nobody can order it at all.

### How it works

- **Lookup:** 17,546 products searched locally. Valio's live price and availability API is asked only about the shortlist.
- **Fixed price:** every offer is signed with Ed25519, valid for 15 minutes, and also issued as an AP2-style checkout. The price is checked again when the order is placed.
- **Six checks, and the first "no" decides who acts next:**
  1. A purchase
  2. Clear
  3. Valio has it
  4. **Safe:** hard rules (banned allergens, live food recalls, the buyer's spending ceiling, the buyer's signed rules) that nobody can override
  5. **Proven:** stock, allergen data and pack size backed by Valio's data, or a person checks the fact
  6. **Agent's call:** spending limits, 24-hour allowance, earned trust for new agents, late delivery and substitutes, or a named approver decides

  Checks 4 to 6 run in Valio's gateway **on every order, whatever agent sent it.**
- **People decide what agents may not:** the buyer's rules are a W3C Verifiable Credential naming the approvers. The approver signs off on their own phone through a single-use link. Agents can never approve.
- **Proof anyone can check:** every order gets a receipt with a QR code. It re-checks Valio's signature, the buyer's signed rules, who decided, and a hash-chained audit trail that Valio and the buyer share. Placed orders also export as Peppol BIS Ordering 3.

### The brief's own example

*"I need 1,000 litres of lactose-free milk by Monday."*

1. 17,546 products narrow to 25 plain lactose-free milks. Lactose is checked in the nutrition data, not the product name.
2. 14 of them arrive by Monday. The cheapest costs €1,010.
3. That is over the €500 the hotel's agent may spend alone, so it waits for the head chef, who approves on her phone.
4. The order is placed and a receipt is issued.

The ticket shows the reason, the chosen product and the two alternatives the agent compared.

### Exceptions

The order ticket names the check that stopped it and the reason in one sentence. Cases include:

- partial availability;
- Valio's official substitutes, fit-checked against category, allergens and the request;
- a delivery later than asked;
- supplier stock Valio can't confirm;
- an order over the hard limit, with the largest quantity that fits;
- unclear or Finnish-language requests;
- instructions hidden in requests or in supplier data.

Fifteen of these run on the live mock data, one click each on the Safety checks page.

### Results on Valio's mock data

- **26/26 trap cases** handled safely: real-data cases where buying agents usually go wrong.
- **395 automated tests.**
- A **Valio view** shows which missing product data keeps agents waiting for a person, and which fix would unlock the most orders.

### Open, and a path to production

- Open standards throughout: MCP, OpenAPI, the A2A agent card, AP2-style checkout, W3C VCs with did:web, Peppol BIS 3, and JWKS for the signing keys.
- Requests are read by **Gemma 4, an open model, on a server in Finland**. Every decision is a rule in code, logged and signed. The model never decides.
- **What's real:** the Valio mock API, the catalog, and every check, signature and log entry.
- **What's made up:** the buyers, the shared demo sign-in, the buyer keys (held by the gateway), and the orders, which stop before Valio's order system.
- **Next for production:** buyers hold their own keys, each agent gets its own credential, orders go into Valio's order system, and rate limits stop agents scraping prices.

**Demo:** https://valio.psavela.com · **Code:** https://github.com/Asmoww/junction-valio
