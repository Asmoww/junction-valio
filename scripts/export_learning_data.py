"""Turn what people taught the gateway into training data for the request reader (scripts/finetune_reader.py).

The fine-tune in scripts/reader_data.py learns Claude's readings of requests Claude wrote. This adds what real
people decided: every phrasing taught in a buyer's glossary, and every ask-back followed by the buyer's clearer
request (learning.py). The vague text is the input; the label is the reading of what the buyer then said they
meant. So a fine-tuned reader learns "milk, the usual" the way this kitchen means it.

Labels come from Claude when ANTHROPIC_API_KEY is set (same as reader_data.py), otherwise from the rule reader.
Per-buyer meanings stay per buyer in the output (`mandate_id`), so they can be kept out of a shared model.

Usage: python scripts/export_learning_data.py [--out data/reader/human.jsonl]
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gateway import agent, ai, glossary, learning, policy  # noqa: E402

TODAY = date(2026, 11, 14)


def reading(text):
    if ai.enabled():
        intent = ai.parse(text, today=TODAY)
        if intent and not intent.get("_error"):
            intent.pop("raw", None)
            return intent, "claude"
    return agent.parse_request(text, today=TODAY), "rules"


def pairs():
    for mid in policy.mandates():
        for e in glossary.entries(mid):
            yield mid, e["phrase"], e["meaning"], "glossary", e["taught_by"]
        for c in learning.suggestions(mid)["clarifications"]:
            yield mid, c["asked"], c["clarified"], "ask_back", None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "reader" / "human.jsonl"))
    args = ap.parse_args()
    seen, rows = set(), []
    for mid, vague, meant, source, by in pairs():
        if (mid, vague.lower()) in seen:
            continue  # a glossary entry wins over the raw ask-back it came from
        seen.add((mid, vague.lower()))
        intent, labeller = reading(meant)
        if not intent:
            continue
        rows.append({"messages": [{"role": "system", "content": ai.SYSTEM},
                                  {"role": "user", "content": ai.user_message(vague, TODAY)},
                                  {"role": "assistant", "content": json.dumps(intent, ensure_ascii=False)}],
                     "request": vague, "meant": meant, "mandate_id": mid, "source": source, "taught_by": by,
                     "label_from": labeller})
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(f"{len(rows)} human-taught examples -> {out} "
          f"({sum(r['source'] == 'glossary' for r in rows)} from glossaries, {sum(r['source'] == 'ask_back' for r in rows)} from ask-backs)")


if __name__ == "__main__":
    main()
