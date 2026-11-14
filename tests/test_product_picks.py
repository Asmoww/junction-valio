"""Without Claude's reality check, the search takes the category of the thing itself, not of its processed forms
(outside runs 14 Nov: "300 eggs" bought cooked egg white, "60 kg tomatoes" a puree, "frozen peas" a pea patty)."""
from gateway import catalog


def prod(pid, cat, name):
    return {"id": pid, "category": cat, "name": name, "name_fi": "", "_text": name, "claims": [], "lactose": None,
            "allergens": [], "mayContain": [], "salesUnit": "ST", "fat": None}


FAKE = [prod("1", "10592", "Boiled and peeled eggs 1,5kg"), prod("2", "10592", "Cooked egg white 180g"),
        prod("3", "10592", "Liquid whole egg 5 kg"), prod("4", "10590", "Barn eggs L180 12300g"),
        prod("5", "41753", "Pea-rutabaga patty 50 g frozen"), prod("6", "41753", "Pea protein balls frozen"),
        prod("7", "41771", "Mozzarella pearl 1g 3kg frozen"), prod("8", "41771", "Mozzarella pearl 2g 3kg frozen"),
        prod("9", "41731", "Peas frozen 2,5kg"), prod("10", "21590", "Tomato puree 1kg"),
        prod("11", "21590", "Passata tomatoes 1kg"), prod("12", "17401", "TOMATO 1CL FINLAND")]


def pick(monkeypatch, q):
    monkeypatch.setattr(catalog, "_catalog", [dict(p) for p in FAKE])
    monkeypatch.setattr(catalog, "load", lambda: None)
    found, trace = catalog.search(q, [], 5)
    return trace["category"]


def test_eggs_are_shell_eggs(monkeypatch):
    assert pick(monkeypatch, "eggs") == "10590"


def test_frozen_peas_are_peas_not_patties_or_pearls(monkeypatch):
    assert pick(monkeypatch, "frozen peas") == "41731"


def test_tomatoes_are_tomatoes_not_puree(monkeypatch):
    assert pick(monkeypatch, "tomatoes") == "17401"


def test_asking_for_the_processed_form_gets_it(monkeypatch):
    assert pick(monkeypatch, "liquid egg") == "10592"
    assert pick(monkeypatch, "tomato puree") == "21590"
