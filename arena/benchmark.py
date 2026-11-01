"""Reproducible benchmark + leaderboard (Phase 4).

A benchmark *entry* is a named (challenger, warden) policy pair. The benchmark
runs each entry over a fixed suite — a set of tiers, each with a fixed seed
range — and reports the aggregated, variance-bearing scores plus a version
manifest, so a result can be regenerated rather than merely trusted.

The leaderboard ranks Warden entries by their mean scalar across the suite,
alongside the axes the scalar is built from, so a high scalar achieved by
gaming one axis is visible in the breakdown.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable

from . import __version__
from .agents import RuleChallenger, RuleWarden
from .harness import run_batch

AgentFactory = Callable[[], Any]

# The standard suite: tier -> seed list. Kept small enough to run in seconds;
# widen the ranges for a publishable run.
STANDARD_SUITE: dict[str, list[int]] = {
    "T1": list(range(0, 20)),
    "T2": list(range(0, 20)),
    "T3": list(range(0, 20)),
}


def bootstrap_ci(values: list[float], *, n_boot: int = 1000, alpha: float = 0.05,
                 seed: int = 0) -> list[float]:
    """Deterministic percentile-bootstrap CI for the mean of ``values``."""
    if not values:
        return [0.0, 0.0]
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot))
    lo = means[int((alpha / 2) * n_boot)]
    hi = means[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return [round(lo, 4), round(hi, 4)]


@dataclass
class BenchmarkEntry:
    name: str
    challenger_factory: AgentFactory
    warden_factory: AgentFactory


@dataclass
class BenchmarkResult:
    entry: str
    manifest: dict[str, Any]
    per_tier: dict[str, dict[str, Any]] = field(default_factory=dict)
    overall: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"entry": self.entry, "manifest": self.manifest,
                "per_tier": self.per_tier, "overall": self.overall}


def run_benchmark(entry: BenchmarkEntry, suite: dict[str, list[int]] | None = None,
                  *, workers: int = 1) -> BenchmarkResult:
    suite = suite or STANDARD_SUITE
    per_tier: dict[str, dict[str, Any]] = {}
    warden_scalars: list[float] = []
    chal_scalars: list[float] = []
    f1s: list[float] = []
    avail: list[float] = []
    faith: list[float] = []
    winrates: list[float] = []
    per_match_scalar: list[float] = []
    per_match_win: list[float] = []

    for tier, seeds in suite.items():
        report = run_batch(tier, seeds, entry.challenger_factory, entry.warden_factory,
                           workers=workers)
        per_match_scalar.extend(c.warden_scalar for c in report.cards)
        per_match_win.extend(1.0 if c.winner == "warden" else 0.0 for c in report.cards)
        per_tier[tier] = {
            "win_rates": report.win_rates,
            "warden_scalar": report.aggregates["warden.scalar"],
            "challenger_scalar": report.aggregates["challenger.scalar"],
            "classification_f1": report.aggregates["warden.classification_f1"],
            "availability": report.aggregates["warden.availability"],
            "faithfulness": report.aggregates["integrity.explanation_faithfulness"],
        }
        warden_scalars.append(report.aggregates["warden.scalar"]["mean"])
        chal_scalars.append(report.aggregates["challenger.scalar"]["mean"])
        f1s.append(report.aggregates["warden.classification_f1"]["mean"])
        avail.append(report.aggregates["warden.availability"]["mean"])
        faith.append(report.aggregates["integrity.explanation_faithfulness"]["mean"])
        winrates.append(report.win_rates.get("warden", 0.0))

    def mean(xs):
        return round(sum(xs) / len(xs), 4) if xs else 0.0

    overall = {
        "warden_scalar": mean(warden_scalars),
        "challenger_scalar": mean(chal_scalars),
        "classification_f1": mean(f1s),
        "availability": mean(avail),
        "faithfulness": mean(faith),
        "warden_winrate": mean(winrates),
        "warden_scalar_ci95": bootstrap_ci(per_match_scalar),
        "warden_winrate_ci95": bootstrap_ci(per_match_win),
        "n_matches": len(per_match_scalar),
    }
    manifest = {
        "arena_version": __version__,
        "entry": entry.name,
        "suite": {t: [min(s), max(s), len(s)] for t, s in suite.items()},
        "warden": entry.warden_factory().__class__.__name__,
        "challenger": entry.challenger_factory().__class__.__name__,
    }
    return BenchmarkResult(entry=entry.name, manifest=manifest,
                           per_tier=per_tier, overall=overall)


def leaderboard(entries: list[BenchmarkEntry], suite: dict[str, list[int]] | None = None,
                *, workers: int = 1) -> dict[str, Any]:
    """Run several entries and rank Wardens by overall scalar (with breakdown)."""
    results = [run_benchmark(e, suite, workers=workers) for e in entries]
    ranked = sorted(results, key=lambda r: r.overall["warden_scalar"], reverse=True)
    return {
        "ranking": [
            {"entry": r.entry, "warden_scalar": r.overall["warden_scalar"],
             "warden_scalar_ci95": r.overall["warden_scalar_ci95"],
             "winrate": r.overall["warden_winrate"],
             "f1": r.overall["classification_f1"],
             "availability": r.overall["availability"],
             "faithfulness": r.overall["faithfulness"]}
            for r in ranked
        ],
        "results": [r.to_dict() for r in results],
    }


def default_entries() -> list[BenchmarkEntry]:
    from .agents import AdaptiveWarden, PassiveWarden, IsolateAllWarden, DeclareAllWarden
    return [
        BenchmarkEntry("rule", lambda: RuleChallenger(), lambda: RuleWarden()),
        BenchmarkEntry("adaptive", lambda: RuleChallenger(), lambda: AdaptiveWarden()),
        BenchmarkEntry("passive", lambda: RuleChallenger(), lambda: PassiveWarden()),
        BenchmarkEntry("isolate_all", lambda: RuleChallenger(), lambda: IsolateAllWarden()),
        BenchmarkEntry("declare_all", lambda: RuleChallenger(), lambda: DeclareAllWarden()),
    ]
