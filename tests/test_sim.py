"""Monday morning simulation: every scenario line names a known site and product."""
from gateway import sim


def test_scenario_is_well_formed():
    assert len(sim.SCENARIO) >= 25
    for site, pid, units, kind in sim.SCENARIO:
        assert site in sim.SITES and pid.isdigit() and units > 0 and kind
    assert sum(k.startswith("attack") for *_, k in sim.SCENARIO) >= 3
