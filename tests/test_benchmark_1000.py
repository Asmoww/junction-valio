"""Regressions from ChatGPT's 1,000-case benchmark (run 14 Nov, in-process, fake Valio).

Each test is one failure the benchmark found in the product: malformed quote input that got signed, a Valio
error that crashed instead of refusing, an approval that placed an order the world had moved away from, and
an agent token that could act for any buyer."""
import pytest

from gateway import agent, policy, store

from .conftest import FakeValio

HOTEL, SCHOOL = "mnd_hotel_helsinki", "mnd_school_espoo"
LINE = [{"productId": "464306", "units": 10}]


@pytest.fixture(autouse=True)
def forget_orders_made_here():
    before = {o["id"] for o in store.list_orders(100000)}
    yield
    with store._lock:
        for o in store.list_orders(100000):
            if o["id"] not in before:
                store._db.execute("DELETE FROM orders WHERE id = ?", (o["id"],))
        store._db.commit()


class Broken(FakeValio):
    def __init__(self, where="availability", **kw):
        super().__init__(**kw)
        self.where = where

    def prices(self, *a):
        if self.where == "prices":
            raise RuntimeError("HTTP 500")
        return super().prices(*a)

    def availability(self, *a):
        if self.where == "availability":
            raise RuntimeError("HTTP 429 after retries")
        return super().availability(*a)


class Odd(FakeValio):
    """Valio answers, but with something odd in the availability data."""
    def __init__(self, patch, **kw):
        super().__init__(**kw)
        self.patch = patch

    def availability(self, *a):
        now, out = super().availability(*a)
        for v in out.values():
            self.patch(v)
        return now, out


# ---- Quote input ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kw, word", [
    ({"deliver_by": "tomorrow"}, "date"), ({"deliver_by": "2026-02-30"}, "real date"), ({"deliver_by": "2020-01-01"}, "past"),
    ({"requested_quantity": float("nan")}, "positive"), ({"requested_quantity": -5}, "positive"),
    ({"requested_unit": "km"}, "requested_unit"),
])
def test_malformed_quote_fields_are_refused_not_signed(kw, word):
    args = {"deliver_by": "2026-11-16", "requested_quantity": None, "requested_unit": None, **kw}
    offer, jws, err = agent.quote(HOTEL, args["deliver_by"], LINE, FakeValio(),
                                  requested_quantity=args["requested_quantity"], requested_unit=args["requested_unit"])
    assert offer is None and jws is None and word in err


@pytest.mark.parametrize("line", [
    {"productId": "464306", "units": 10, "unitPrice": 0}, {"productId": "464306", "units": 10, "lineTotal": -999},
    {"productId": "464306", "units": 10, "substitution": "TRUST_ME"},
])
def test_callers_cannot_send_prices_or_invented_substitution_types(line):
    offer, _, err = agent.quote(HOTEL, "2026-11-16", [line], FakeValio())
    assert offer is None and err


# ---- Valio errors and odd data fail closed ---------------------------------------------------------------------
@pytest.mark.parametrize("where", ["prices", "availability"])
def test_a_valio_error_is_a_clean_refusal_not_a_crash(where):
    offer, _, err = agent.quote(HOTEL, "2026-11-16", LINE, Broken(where))
    assert offer is None and "Valio didn't answer" in err


@pytest.mark.parametrize("patch", [
    lambda v: v.update(closingTime="not-a-time"),
    lambda v: v["scheduleLines"][0].update(availableAmount=float("inf")),
    lambda v: v["scheduleLines"][0].update(availableAmount="lots"),
])
def test_unreadable_cutoff_or_stock_counts_as_unavailable(patch):
    offer, _, err = agent.quote(HOTEL, "2026-11-16", LINE, Odd(patch))
    assert offer is None and err


def test_a_valio_error_at_commit_is_a_clean_refusal(monkeypatch):
    _, jws, _ = agent.quote(HOTEL, "2026-11-16", LINE, FakeValio())
    monkeypatch.setattr(agent, "ValioClient", lambda: Broken("prices"))
    order, err = agent.order_from_jws(jws, "k-bench-commit-500")
    assert order is None and "Valio didn't answer" in err


# ---- What the agent signed for vs what the buyer asked ------------------------------------------------------------
def test_an_offer_covering_a_tenth_of_the_request_is_not_placed_alone():
    _, jws, _ = agent.quote(HOTEL, "2026-11-16", LINE, FakeValio(), requested_quantity=100, requested_unit="L")
    order, _ = agent.order_from_jws(jws, "k-bench-short")
    assert order["status"] != "placed"
    assert any(r["rule"] == "Full quantity" and not r["ok"] for r in order["policy"]["rules"])


# ---- Approval re-checks the world, not only the price after the lock ------------------------------------------------
def pending():
    _, jws, _ = agent.quote(HOTEL, "2026-11-16", [{"productId": "464306", "units": 900}], FakeValio())
    order, _ = agent.order_from_jws(jws, f"k-bench-pending-{id(jws)}")
    assert order["status"] == "pending_approval"
    return order["id"]


def patched_mandate(monkeypatch, **changes):
    real = policy.load

    def load(mid, overrides=None):
        m = real(mid, overrides)
        for path, value in changes.items():
            node = m
            *head, last = path.split("__")
            for k in head:
                node = node[k]
            node[last] = value
        return m
    monkeypatch.setattr(policy, "load", load)


@pytest.mark.parametrize("changes", [{"valid_until": "2000-01-01"}, {"hard_limits__max_order_total_eur": 500}])
def test_approval_refuses_when_a_hard_rule_now_fails(monkeypatch, changes):
    oid = pending()
    patched_mandate(monkeypatch, **changes)
    order, err = agent.decide(oid, "approve", "Aino Virtanen", client=FakeValio())
    assert order is None and "hard rule" in err
    assert store.get_order(oid)["status"] == "blocked"


@pytest.mark.parametrize("client", [
    FakeValio(prices={"464306": 1.55}),                                          # price moved
    Odd(lambda v: v["scheduleLines"][0].update(availableAmount=0)),              # stock gone
    Odd(lambda v: v.update(salesBlock=True)),                                    # sales block
    Odd(lambda v: v.update(logisticModel="Terminal")),                           # Valio no longer confirms stock
])
def test_approval_refuses_when_valio_changed_even_inside_the_price_lock(client):
    oid = pending()
    order, err = agent.decide(oid, "approve", "Aino Virtanen", client=client)
    assert order is None and "new offer" in err
    assert store.get_order(oid)["status"] == "expired"


def test_approval_waits_when_valio_does_not_answer():
    oid = pending()
    order, err = agent.decide(oid, "approve", "Aino Virtanen", client=Broken("prices"))
    assert order is None and "keeps waiting" in err
    assert store.get_order(oid)["status"] == "pending_approval"
    assert agent.decide(oid, "approve", "Aino Virtanen", client=FakeValio())[0]["status"] == "placed"


def test_a_problem_the_approver_was_never_shown_sends_it_back(monkeypatch):
    oid = pending()
    patched_mandate(monkeypatch, auto_approve__max_unstated_detail_spread_eur=-1,
                    min_delivery_reliability=1.01)  # a new gate-5 issue appears after the request was sent
    order, err = agent.decide(oid, "approve", "Aino Virtanen", client=FakeValio())
    assert order is None and "changed since you were asked" in err


def test_rejecting_needs_no_valio_check():
    oid = pending()
    assert agent.decide(oid, "reject", "Aino Virtanen", client=Broken("prices"))[0]["status"] == "rejected"


# ---- An agent token acts for one buyer ------------------------------------------------------------------------------
def test_a_token_bound_to_the_school_cannot_quote_or_order_for_the_hotel():
    _, hotel_jws, _ = agent.quote(HOTEL, "2026-11-16", LINE, FakeValio())
    t = agent.CALLER.set(SCHOOL)
    try:
        offer, _, err = agent.quote(HOTEL, "2026-11-16", LINE, FakeValio())
        assert offer is None and "acts for mnd_school_espoo" in err
        order, err = agent.order_from_jws(hotel_jws, "k-bench-cross")
        assert order is None and "acts for" in err
    finally:
        agent.CALLER.reset(t)
    assert any(e["type"] == "security.mandate_mismatch" for e in store.events(limit=50))


def test_a_token_bound_to_the_hotel_works_for_the_hotel():
    t = agent.CALLER.set(HOTEL)
    try:
        offer, _, err = agent.quote(HOTEL, "2026-11-16", LINE, FakeValio())
        assert offer and err is None
    finally:
        agent.CALLER.reset(t)


# ---- Outside browser test of the live demo (14 Nov 05:23): reading exact limits and conditions ---------------------
from datetime import date, timedelta  # noqa: E402

from gateway import request_checks as rc  # noqa: E402

EGGS_1 = ("Order exactly 24 individual eggs for Monday. Maximum quantity is 24 eggs, not 24 packs. "
          "If the smallest available pack would exceed 24 eggs, ask me instead of rounding up.")
EGGS_2 = ("Order exactly 24 individual eggs for Monday, with no overdelivery. If pack sizes would require more eggs, "
          "ask me for approval instead of rounding up.")


@pytest.mark.parametrize("text", [EGGS_1, EGGS_2])
def test_a_restated_limit_is_one_quantity_not_a_shopping_list(text):
    t = rc.drop_exact_clauses(rc.normalise(text))
    assert rc.exact_quantity(text)
    assert t.count("24") == 1 and "round" not in t and "individual" not in t  # one quantity left, limit kept as a flag


def test_a_limit_sentence_that_orders_something_else_is_kept():
    t = rc.drop_exact_clauses("Order 24 eggs for Monday. No more than 24 eggs, and also 10 kg butter.")
    assert "butter" in t


def test_do_not_order_only_one_item_is_not_do_not_order():
    text = ("Order exactly 12 litres of lactose-free milk AND 24 eggs for Monday. Both items are required together; "
            "do not order only one item.")
    assert rc.request_type(text)[0] is None  # (with the real catalog it is then asked back as two lines)
    assert rc.request_type("Do not order anything, just check the price of milk")[0] == "question"


def test_exactly_means_a_person_decides_when_packs_round_up(valio):
    r = agent.run("Order exactly 100 litres of lactose-free milk by Monday.", HOTEL)
    rule = next(x for x in r["order"]["policy"]["rules"] if x["rule"] == "Exact quantity")
    assert rule["ok"]


def test_a_past_date_shows_the_date_that_failed(valio):
    r = agent.run("Order 20 litres of lactose-free milk for delivery yesterday.", HOTEL)
    assert r["ladder"]["action"] == "ask_back"
    assert r["steps"][0]["intent"]["deliver_by"] == (date.today() - timedelta(days=1)).isoformat()


# ---- Outside browser test round 2 (14 Nov 11:07): a budget stated in the request was ignored --------------------------
@pytest.mark.parametrize("text, eur", [
    ("Order 30 litres of lactose-free milk by Monday. My total budget for this purchase is EUR 5, including all charges.", 5),
    ("Order 100 litres of lactose-free milk by Monday, total budget €10.", 10),
    ("Buy 30 litres of lactose-free milk by Monday. Spend no more than five euros in total. If it costs more, ask me first.", 5),
    ("Order 30 litres of lactose-free milk by Monday, spending at most 4,50 €.", 4.5),
])
def test_a_budget_in_the_request_is_read(text, eur):
    assert rc.stated_budget(text) == eur
    assert not rc.exact_quantity(rc.drop_budget_clauses(text))


@pytest.mark.parametrize("text", ["Order no more than 24 eggs by Monday.", "Order 100 litres of milk by Monday, max 5 % fat."])
def test_a_limit_that_is_not_money_is_not_a_budget(text):
    assert rc.stated_budget(text) is None


def test_an_order_over_the_buyers_own_budget_is_never_placed(valio):
    r = agent.run("Order 100 litres of lactose-free milk by Monday. My total budget for this purchase is EUR 10.", HOTEL)
    assert r["ladder"]["action"] == "stop" and r["order"]["status"] == "blocked"
    rule = next(x for x in r["order"]["policy"]["rules"] if x["rule"] == "Within the buyer's stated budget")
    assert not rule["ok"] and "only the requester" in rule["detail"]


def test_an_order_inside_the_buyers_budget_goes_through(valio):
    r = agent.run("Order 100 litres of lactose-free milk by Monday. My total budget for this purchase is EUR 500.", HOTEL)
    assert next(x for x in r["order"]["policy"]["rules"] if x["rule"] == "Within the buyer's stated budget")["ok"]


def test_a_negative_quantity_is_not_shown_as_positive(valio):
    r = agent.run("Order -10 litres of lactose-free milk by Monday.", HOTEL)
    assert r["ladder"]["action"] == "ask_back" and r["steps"][0]["intent"]["quantity"] is None
