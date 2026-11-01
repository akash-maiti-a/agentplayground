"""Regression tests for the v2 improvements (AdaptiveWarden, StealthChallenger,
RL v2, cross-process determinism, bootstrap CIs, export helpers)."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

from arena.agents import (
    AdaptiveWarden, DeclareAllWarden, IsolateAllWarden, PassiveWarden,
    RuleChallenger, RuleWarden, StealthChallenger,
)
from arena.agents.warden import calibrated_p
from arena.benchmark import BenchmarkEntry, bootstrap_ci, run_benchmark
from arena.match import run_match
from arena.rl import QWarden, WardenEnv, train_warden
from arena.schema import EventType

ROOT = pathlib.Path(__file__).resolve().parents[1]
_SUITE = {"T1": list(range(10)), "T2": list(range(10)), "T3": list(range(10))}


def _overall(warden_factory, challenger_factory=RuleChallenger):
    return run_benchmark(BenchmarkEntry("x", challenger_factory, warden_factory), _SUITE).overall


# --- cross-process determinism (the bug the in-process gate could not see) --

_PROBE = (
    "from arena.harness import run_batch;"
    "r=run_batch('T2', list(range(8)));"
    "print([c.warden_scalar for c in r.cards])"
)


def test_results_do_not_depend_on_hash_seed():
    """Set iteration order changes with PYTHONHASHSEED; before the fix in
    match.py, results (and RL training) silently varied between processes."""
    outs = []
    for h in ("1", "2", "3"):
        env = dict(os.environ, PYTHONHASHSEED=h)
        outs.append(subprocess.run([sys.executable, "-c", _PROBE], cwd=ROOT, env=env,
                                   capture_output=True, text=True, check=True).stdout)
    assert outs[0] == outs[1] == outs[2]


# --- AdaptiveWarden -----------------------------------------------------------

def test_adaptive_beats_rule_warden_on_scalar_and_f1():
    rule, adaptive = _overall(lambda: RuleWarden()), _overall(lambda: AdaptiveWarden())
    assert adaptive["warden_scalar"] > rule["warden_scalar"], (adaptive, rule)
    assert adaptive["classification_f1"] > rule["classification_f1"] + 0.2, (adaptive, rule)


def test_adaptive_beats_every_degenerate_baseline():
    adaptive = _overall(lambda: AdaptiveWarden())["warden_scalar"]
    for f in (PassiveWarden, IsolateAllWarden, DeclareAllWarden):
        assert adaptive > _overall(f)["warden_scalar"], f.__name__


def test_adaptive_holds_up_against_stealth_challenger():
    rule = _overall(lambda: RuleWarden(), StealthChallenger)
    adaptive = _overall(lambda: AdaptiveWarden(), StealthChallenger)
    assert adaptive["warden_winrate"] >= rule["warden_winrate"]
    assert adaptive["warden_scalar"] > rule["warden_scalar"]


def test_calibration_is_monotone_and_centered():
    for f in (0.2, 0.5, 0.9):
        mid = 0.3 + 0.2 * f
        assert abs(calibrated_p(mid, f) - 0.5) < 1e-6
        assert calibrated_p(mid + 0.1, f) > 0.5 > calibrated_p(mid - 0.1, f)


def test_new_agents_never_emit_invalid_intents():
    for tier in ("T1", "T3"):
        res = run_match(tier, 5, StealthChallenger(), AdaptiveWarden(use_decoys=True))
        assert not res.log.events_of(EventType.INTENT_REJECTED.value)


def test_new_agents_are_deterministic():
    def tr():
        res = run_match("T2", 9, StealthChallenger(), AdaptiveWarden())
        return [e["hash"] for e in res.log.events_of(EventType.STATE_HASH.value)]
    assert tr() == tr()


# --- RL v2 -------------------------------------------------------------------

def test_env_v2_shape():
    env = WardenEnv("T1", version="v2")
    obs, _ = env.reset(seed=1)
    assert len(obs) == env.observation_dim == 8


def test_q_v2_beats_q_v1_on_noisy_tier():
    def score(ver):
        L = train_warden("T2", episodes=150, seeds=list(range(30)), epsilon=0.25, version=ver)
        return run_benchmark(BenchmarkEntry(ver, RuleChallenger, lambda: QWarden(L)),
                             {"T2": list(range(100, 120))}).overall
    v1, v2 = score("v1"), score("v2")
    assert v2["warden_winrate"] > v1["warden_winrate"], (v1, v2)


def test_training_records_learning_curve():
    L = train_warden("T1", episodes=12, seeds=list(range(4)), version="v2")
    assert len(L.curve) == 12 and L.epsilon < 0.25


# --- benchmark statistics ----------------------------------------------------

def test_bootstrap_ci_is_deterministic_and_brackets_mean():
    xs = [0.1, 0.4, 0.5, 0.9, 0.6, 0.3, 0.7]
    lo, hi = bootstrap_ci(xs)
    assert (lo, hi) == tuple(bootstrap_ci(xs))
    assert lo <= sum(xs) / len(xs) <= hi


def test_export_replay_is_deterministic():
    from arena.export import sample_replay
    a, b = sample_replay([100, 101, 102]), sample_replay([100, 101, 102])
    assert a == b
    assert a["runs"]["rule"]["timeline"] and a["topology"]["nodes"]


# --- engine: verdict ground truth survives containment ------------------------

def test_declaring_an_isolated_foothold_is_correct():
    from arena.engine import Engine
    from arena.rng import DeterministicRNG
    from arena.scenario import generate_world
    from arena.schema import Intent, Side
    from arena.world import NodeStatus
    w = generate_world("T1", 3)
    nid = sorted(w.nodes)[0]
    w.nodes[nid].status = NodeStatus.FOOTHOLD
    eng = Engine(w, DeterministicRNG(3))
    eng.resolve_tick(None, Intent(Side.WARDEN, "ISOLATE", {"node": nid}, "contain"))
    out = eng.resolve_tick(None, Intent(Side.WARDEN, "DECLARE", {"node": nid, "verdict": "compromised"}, "declare"))
    dec = [e for e in out.events if e.get("type") == "resolution" and e.get("verb") == "DECLARE"][0]
    assert dec["detail"]["truth"] == "compromised" and dec["detail"]["correct"]
