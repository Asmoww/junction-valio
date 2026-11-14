"""scripts/autonomy.py: a signed week plan turns spend-limit escalations into orders placed alone."""
import importlib.util
from pathlib import Path


def load():
    spec = importlib.util.spec_from_file_location("autonomy", Path(__file__).parent.parent / "scripts" / "autonomy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_plan_moves_spend_limit_escalations_to_alone(valio):
    from gateway import store
    with store._lock:
        store._db.execute("DELETE FROM plans"); store._db.commit()
    a = load()
    out = a.measure(["We need 900 litres of lactose-free milk by Monday.", "We need 100 litres of lactose-free milk by Monday."])
    assert out["without_plan"]["person"] == 1 and out["without_plan"]["alone"] == 1
    assert out["with_plan"]["alone"] == 2 and out["plan"]["products"] == 1
    assert out["changed"][0]["before"] == "person" and out["changed"][0]["after"] == "alone"
    with store._lock:
        store._db.execute("DELETE FROM plans"); store._db.execute("DELETE FROM orders"); store._db.commit()
