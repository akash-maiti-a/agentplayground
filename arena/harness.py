"""Evaluation harness — batches of matches with variance reporting.

Matches are independent and embarrassingly parallel. This module runs a batch
over a set of seeds, scores each from its log, and aggregates per-metric mean
and standard deviation (never single-seed claims). It also records a version
*manifest* so any batch can be regenerated rather than merely stored.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable

from . import __version__
from .agents import RuleChallenger, RuleWarden
from .control import ResourceLimits
from .match import run_match
from .scoring import ScoreCard, score_match

AgentFactory = Callable[[], Any]


@dataclass
class BatchReport:
    scenario_id: str
    seeds: list[int]
    manifest: dict[str, Any]
    cards: list[ScoreCard] = field(default_factory=list)
    aggregates: dict[str, dict[str, float]] = field(default_factory=dict)
    win_rates: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "seeds": self.seeds,
            "manifest": self.manifest,
            "win_rates": self.win_rates,
            "aggregates": self.aggregates,
            "cards": [c.to_dict() for c in self.cards],
        }


def _agg(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "std": 0.0, "n": 0}
    mean = statistics.fmean(values)
    std = statistics.pstdev(values) if len(values) > 1 else 0.0
    return {"mean": round(mean, 4), "std": round(std, 4), "n": len(values)}


def run_batch(
    scenario_id: str,
    seeds: list[int],
    challenger_factory: AgentFactory = lambda: RuleChallenger(),
    warden_factory: AgentFactory = lambda: RuleWarden(),
    *,
    limits: ResourceLimits | None = None,
    sensor_coverage: float = 1.0,
) -> BatchReport:
    cards: list[ScoreCard] = []
    wins = {"warden": 0, "challenger": 0, "draw": 0}
    for seed in seeds:
        result = run_match(
            scenario_id, seed, challenger_factory(), warden_factory(),
            limits=limits, sensor_coverage=sensor_coverage,
        )
        card = score_match(result.log)
        cards.append(card)
        wins[result.winner] = wins.get(result.winner, 0) + 1

    # aggregate the key axes across seeds
    axes_warden = ["mttd_score", "mttc_score", "classification_f1", "availability",
                   "containment_quality", "false_alarm_score", "recovery"]
    axes_chal = ["objective_progress", "stealth", "efficiency"]
    aggregates: dict[str, dict[str, float]] = {}
    for k in axes_warden:
        aggregates[f"warden.{k}"] = _agg([c.warden.get(k, 0.0) for c in cards])
    for k in axes_chal:
        aggregates[f"challenger.{k}"] = _agg([c.challenger.get(k, 0.0) for c in cards])
    aggregates["warden.scalar"] = _agg([c.warden_scalar for c in cards])
    aggregates["challenger.scalar"] = _agg([c.challenger_scalar for c in cards])
    aggregates["integrity.rule_compliance"] = _agg([c.integrity.get("rule_compliance", 0.0) for c in cards])

    n = max(1, len(seeds))
    win_rates = {k: round(v / n, 4) for k, v in wins.items()}

    manifest = {
        "arena_version": __version__,
        "scenario_id": scenario_id,
        "n_seeds": len(seeds),
        "challenger": challenger_factory().__class__.__name__,
        "warden": warden_factory().__class__.__name__,
        "sensor_coverage": sensor_coverage,
        "limits": vars(limits) if limits else vars(ResourceLimits()),
    }
    return BatchReport(scenario_id=scenario_id, seeds=list(seeds), manifest=manifest,
                       cards=cards, aggregates=aggregates, win_rates=win_rates)
