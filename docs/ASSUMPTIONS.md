# Assumptions about the Valio mock (checked 13 Nov 2026 against the product file and live API)

Every quirk we found, the evidence, and how the gateway treats it. Some may be shortcuts of the
mock rather than how Valio's real systems behave; we handle them safely either way and say so.
Open questions go to Valio's tech mentors (Nurmi-Aro, Takanen, Tapper).

| # | What we see | Evidence | How the gateway treats it |
|---|---|---|---|
| 1 | **Supplier-warehouse ("Terminal") stock is unknown** | API docs: `availableAmount` is always 999,999 for Terminal products. 20 of 35 lactose-free milks are Terminal | Trust flag `stock: unknown`. Lines above `max_unconfirmed_units` need a person |
| 2 | **Stock items report placeholder quantities** (9,999,999,999 and 666,666,666) | Only a few products show real numbers (51, 192, 384, 698) | Trust flag "in stock: exact quantity not given". Real numbers drive partial / split orders |
| 3 | **Requested date is ignored** | Identical answers for 16 Nov and 24 Nov on all 35 lactose-free milks; `availableDate` is the same | We don't spend API calls asking for "the next possible date": it can't tell us anything here |
| 4 | **Terminal cut-offs have already passed** but `availableDate` still says Monday | All 20 Terminal milks: `closingTime` 12 Nov 15:00, checked 13 Nov 20:42 | Cut-off wins: the product is "deadline passed", no offer is issued. *Ask mentors if intended* |
| 5 | **One product returns no availability** | 464440 has no schedule lines | Status "not in time", trust flag "no availability answer" |
| 6 | **Allergen units go beyond the schema** | Schema documents CONTAINS (allergen group) and FREE_FROM (68,597 values, nonAllergen group). The data also uses MAY_CONTAIN (9,956) and UNDECLARED (303) | MAY_CONTAIN follows the mandate (`allergen_policy.may_contain`); UNDECLARED needs a person |
| 7 | **46 % of products have no allergen data at all** (8,110 of 17,546) | Neither allergen nor nonAllergen values | "Unknown", never "allergen-free". Under an allergen ban: excluded if verified alternatives exist, else a person checks |
| 8 | **Lactose-free ≠ milk-free** | All 35 lactose-free milks are tagged milk (AM) CONTAINS | A milk-allergy mandate (AM) refuses them with a clear reason |
| 9 | **Lactose-free by name isn't always provable** | 3,573 products have a lactose value; 463203 (100 × 2 cl cups) has none | Lactose-free verified from `LACS = 0` first; name-only claims get a data-gap flag |
| 10 | **Official substitutes can be a different product** | Coffee milk 464237's substitutes include 466238, a 10 % cream (category 10401 vs 10400) | Fit check: an official substitute must be in the same category, else rejected with a reason |
| 11 | **Lot size 0.01** for 16,872 products | Allows ordering 0.01 of a 1 L carton | We order whole sales units |
| 12 | **Litres per sales unit are not in the data directly** | `catalogPrice ÷ comparisonPrice` gives it (31.31 ÷ 1.57 = 19.94 ≈ 20 L) | Rounded when within 15 %, otherwise kept exact; null or zero comparison price → "unit unclear" |
| 13 | **Carbon is a group average** (177 distinct values) | All lactose-free milks share 1.1 kg CO2e/kg | Small ranking weight only; it can't separate products within a group |
| 14 | **Delivery reliability covers 25 % of products** (4,425) | 564 below 0.95, 12 at 0 | Unknown is neutral in ranking and shown as "no delivery history"; below the mandate's minimum needs a person |
| 15 | **Customer number doesn't change prices** in the mock | API docs | Each mandate carries a customer id anyway, as production would need |
| 16 | **No instructions aimed at AI agents in the real product texts** | Scanned all names; scanner has 0 false alarms on 3,000 names | The injection case in the demo is our own, labelled red-team test |

Tested by the trap suite: `python scripts/run_traps.py` (or `pytest tests/traps`).
