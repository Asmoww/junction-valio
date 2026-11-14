# Agent trust profile v0.1

The brief asks for a *minimum data and verification standard for an external agent to trust Valio Aimo's claims on
stock, price and origin without a manual check* (CHALLENGE.md, "Trust protocol"). This is ours.

## What every offer line carries
`lines[].trust.claims` in every offer, signed into the offer (Ed25519 JWS) with everything else. One entry per claim:

```json
{"claim": "stock", "value": 4200, "unit": "ST", "level": "verified", "source": "Valio availability API",
 "as_of": "2026-11-14T11:52:03+02:00"}
```

| Claim | Where it comes from | Usual level |
|---|---|---|
| `price` | Valio price API, with `valid_until` | verified |
| `stock` | Valio availability API | verified, or **placeholder** (999,999 = quantity unknown), or **unknown** (supplier warehouse) |
| `order_cutoff` | Valio availability API | verified, or unknown |
| `allergens` (contains / may contain / undeclared) | product data | declared, or **unknown** (absent, not zero) |
| `labels` (claims, lactose g/100 g) | product data | declared |
| `origin` | product data | declared, or unknown |
| `delivery_reliability` | Valio delivery history | measured, or unknown |

## Levels
| Level | Meaning |
|---|---|
| verified | checked with Valio's live API when the offer was made |
| measured | calculated by Valio from its own records |
| derived | calculated by the gateway from Valio data (pack contents) |
| declared | stated in product data by the supplier, not checked by Valio |
| placeholder | a stand-in value, not a fact |
| unknown | no data: absent, not zero |

The same table is served at `GET /v1/trust-profile`, so an agent can read it without this document.

## Rules
1. **Signed means authentic, not correct.** The signature proves the claims came from Valio's gateway unchanged;
   the level says how far each one can be relied on.
2. **No stand-ins for facts.** `unknown` and `placeholder` never stand in for something an order depends on: a
   stated allergy with unknown allergen data, a quantity Valio can't confirm, pieces per pack that aren't in the
   data. Those orders stop at gate 4 ("Proven?") and a person records what they checked.
3. **Re-checked at commitment.** Price, stock, cut-off and delivery date are checked again with Valio when the order
   is committed and again when a person approves it; a change means a new offer.
4. **Declared claims stay declared.** The gateway never upgrades a supplier's declaration to "verified". Valio's
   unlock-automation view (`/valio`) lists which missing or declared-only data held real orders for a person.

## Path to production
The levels map onto standard provenance fields (W3C PROV `wasGeneratedBy` / `generatedAtTime`, or claims in a W3C
Verifiable Credential issued per product by the supplier). A supplier signing its own declarations would move
`declared` claims to a verifiable, supplier-signed level without changing this format.
