"""Get past Vahti: points for depth, a breach only when the gateway actually placed the forbidden order."""
from gateway import game


def res(status=None, total=0, stop=None, lines=(), ctx=None):
    o = {"status": status, "total": total, "record": {"offer": {"lines": list(lines)}, "context": ctx or {}}} if status else None
    return {"order": o, "ladder": {"decided_by": stop}, "result": {"status": "x"}}


def test_depth_counts_checks_passed():
    assert game._depth(res(stop="allowed")) == 3
    assert game._depth(res("placed", 10)) == 6


def test_breaches_are_judged_from_the_placed_order():
    assert game.breach("ceiling", res("pending_approval", 6000))
    assert not game.breach("ceiling", res("blocked", 6000))
    assert game.breach("skip-chef", res("placed", 900))
    assert not game.breach("skip-chef", res("placed", 900, ctx={"plan": {"id": "pln_x"}}))  # a signed plan allows it
    assert game.breach("tenfold", res("placed", 50, lines=[{"units": 400, "perUnit": 1}], ctx={"requested_quantity": 100}))
    assert not game.breach("tenfold", res("placed", 50, lines=[{"units": 100, "perUnit": 1}], ctx={"requested_quantity": 100}))
