"""Reward-hacking resistance (RQ5) and faithfulness tests.

The central property: the *balanced* rule-based Warden must beat every
degenerate single-axis strategy on the scalar readout. If a future scoring
change lets a degenerate policy win, this test fails — which is exactly the
"adversarial scoring review" tripwire the plan calls for.
"""

from __future__ import annotations

import pytest

from arena.agents import (
    DeclareAllWarden, IsolateAllWarden, PassiveWarden, RuleChallenger, RuleWarden,
)
from arena.benchmark import BenchmarkEntry, run_benchmark
from arena.match import run_match
from arena.scoring import score_match

_SUITE = {"T1": list(range(10)), "T2": list(range(10))}


def _scalar(warden_factory) -> float:
    entry = BenchmarkEntry("x", lambda: RuleChallenger(), warden_factory)
    return run_benchmark(entry, _SUITE).overall["warden_scalar"]


def test_rule_warden_beats_all_degenerate_baselines():
    rule = _scalar(lambda: RuleWarden())
    passive = _scalar(lambda: PassiveWarden())
    isolate_all = _scalar(lambda: IsolateAllWarden())
    declare_all = _scalar(lambda: DeclareAllWarden())
    assert rule > passive, (rule, passive)
    assert rule > isolate_all, (rule, isolate_all)
    assert rule > declare_all, (rule, declare_all)


def test_isolate_all_tanks_availability():
    res = run_match("T2", 3, RuleChallenger(), IsolateAllWarden())
    card = score_match(res.log)
    assert card.warden["availability"] < 0.7


def test_declare_all_has_low_faithfulness():
    """Declaring every node 'compromised' with high confidence is poorly
    calibrated against ground truth, so faithfulness should be low."""
    res = run_match("T2", 3, RuleChallenger(), DeclareAllWarden())
    card = score_match(res.log)
    assert card.integrity["explanation_faithfulness"] < 0.5


def test_faithful_warden_scores_well_on_calibration():
    res = run_match("T1", 4471, RuleChallenger(), RuleWarden())
    card = score_match(res.log)
    assert card.integrity["n_claims"] > 0
    assert card.integrity["explanation_faithfulness"] >= 0.5
