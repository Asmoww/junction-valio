"""Everyday requests go through (investigation of 14 Nov: only 16 of 36 ordinary requests were placed alone)."""
from datetime import date

import pytest

from gateway import agent, ai, request_checks

SAT = date(2026, 11, 14)


@pytest.mark.parametrize("text, want", [
    ("We need milk by Monday", "2026-11-16"), ("maitoa maanantaiksi", "2026-11-16"), ("tilaa keskiviikoksi", "2026-11-18"),
    ("10 kg huomiseksi", "2026-11-15"), ("milk for tomorrow", "2026-11-15"), ("100 L today", "2026-11-14"),
    ("10 kg till fredag", "2026-11-20"),
])
def test_a_plain_day_word_means_one_date(text, want):
    assert request_checks.plain_day(text, SAT).isoformat() == want


@pytest.mark.parametrize("text", ["milk on 18.11.", "milk 2026-11-18", "Monday or Tuesday", "next Monday", "200 litres of milk"])
def test_no_single_plain_day_no_override(text):
    assert request_checks.plain_day(text, SAT) is None


def test_finnish_wednesday_counts_as_a_stated_date():
    assert request_checks.date_stated("Tilaa 30 litraa täysmaitoa keskiviikoksi")


def test_pack_words_ids_and_brands_are_placed_not_sent_back():
    cands = [{"id": "1", "_text": "valio butter 500 g unsalted"}, {"id": "2", "_text": "arla butter 500 g salted"}]
    intent = {"ignored_terms": ["Valio", "unsalted", "loaves", "460155", "plutonium"]}
    kept, narrowed = agent._place_terms(intent, cands, ["460155"])
    assert [p["id"] for p in kept] == ["1"] and narrowed == ["Valio", "unsalted"]
    assert intent["ignored_terms"] == ["plutonium"]  # still asks back about a word nothing carries


def test_the_profile_never_sets_a_fat_level_the_product_names():
    mandate = {"profile": {"milk": {"fat_pct": 1.5, "shelf_life": "any"}}}
    cands = [{"fatPct": 0.0, "_text": "skimmed milk"}, {"fatPct": 1.5, "_text": "semi skimmed milk"}]
    intent = {"product": "skimmed milk", "fat_pct": None, "shelf_life": None}
    out = agent.apply_profile(cands, intent, mandate)
    assert intent["fat_pct"] is None and len(out) == 2


def test_english_milk_names_state_the_fat_level():
    for text, fat in (("20 litres of skimmed milk", 0.0), ("40 L semi-skimmed milk", 1.5), ("whole milk 30 l", 3.5)):
        i = {"product": "milk", "fat_pct": None, "shelf_life": None, "attributes": [], "ignored_terms": []}
        ai._finnish_terms(i, text)
        assert i["fat_pct"] == fat, text


def test_confirmed_stock_is_recommended_when_it_costs_little_more():
    base = {"status": "ok", "reliability": .97, "carbon": None, "per_unit": 1, "units": 40, "sales_unit": "ST", "unit_price": 1.0,
            "delivery_date": "2026-11-16", "score_parts": {}}
    terminal = dict(base, productId="T", name="Terminal milk", total=37.24, score=.99, unconfirmed_stock=True)
    stock = dict(base, productId="S", name="Stock milk", total=40.00, score=.95)
    opts = agent.build_options([terminal, stock], {"quantity": 40}, tolerance=25)
    assert opts[0]["lines"][0]["productId"] == "S" and "stock confirmed by Valio" in opts[0]["why"]
    far = dict(stock, total=99.0)
    assert agent.build_options([terminal, far], {"quantity": 40}, tolerance=25)[0]["lines"][0]["productId"] == "T"
