"""Week plans: the chef signs the week once, the agent orders inside it alone, only exceptions reach a person."""
import pytest

from gateway import agent, plans, policy, store

from .conftest import FakeValio

HOTEL, SCHOOL = "mnd_hotel_helsinki", "mnd_school_espoo"


@pytest.fixture(autouse=True)
def clean():
    before = {o["id"] for o in store.list_orders(100000)}
    yield
    with store._lock:
        for o in store.list_orders(100000):
            if o["id"] not in before:
                store._db.execute("DELETE FROM orders WHERE id = ?", (o["id"],))
        store._db.execute("DELETE FROM plans")
        store._db.commit()


def signed_plan(units=2000, budget=2500.0, start="2026-11-14", end="2026-11-20", mandate=HOTEL):
    plan, err = plans.create(mandate, [{"productId": "464306", "units": units}], start, end, budget, "Test chef")
    assert err is None
    plan, err = plans.approve(plan["id"], "Jari Laine" if budget > 1000 else "Aino Virtanen", "week 47 milk")
    assert err is None
    return plan


def order(units, key, mandate=HOTEL, pid="464306", client=None):
    _, jws, err = agent.quote(mandate, "2026-11-16", [{"productId": pid, "units": units}], client or FakeValio())
    assert err is None, err
    return agent.order_from_jws(jws, key)


def test_without_a_plan_a_big_order_waits_for_a_person():
    o, _ = order(900, "k-plan-none")
    assert o["status"] == "pending_approval"


def test_inside_a_signed_plan_the_agent_orders_alone():
    plan = signed_plan()
    o, _ = order(900, "k-plan-in")
    assert o["status"] == "placed"
    assert o["record"]["context"]["plan"]["id"] == plan["id"]
    rule = next(r for r in o["policy"]["rules"] if r["rule"] == "Auto-approve spend limit")
    assert plan["id"] in rule["detail"] and "Jari Laine" in rule["detail"]


def test_the_plan_runs_out_then_a_person_decides_again():
    signed_plan(units=1000, budget=1100)
    assert order(900, "k-plan-a")[0]["status"] == "placed"
    o, _ = order(600, "k-plan-b")  # 1,500 units > 1,000 planned, €606 > the agent's own €500
    assert o["status"] == "pending_approval"
    assert "fewer units left" in next(r["detail"] for r in o["policy"]["rules"] if r["rule"] == "Auto-approve spend limit")


def test_budget_is_counted_across_orders():
    signed_plan(units=5000, budget=1000)
    assert order(600, "k-plan-c")[0]["status"] == "placed"  # €606
    assert order(600, "k-plan-d")[0]["status"] == "pending_approval"  # would be €1,212


def test_a_product_outside_the_plan_is_not_covered():
    signed_plan()
    o, _ = order(900, "k-plan-other", pid="464073")
    assert o["status"] == "pending_approval"


def test_a_draft_plan_covers_nothing():
    plans.create(HOTEL, [{"productId": "464306", "units": 2000}], "2026-11-14", "2026-11-20", 2500, "chef")
    assert order(900, "k-plan-draft")[0]["status"] == "pending_approval"


def test_a_revoked_plan_covers_nothing():
    plan = signed_plan()
    plans.revoke(plan["id"], "Jari Laine", "menu changed")
    assert order(900, "k-plan-revoked")[0]["status"] == "pending_approval"


def test_delivery_outside_the_window_is_not_covered():
    signed_plan(start="2026-11-17", end="2026-11-20")  # offer delivers 2026-11-16
    assert order(900, "k-plan-window")[0]["status"] == "pending_approval"


def test_hard_rules_still_apply_inside_a_plan(monkeypatch):
    signed_plan()
    real = policy.load
    monkeypatch.setattr(policy, "load", lambda mid, ov=None: dict(real(mid, ov), valid_until="2000-01-01"))
    assert order(900, "k-plan-hard")[0]["status"] == "blocked"


def test_a_forbidden_product_cannot_be_planned():
    plan, err = plans.create(SCHOOL, [{"productId": "463380", "units": 10}], "2026-11-14", "2026-11-20", 100, "chef")
    if plan is None:
        assert "forbids" in err or "required" in err


def test_only_an_approver_with_enough_authority_signs():
    plan, _ = plans.create(HOTEL, [{"productId": "464306", "units": 3000}], "2026-11-14", "2026-11-20", 3000, "chef")
    assert "up to" in plans.approve(plan["id"], "Aino Virtanen")[1]
    assert "not an approver" in plans.approve(plan["id"], "Mallory")[1]
    assert plans.approve(plan["id"], "Jari Laine")[0]["status"] == "approved"


def test_a_tampered_plan_is_ignored():
    plan = signed_plan(units=100, budget=200)
    stored = store.get_plan(plan["id"])
    stored["lines"][0]["units"] = 100000  # edited in the database, not re-signed
    stored["max_total_eur"] = 100000
    store.save_plan(stored)
    assert order(900, "k-plan-tamper")[0]["status"] == "pending_approval"
    assert any(e["type"] == "security.plan_signature_invalid" for e in store.events(limit=50))


@pytest.mark.parametrize("args", [
    ([], "2026-11-14", "2026-11-20", 100), ([{"productId": "464306", "units": -1}], "2026-11-14", "2026-11-20", 100),
    ([{"productId": "464306", "units": 1}], "2026-11-20", "2026-11-14", 100),
    ([{"productId": "464306", "units": 1}], "2026-11-01", "2026-11-30", 100),
    ([{"productId": "464306", "units": 1}], "2026-11-14", "2026-11-20", float("nan")),
])
def test_malformed_plans_are_refused(args):
    assert plans.create(HOTEL, *args, "chef")[0] is None


def test_plan_orders_do_not_use_up_the_agents_own_24h_allowance():
    signed_plan()
    before = store.auto_spend_since(HOTEL, "2000-01-01")
    assert order(900, "k-plan-big")[0]["status"] == "placed"
    assert store.auto_spend_since(HOTEL, "2000-01-01") == before  # the plan's budget limits it, not the agent's own


@pytest.mark.twin_check
def test_approving_an_order_already_placed_again_under_a_plan_is_refused():
    """Review of the week plan: an order waited for approval, the same order was then placed under a signed plan;
    approving the old one would have doubled it."""
    from gateway import agent, store
    from .conftest import FakeValio
    lines = [{"productId": "464073", "units": 640}]
    _, jws, _ = agent.quote("mnd_hotel_helsinki", "2026-11-16", lines, FakeValio())
    waiting, _ = agent.order_from_jws(jws, "k-plan-twin-1")
    assert waiting["status"] == "pending_approval"
    placed = dict(waiting, id="ord_twin_placed", offer_id="ofr_twin", idempotency_key="k-plan-twin-2", status="placed",
                  created=None)
    store.save_order(placed)
    o, err = agent.decide(waiting["id"], "approve", "Aino Virtanen", client=FakeValio())
    assert o is None and "ord_twin_placed" in err
    assert agent.decide(waiting["id"], "reject", "Aino Virtanen")[0]["status"] == "rejected"
