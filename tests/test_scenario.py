"""Scenario generator invariants."""

from __future__ import annotations

import pytest

from arena.scenario import TIERS, WorldInvalid, generate_world, validate_world
from arena.world import Exposure


@pytest.mark.parametrize("scenario", list(TIERS))
@pytest.mark.parametrize("seed", [0, 1, 2, 17, 500, 123456])
def test_generated_worlds_pass_invariants(scenario, seed):
    w = generate_world(scenario, seed)
    validate_world(w)  # must not raise
    assert w.objective_node in w.nodes
    assert any(n.zone == "perimeter" for n in w.nodes.values())
    assert w.business_services()
    # a viable soft public entry exists
    assert any(
        s.exposure == Exposure.PUBLIC and s.weakness() > 0.2
        for n in w.nodes.values() for s in n.services.values()
    )


def test_perimeter_is_pre_discovered():
    w = generate_world("T1", 9)
    perim = {n.id for n in w.nodes.values() if n.zone == "perimeter"}
    assert perim <= w.discovered_nodes


def test_availability_full_at_start():
    w = generate_world("T2", 9)
    assert w.availability() == 1.0


def test_unknown_scenario_raises():
    with pytest.raises(KeyError):
        generate_world("T99", 0)
