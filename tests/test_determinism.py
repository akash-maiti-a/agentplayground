"""The determinism gate (hard CI requirement).

A match must be a pure function of (scenario, seed, policies): running the same
configuration twice must produce an identical per-tick state-hash transcript
and identical final state. Any accidental nondeterminism surfaces here.
"""

from __future__ import annotations

import pytest

from arena.agents import RuleChallenger, RuleWarden
from arena.match import run_match
from arena.scenario import generate_world
from arena.schema import EventType


def _transcript(scenario: str, seed: int) -> list[str]:
    res = run_match(scenario, seed, RuleChallenger(), RuleWarden())
    return [e["hash"] for e in res.log.events_of(EventType.STATE_HASH.value)]


@pytest.mark.parametrize("scenario", ["T0", "T1", "T2"])
@pytest.mark.parametrize("seed", [0, 1, 4471, 99999])
def test_match_transcript_is_deterministic(scenario, seed):
    a = _transcript(scenario, seed)
    b = _transcript(scenario, seed)
    assert a == b
    assert a, "expected at least one tick"


@pytest.mark.parametrize("scenario", ["T0", "T1", "T2"])
def test_world_generation_is_deterministic(scenario):
    w1 = generate_world(scenario, 123)
    w2 = generate_world(scenario, 123)
    assert w1.state_hash() == w2.state_hash()


def test_different_seeds_differ():
    assert _transcript("T1", 1) != _transcript("T1", 2)


def test_final_result_fields_are_stable():
    r1 = run_match("T1", 4471, RuleChallenger(), RuleWarden())
    r2 = run_match("T1", 4471, RuleChallenger(), RuleWarden())
    assert (r1.winner, r1.reason, r1.ticks, r1.final_hash) == \
           (r2.winner, r2.reason, r2.ticks, r2.final_hash)
