"""Seed the demo with a few orders that wait for a person because Valio's data can't prove something.

Without these, the "Unlock automation" card in the Valio view is empty on a fresh database. Each request below
is an ordinary hospital or hotel order that stops at gate 4 ("Proven?") on Valio's real data: no delivery
history for the product, or pieces per pack missing. They go through the real agent and gateway on Valio's live
API (a handful of calls) and wait in the Valio view like any other order; reject them after the demo.

Usage: python scripts/seed_demo.py           (uses the demo database, like the server)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway import agent, unlock  # noqa: E402

SEEDS = [
    ("mnd_hospital_ward", "Order 200 litres of organic oat drink by Tuesday."),
    ("mnd_hospital_ward", "Send 15 kg of salmon fillet on Friday"),
    ("mnd_hospital_ward", "Order 300 eggs by Monday"),
    ("mnd_hotel_helsinki", "Order 300 eggs by Monday"),
]


def main():
    for mandate, text in SEEDS:
        r = agent.run(text, mandate)
        o = r.get("order") or {}
        held = [x["rule"] for x in (o.get("policy") or {}).get("rules", []) if not x["ok"]]
        print(f"{mandate:20} {text[:45]:45} -> {r['ladder']['action']:15} {held}")
    rep = unlock.report()
    print(f"\nUnlock card: {rep['held_for_missing_data']} orders held for missing data; "
          f"{rep['unlocked_if_all_fixed']} would go through alone with the data added.")


if __name__ == "__main__":
    main()
