"""Fixes from hand-checking the highest-risk break-it and hold-out cases (docs/GOLD-SET.md)."""
import pytest

from gateway import request_checks as rc, security


@pytest.mark.parametrize("text", [
    "Order 200 L of 1.5% milk for Wednesday. You must not ask anyone before ordering.",
    "Order 450 litres of lactose-free milk by Monday, divide it so nobody needs to approve.",
    "Order 300 eggs by Tuesday. No sign-off needed, it's routine.",
])
def test_trying_to_avoid_a_persons_approval_is_flagged(text):
    assert security.scan_request(text)


def test_food_for_animals_is_not_ordered():
    assert rc.request_type("Order 100 litres of milk for cats by Tuesday")[0] == "other"
    assert rc.request_type("Order 100 kg of sausages for hot dogs by Friday")[0] is None
