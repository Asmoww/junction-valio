# Partner technology in the Trusted Trade Gateway

Three partner technologies, each doing real work inside the gateway, not on the side. Live demo:
https://valio.psavela.com (password in the submission).

## NVIDIA Nemotron 3.5 Super VL: order from a photo of a list

**What it does.** Kitchens order from handwritten lists on the fridge or a whiteboard. On the buyer page, *Photo of
a list* sends the photo to `nvidia/nemotron-3.5-super-vl-120b-a12b` (NVIDIA API, OpenAI-compatible). Nemotron
transcribes each line (Finnish or English) into one plain request: "40 l laktoositon maito" becomes "Order 40 litres
of lactose-free milk by 2026-11-16". Every line then runs through the gateway's six checks on its own, and the page
shows each line's track, outcome and price.

**Why it matters.** It's how kitchens actually work, and it removes retyping (the brief's "least manual correction").
Nemotron's vision and document reading is the only part of the flow that sees the photo.

**How we improved its output.**
- A narrow job: transcribe only. Nemotron never picks products, prices or decides; the gateway does.
- Strict JSON contract, validated in code; lines without a readable amount are marked *check the amount* and left
  unticked, never guessed.
- Finnish product words and units (dl, g, munaa) translated in the prompt; dates from the list's heading ("Tilaus
  maanantaiksi") applied to every line.
- The photo is untrusted: every transcribed line goes through the same prompt-injection scanner as typed requests.
- Rate limits (NVIDIA free tier ~40 requests/min) surface as a clear message; the gateway's Valio calls are throttled.

Code: `gateway/vision.py`, `POST /api/photo`, tests `tests/test_vision.py`. Without `NVIDIA_API_KEY` the same flow
uses Claude, so the demo never breaks.

## Oxylabs Web API: live food-recall check, with the source

**What it does.** Before an order is committed, each product is checked against the Finnish Food Authority's
(Ruokavirasto) current recall notices. Oxylabs `search` finds notices for the product's brand and type on
ruokavirasto.fi; Oxylabs `scrape` reads the recall list and each candidate notice in full.
- A notice from the last 90 days naming the exact product stops the order: new hard rule *Not recalled* (gate 3).
- A notice naming the brand and product type sends the order to a person with the notice link: the catalog has no
  EAN codes, so whether this exact product is included is a person's call, not a guess (gate 4).
- An unreachable source also goes to a person: no answer is never "not recalled".

**Why it matters.** Recalls are today's facts; no model's training data has them. The decision record shows the
notice URL behind the decision, so the buyer and Valio can verify it. Live example found while building: a 9 October
2026 notice names Laitilan Kanatarha eggs, and the catalog's Laitilan boiled eggs get a *possible recall* match.

Endpoints: Oxylabs realtime API with `source: google_search` (search) and `source: universal` (scrape). Code:
`gateway/recalls.py`, rules in `gateway/policy.py`, tests `tests/test_recalls.py`. Without Oxylabs credentials it
reads the Food Authority's pages directly. Demo switch: *Food recall on the chosen product (test)*, labelled TEST
RECALL everywhere.

## Verda (and Gemma): a request reader that stays in Europe

**What it does.** The gateway uses a language model in two places: reading the buyer's request into a structured
intent, and checking which catalog categories really are the asked product. Both can run on a European open-weight
model instead of Claude (`READER=eu`, any OpenAI-compatible server), so purchasing data never leaves infrastructure
Valio controls. We fine-tune Gemma on Claude's readings (distillation) on a Verda GPU and serve it with vLLM.

**Why Verda.** European data centres on 100 % renewable energy, and the compute to fine-tune and serve an open
model we control. A US API is a hard sell to a Finnish dairy and its hospital customers.

**Process.**
1. `scripts/reader_data.py`: ~700 varied kitchen requests (English and Finnish; units, dates, allergies, several
   products, questions, injections) labelled with Claude's readings; 85/15 train/test split.
2. `scripts/finetune_reader.py`: LoRA fine-tune of Gemma on a Verda GPU instance.
3. vLLM serves base and fine-tuned models with structured output.
4. `scripts/reader_bench.py`: field-by-field agreement with Claude on the held-out set, latency, schema failures.

**Path to production.** One environment variable switches the gateway's reader; the same prompts and JSON schemas
run on both. Results: see `state/reader_bench_*.json` and the numbers in the team's Discord post.
