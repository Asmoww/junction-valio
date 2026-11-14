# Open standards in Vahti

| Standard | What Vahti does | Where |
|---|---|---|
| MCP | Tools for any MCP client: mandate, search, price, preplan, offer, order, status | `/mcp`, `gateway/mcp_server.py` |
| OpenAPI | The REST flow, typed contract in `contracts/openapi.yaml` (served schema for quotes still untyped) | `/docs`, `/openapi.json` |
| A2A 1.0 (a2a-protocol.org) | Agent Card at `/.well-known/agent-card.json`; JSON-RPC `SendMessage` / `GetTask` (and v0.3 `message/send`, `tasks/get`) at `/a2a`. Text is answered as an inquiry and never orders; quote and order take data parts and run the same checks as REST and MCP, under the per-buyer token | `gateway/a2a.py` |
| AP2 v0.2 (ap2-protocol.org) | The merchant side: each offer also as a merchant-signed checkout JWT, **ES256** as the spec requires for checkouts (not Ed25519), with `checkout_hash` = base64url SHA-256 of the JWT; amounts in minor units; key in `/.well-known/jwks.json` | `gateway/ap2.py`, `GET /v1/offers/{id}/ap2` |
| W3C Verifiable Credentials | The buyer's rules as a credential signed by the buyer (`vc+jwt`, EdDSA, `did:web`) | `gateway/standards.py` |
| Peppol BIS Ordering 3 | Placed orders as UBL 2.1 Orders, checked against the official UBL XSD (not the Peppol schematron) | `/v1/orders/{id}/peppol.xml` |
| JWS / JWKS | Ed25519-signed offers; public keys at `/.well-known/jwks.json` | `gateway/signing.py` |

**Not done, said plainly:** Vahti does not issue or verify AP2's SD-JWT checkout and payment mandates. The challenge
has no payments, and the buyer's authority is the signed mandate credential plus a person's approval. Mapping:
AP2's open checkout mandate (the user's constraints) corresponds to the signed mandate credential and
`request_limits`; the closed checkout mandate would reference our `checkout_hash`; a payment mandate is out of scope.
