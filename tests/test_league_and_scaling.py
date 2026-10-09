"""Co-evolution league (RQ3) and parallel-orchestration tests."""

from __future__ import annotations

from arena.harness import run_batch
from arena.league import run_league


def test_league_runs_and_is_deterministic():
    a = run_league("T1", seeds=list(range(4)), rounds=3)
    b = run_league("T1", seeds=list(range(4)), rounds=3)
    assert a.warden_ratings == b.warden_ratings
    assert a.dynamic in {"stable", "cycling", "dominance"}
    assert set(a.warden_ratings) == {"rule", "passive", "isolate_all", "declare_all"}


def test_league_rates_balanced_warden_above_passive():
    rep = run_league("T1", seeds=list(range(6)), rounds=4)
    # a warden that actually wins matches should out-rate the do-nothing one
    assert rep.warden_ratings["rule"] > rep.warden_ratings["passive"]


def test_parallel_batch_matches_serial():
    serial = run_batch("T1", list(range(12)), workers=1)
    parallel = run_batch("T1", list(range(12)), workers=4)
    # identical results regardless of worker count (matches are independent)
    assert serial.win_rates == parallel.win_rates
    assert [c.warden_scalar for c in serial.cards] == [c.warden_scalar for c in parallel.cards]


def test_batch_reports_variance():
    report = run_batch("T2", list(range(10)))
    agg = report.aggregates["warden.scalar"]
    assert agg["n"] == 10
    assert "mean" in agg and "std" in agg
