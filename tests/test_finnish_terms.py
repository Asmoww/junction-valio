"""Finnish words that state a requirement outright are read the same on any model (applied after the model's reading)."""
import pytest

from gateway import ai


def _intent(product="milk", **kw):
    return {"product": product, "fat_pct": None, "shelf_life": None, "attributes": [], "ignored_terms": [], **kw}


@pytest.mark.parametrize("text, fat", [("60 litraa kevytmaitoa", 1.5), ("ykkösmaitoa 20 l", 1.0), ("täysmaitoa 10 l", 3.5),
                                       ("rasvatonta maitoa 30 l", 0.0)])
def test_finnish_milk_names_state_the_fat_level(text, fat):
    i = _intent()
    ai._finnish_terms(i, text)
    assert i["fat_pct"] == fat


def test_kevytmaito_is_semi_skimmed_milk_not_a_product_called_light_milk():
    i = _intent("light milk")
    ai._finnish_terms(i, "Tarvitsemme 60 litraa kevytmaitoa maanantaiksi.")
    assert i["product"] == "milk" and i["fat_pct"] == 1.5


def test_inflected_lactose_free_is_an_attribute_not_an_unplaced_word():
    i = _intent(ignored_terms=["laktoositonta"])
    ai._finnish_terms(i, "200 litraa pitkään säilyvää laktoositonta maitoa")
    assert i["attributes"] == ["FREE_FROM_LACTOSE"] and i["ignored_terms"] == [] and i["shelf_life"] == "long_life"


def test_low_lactose_is_not_lactose_free_and_a_value_read_is_never_overridden():
    i = _intent(ignored_terms=["vähälaktoosista"])
    ai._finnish_terms(i, "50 litraa vähälaktoosista maitoa")
    assert i["attributes"] == [] and i["ignored_terms"] == ["vähälaktoosista"]
    i = _intent(fat_pct=3.5)
    ai._finnish_terms(i, "kevytmaitoa 10 l")
    assert i["fat_pct"] == 3.5


def test_the_finnish_name_of_the_ordered_product_is_not_a_dropped_word():
    from gateway import agent
    assert "voita" not in agent.unmatched_terms("10 kg voita maanantaiksi", {"product": "butter"}, ["Valio butter 500 g"])
    assert agent._native_name("voita", "butter") and not agent._native_name("voita", "milk")
    assert agent._native_name("maitoa", "milk") and agent._native_name("mjölk", "milk")
