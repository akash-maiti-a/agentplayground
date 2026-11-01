"""Co-evolution league (RQ3) — populations of agents over repeated rounds.

Each round, every Warden policy plays every Challenger policy across a shared
seed set. Match outcomes update per-policy Elo ratings (Wardens and Challengers
are rated in one pool each). Across rounds we track rating trajectories and a
*rank-volatility* statistic that distinguishes the three dynamics the plan asks
about:

    * **stable equilibrium** — ratings converge, rank order stops changing
      (volatility -> 0),
    * **arms race / cycling** — rank order keeps churning (volatility stays
      high), e.g. rock-paper-scissors among strategies,
    * **collapse** — one policy dominates everything (high rating spread, low
      volatility).

Everything is deterministic for a given seed set and pool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .agents import RuleChallenger, RuleWarden
from .match import run_match

AgentFactory = Callable[[], Any]
K_FACTOR = 24.0
INITIAL_ELO = 1200.0


def _expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


@dataclass
class LeagueReport:
    rounds: int
    seeds: list[int]
    warden_ratings: dict[str, float]
    challenger_ratings: dict[str, float]
    rating_history: list[dict[str, dict[str, float]]] = field(default_factory=list)
    win_matrix: dict[str, dict[str, float]] = field(default_factory=dict)  # warden -> challenger -> warden winrate
    rank_volatility: float = 0.0
    dynamic: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rounds": self.rounds, "seeds": self.seeds,
            "warden_ratings": {k: round(v, 1) for k, v in self.warden_ratings.items()},
            "challenger_ratings": {k: round(v, 1) for k, v in self.challenger_ratings.items()},
            "win_matrix": self.win_matrix,
            "rank_volatility": round(self.rank_volatility, 4),
            "dynamic": self.dynamic,
            "rating_history": self.rating_history,
        }


def default_pools() -> tuple[dict[str, AgentFactory], dict[str, AgentFactory]]:
    from .agents import PassiveWarden, IsolateAllWarden, DeclareAllWarden
    wardens = {
        "rule": lambda: RuleWarden(),
        "passive": lambda: PassiveWarden(),
        "isolate_all": lambda: IsolateAllWarden(),
        "declare_all": lambda: DeclareAllWarden(),
    }
    challengers = {
        "rule": lambda: RuleChallenger(),
    }
    return wardens, challengers


def extended_pools(q_learners: dict[str, Any] | None = None
                   ) -> tuple[dict[str, AgentFactory], dict[str, AgentFactory]]:
    """The default pools plus the v2 agents: AdaptiveWarden, any supplied
    trained Q-Wardens, and a second Challenger (StealthChallenger) so the
    league has more than one opponent and its dynamics mean something."""
    from .agents import AdaptiveWarden, StealthChallenger
    wardens, challengers = default_pools()
    wardens["adaptive"] = lambda: AdaptiveWarden()
    if q_learners:
        from .rl import QWarden
        for name, learner in q_learners.items():
            wardens[name] = (lambda L=learner: QWarden(L))
    challengers["stealth"] = lambda: StealthChallenger()
    return wardens, challengers


def _rank_order(ratings: dict[str, float]) -> list[str]:
    return [k for k, _ in sorted(ratings.items(), key=lambda kv: kv[1], reverse=True)]


def _rank_churn(prev: list[str], cur: list[str]) -> int:
    pos = {k: i for i, k in enumerate(prev)}
    return sum(1 for i, k in enumerate(cur) if pos.get(k, i) != i)


def run_league(
    scenario_id: str,
    seeds: list[int],
    rounds: int = 5,
    wardens: dict[str, AgentFactory] | None = None,
    challengers: dict[str, AgentFactory] | None = None,
) -> LeagueReport:
    if wardens is None or challengers is None:
        wardens, challengers = default_pools()

    w_elo = {name: INITIAL_ELO for name in wardens}
    c_elo = {name: INITIAL_ELO for name in challengers}
    wins = {wn: {cn: [0, 0] for cn in challengers} for wn in wardens}  # [warden_wins, games]
    history: list[dict[str, dict[str, float]]] = []
    churn_total = 0
    prev_rank = _rank_order(w_elo)

    for _ in range(rounds):
        for wn, wf in wardens.items():
            for cn, cf in challengers.items():
                for seed in seeds:
                    res = run_match(scenario_id, seed, cf(), wf())
                    warden_won = 1.0 if res.winner == "warden" else 0.0
                    # Elo update (warden vs challenger)
                    exp_w = _expected(w_elo[wn], c_elo[cn])
                    w_elo[wn] += K_FACTOR * (warden_won - exp_w)
                    c_elo[cn] += K_FACTOR * ((1.0 - warden_won) - (1.0 - exp_w))
                    wins[wn][cn][0] += int(warden_won)
                    wins[wn][cn][1] += 1
        history.append({"warden": dict(w_elo), "challenger": dict(c_elo)})
        cur_rank = _rank_order(w_elo)
        churn_total += _rank_churn(prev_rank, cur_rank)
        prev_rank = cur_rank

    win_matrix = {
        wn: {cn: round(v[0] / v[1], 4) if v[1] else 0.0 for cn, v in row.items()}
        for wn, row in wins.items()
    }
    # normalise churn to [0,1] per round per policy
    rank_volatility = churn_total / max(1, rounds * max(1, len(wardens)))
    spread = (max(w_elo.values()) - min(w_elo.values())) if w_elo else 0.0
    if rank_volatility > 0.5:
        dynamic = "cycling"
    elif spread > 250:
        dynamic = "dominance"
    else:
        dynamic = "stable"

    return LeagueReport(
        rounds=rounds, seeds=list(seeds), warden_ratings=w_elo, challenger_ratings=c_elo,
        rating_history=history, win_matrix=win_matrix,
        rank_volatility=rank_volatility, dynamic=dynamic,
    )
