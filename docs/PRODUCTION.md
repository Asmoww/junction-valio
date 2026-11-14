# From demo to production

Vahti is one service between buying agents and Valio Aimo's systems. The demo runs the real decision logic on
Valio's mock API; what changes for production is mostly *who holds which key* and *where the data comes from*,
not the six checks.

## What an agent can and cannot see

| An agent can | An agent can never |
|---|---|
| Search the catalog: name, brand, sales unit, origin, allergens, claims, labels, delivery reliability, carbon range, Valio's substitutes | Call Valio's own API, or see the API key: every live call goes through the gateway |
| Get live price and availability for a shortlist of up to 20 products, as a signed fixed-price offer | See prices for products it didn't ask about, or a price list in bulk |
| Place an order from a signed offer, for the one buyer its token is bound to | Act for another buyer, read another buyer's orders, or change a price (prices come from Valio, never from the caller) |
| Read its own orders, decision records and audit events | Approve anything: approvals come only from a signed-in person named in the buyer's rules (agents get 403) |
| Verify every signature itself (offer keys at `/.well-known/jwks.json`, buyers' keys as did:web) | See Valio's supplier terms, margins, other customers or the marketplace roadmap: none of it is in the interface |

## Demo → production

| Part | In the demo | In production |
|---|---|---|
| Valio data | Mock API (synthetic catalog, prices, availability) and a static catalog snapshot | Valio's product information system and the webshop's price/availability service, behind the same two calls (`valio.py`); catalog refreshed on change |
| Buyer identity | Fixed demo buyers; the gateway holds each buyer's signing key | The buyer signs its rules with its own key (did:web on the buyer's domain, or an EU business wallet / EUDI credential); the gateway only verifies |
| Agent identity | One token per buyer (HMAC of a server secret and the buyer) | A credential per agent and per buyer (OAuth 2 client credentials or a signed agent card), so "earned trust" and "on hold" follow the exact agent, revocable on its own |
| People | Shared demo password; approval links by QR | The buyer's own sign-in (Valio webshop accounts or the buyer's SSO); approval links sent to the approver's phone or email |
| Signing keys | Files in `state/` on the server | A key management service or HSM, with rotation; old public keys stay in the JWKS until their offers expire |
| Storage | SQLite with a hash-chained audit log | A managed EU database; the audit chain is anchored daily (e.g. a signed daily head), retention per Valio's policy |
| Request reading | Claude (Anthropic API), or an EU open-weight model with `READER=eu` | Either, per Valio's data policy. It only reads requests; every decision is a rule in code |
| Limits on calls | Throttling towards Valio's API only | Per-agent rate limits and quotas on search and quotes, so no agent can scrape prices |
| Orders | Recorded in the gateway; Peppol BIS Ordering 3 export | Sent to Valio's order system; Peppol to the buyer's ERP |
| Hosting | One server behind a Cloudflare tunnel | Valio's EU cloud, two or more instances behind the existing API gateway |

## Standards it already speaks

MCP (`/mcp`), REST with OpenAPI (`/docs`, `contracts/openapi.yaml`), A2A agent card
(`/.well-known/agent-card.json`), AP2-style signed checkout (ES256) on every offer, W3C Verifiable Credentials
for the buyer's rules, Ed25519 JWS offers, Peppol BIS Ordering 3. Details: `docs/STANDARDS.md`.

## A sensible rollout

1. **Read-only pilot.** Agents of a few existing customers use search and quotes only. Valio sees what agents ask
   for and which missing product data blocks them (Valio view: catalog gaps).
2. **Orders with a person.** Every order waits for the buyer's approver. The decision record shows what the agent
   would have done alone, so the buyer can see whether to trust it.
3. **Agents alone within limits.** Buyers sign rules with small limits; agents earn more through clean orders
   (New → Proven → Trusted). Hard rules (allergens, recalls, ceilings) never loosen.
4. **Open to any agent.** Any buyer's agent connects through MCP, REST or A2A with its own credential.

## Open questions for Valio

- Which system is the source of truth for allergen and lactose data? 46 % of products have none, and an agent
  can't decide alone without it (Valio view shows which categories to fix first).
- Supplier-warehouse ("Terminal") stock can't be confirmed today, so those orders go to a person. Can the
  supplier feed confirm it?
- Should the fixed price in an offer be held for 15 minutes, or until the order cut-off?
- Who in a customer organisation may sign the rules: the webshop account owner, or a named role?
