"""Results export — one JSON document with everything the results site shows.

``python -m arena export --out site/public/data/results.json`` runs the full
experiment (baseline v1 agents vs the improved v2 agents, against two
Challengers, across tiers T0–T3 on held-out seeds), plus a co-evolution league,
RL learning curves and an ablation, and a sample match timeline for replay.

Everything is derived from (seeds, code version) only, so re-running the export
reproduces the same file byte-for-byte except ``generated_at``.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import __version__
from .agents import (
    AdaptiveWarden, DeclareAllWarden, IsolateAllWarden, PassiveWarden,
    RuleChallenger, RuleWarden, StealthChallenger,
)
from .benchmark import BenchmarkEntry, bootstrap_ci, run_benchmark
from .league import extended_pools, run_league
from .match import run_match
from .rl import QWarden, train_warden
from .scenario import generate_world
from .scoring import score_match

TIERS = ("T0", "T1", "T2", "T3")
TRAIN_SEEDS = list(range(0, 30))

# Warden roster: (key, label, generation, description)
WARDENS = [
    ("rule", "Rule Warden", "v1", "Original priority-ladder baseline (fixed 0.6 confidence bar)."),
    ("adaptive", "Adaptive Warden", "v2", "Fidelity-aware threshold, alert triage, idle-AP threat hunting, calibrated claims."),
    ("q_v1", "Q-Warden v1", "v1", "Tabular Q-learning, original 6 features and top-suspect decoder."),
    ("q_v2", "Q-Warden v2", "v2", "Tabular Q-learning, target-aware decoder, calibrated features, epsilon decay."),
    ("passive", "Passive", "baseline", "Degenerate: never acts."),
    ("isolate_all", "Isolate-all", "baseline", "Degenerate: isolates everything."),
    ("declare_all", "Declare-all", "baseline", "Degenerate: declares everything compromised."),
]
CHALLENGERS = [
    ("rule", "Rule Challenger", "Original priority-ladder challenge generator."),
    ("stealth", "Stealth Challenger", "Low-and-slow: lies low after loud moves so suspicion decays."),
]


def _rolling(xs: list[float], k: int) -> list[float]:
    out, acc = [], 0.0
    for i, x in enumerate(xs):
        acc += x
        if i >= k:
            acc -= xs[i - k]
        out.append(round(acc / min(i + 1, k), 4))
    return out


def train_learners(episodes: int = 800) -> dict[str, Any]:
    """Train v1 and v2 Q-Wardens on a mixed-tier curriculum (same budget)."""
    learners = {}
    for ver in ("v1", "v2"):
        learner = None
        per_tier = max(1, episodes // len(TIERS))
        # interleave tiers in blocks so one learner sees every tier
        for block in range(4):
            # v1: constant epsilon (original recipe); v2: decays 0.25 -> 0.02
            start = 0.25 if ver == "v1" else max(0.02, 0.25 - 0.0575 * block)
            end = 0.25 if ver == "v1" else max(0.02, 0.25 - 0.0575 * (block + 1))
            for tier in TIERS:
                learner = train_warden(tier, episodes=per_tier // 4, seeds=TRAIN_SEEDS,
                                       learner=learner, epsilon=start, version=ver,
                                       epsilon_min=end)
        learners[ver] = learner
    return learners


def _warden_factories(learners) -> dict[str, Any]:
    return {
        "rule": lambda: RuleWarden(),
        "adaptive": lambda: AdaptiveWarden(),
        "q_v1": lambda: QWarden(learners["v1"]),
        "q_v2": lambda: QWarden(learners["v2"]),
        "passive": lambda: PassiveWarden(),
        "isolate_all": lambda: IsolateAllWarden(),
        "declare_all": lambda: DeclareAllWarden(),
    }


_CHALLENGER_FACTORIES = {"rule": lambda: RuleChallenger(), "stealth": lambda: StealthChallenger()}

_AXES = ("mission_success", "mttd_score", "mttc_score", "classification_f1", "availability",
         "containment_quality", "false_alarm_score")


def run_leaderboard(learners, seeds: list[int], workers: int) -> dict[str, Any]:
    from .harness import run_batch
    wf = _warden_factories(learners)
    rows = []
    for wkey, *_ in WARDENS:
        for ckey, *_ in CHALLENGERS:
            scal, wins, per_tier, axes = [], [], {}, {a: [] for a in _AXES}
            faith = []
            for tier in TIERS:
                rep = run_batch(tier, seeds, _CHALLENGER_FACTORIES[ckey], wf[wkey], workers=workers)
                ts = [c.warden_scalar for c in rep.cards]
                tw = [1.0 if c.winner == "warden" else 0.0 for c in rep.cards]
                scal += ts
                wins += tw
                for a in _AXES:
                    axes[a] += [c.warden.get(a, 0.0) for c in rep.cards]
                faith += [c.integrity.get("explanation_faithfulness", 1.0) for c in rep.cards]
                per_tier[tier] = {
                    "scalar": round(sum(ts) / len(ts), 4),
                    "scalar_ci95": bootstrap_ci(ts),
                    "winrate": round(sum(tw) / len(tw), 4),
                    "f1": rep.aggregates["warden.classification_f1"]["mean"],
                    "availability": rep.aggregates["warden.availability"]["mean"],
                    "challenger_scalar": rep.aggregates["challenger.scalar"]["mean"],
                }
            n = len(scal)
            rows.append({
                "warden": wkey, "challenger": ckey, "n": n,
                "scalar": round(sum(scal) / n, 4), "scalar_ci95": bootstrap_ci(scal),
                "winrate": round(sum(wins) / n, 4), "winrate_ci95": bootstrap_ci(wins),
                "faithfulness": round(sum(faith) / n, 4),
                "axes": {a: round(sum(v) / n, 4) for a, v in axes.items()},
                "per_tier": per_tier,
            })
    return {"rows": rows}


def run_rl_ablation(tiers=("T2", "T3"), eval_seeds=None) -> list[dict[str, Any]]:
    eval_seeds = eval_seeds or list(range(100, 140))
    out = []
    for tier in tiers:
        for ver in ("v1", "v2"):
            for eps in (150, 400):
                L = train_warden(tier, episodes=eps, seeds=TRAIN_SEEDS, epsilon=0.25, version=ver)
                r = run_benchmark(BenchmarkEntry(f"{ver}-{eps}", RuleChallenger, lambda: QWarden(L)),
                                  {tier: eval_seeds})
                o = r.overall
                out.append({"tier": tier, "version": ver, "episodes": eps,
                            "winrate": o["warden_winrate"], "scalar": o["warden_scalar"],
                            "f1": o["classification_f1"], "availability": o["availability"]})
    return out


def _action_mix(seeds: list[int], tier: str = "T2") -> dict[str, dict[str, float]]:
    out = {}
    for key, make in (("rule", RuleWarden), ("adaptive", AdaptiveWarden)):
        c: Counter = Counter()
        for s in seeds:
            res = run_match(tier, s, RuleChallenger(), make())
            c.update(e["verb"] for e in res.log
                     if e.get("type") == "intent_accepted" and e.get("side") == "warden")
        tot = sum(c.values()) or 1
        out[key] = {v: round(n / tot, 4) for v, n in sorted(c.items())}
    return out


def _timeline(tier: str, seed: int, warden) -> dict[str, Any]:
    res = run_match(tier, seed, RuleChallenger(), warden)
    card = score_match(res.log)
    state: dict[str, str] = {}
    admin: set[str] = set()
    ticks: dict[int, dict[str, Any]] = {}

    def T(t):
        return ticks.setdefault(t, {"t": t, "c": None, "w": None, "alerts": 0, "greens": 0})

    for e in res.log:
        typ, t = e.get("type"), e.get("tick")
        if t is None:
            continue
        if typ == "resolution":
            d = e.get("detail", {})
            node = d.get("node") or d.get("to") or d.get("zone")
            side = "c" if e["side"] == "challenger" else "w"
            if not (e["verb"] == "RESTORE" and d.get("completed")):
                T(t)[side] = {"verb": e["verb"], "target": node, "ok": bool(e.get("success"))}
            if e.get("success"):
                v = e["verb"]
                if v == "ACCESS":
                    state[d["node"]] = "foothold"
                elif v == "TRAVERSE" and not d.get("decoy"):
                    state[d["to"]] = "foothold"
                elif v == "ESCALATE":
                    admin.add(d["node"])
                elif v == "ISOLATE":
                    state[d["node"]] = "isolated"
                elif v == "RESTORE":
                    if d.get("completed"):
                        state.pop(d["node"], None)
                        admin.discard(d["node"])
                    else:
                        state[d["node"]] = "restoring"
                elif v == "DECEIVE":
                    state[d["decoy"]] = "decoy"
        elif typ == "alert":
            T(t)["alerts"] += 1
        elif typ == "green":
            T(t)["greens"] += 1
        elif typ == "state_hash":
            row = T(t)
            row["avail"] = e.get("availability", 1.0)
            row["nodes"] = {n: (s + "+admin" if n in admin and s == "foothold" else s)
                            for n, s in sorted(state.items())}
    return {
        "warden": warden.__class__.__name__, "winner": res.winner, "reason": res.reason,
        "ticks": res.ticks, "scalar": card.warden_scalar,
        "f1": card.warden["classification_f1"],
        "ever_compromised": card.notes["ever_compromised"],
        "declared": card.notes["declared_compromised"],
        "timeline": [ticks[k] for k in sorted(ticks)],
    }


def sample_replay(seeds: list[int], tier: str = "T2") -> dict[str, Any]:
    """Pick the first held-out seed where v1 loses and v2 wins, and replay both."""
    chosen = seeds[0]
    for s in seeds:
        if run_match(tier, s, RuleChallenger(), RuleWarden()).winner != "warden" and \
                run_match(tier, s, RuleChallenger(), AdaptiveWarden()).winner == "warden":
            chosen = s
            break
    w = generate_world(tier, chosen)
    topo = {
        "objective": w.objective_node, "zones": list(w.zones),
        "nodes": [{"id": n.id, "zone": n.zone, "role": n.role, "value": n.value,
                   "business": any(s.business_critical for s in n.services.values())}
                  for n in sorted(w.nodes.values(), key=lambda n: n.id)],
        "edges": sorted({tuple(sorted((a, b))) for a, nb in w.adjacency.items() for b in nb}),
    }
    return {"tier": tier, "seed": chosen, "topology": topo,
            "runs": {"rule": _timeline(tier, chosen, RuleWarden()),
                     "adaptive": _timeline(tier, chosen, AdaptiveWarden())}}


def build_results(*, eval_seeds: list[int], workers: int = 4, episodes: int = 800,
                  league_seeds: list[int] | None = None, league_rounds: int = 5,
                  generated_at: str | None = None, quick: bool = False) -> dict[str, Any]:
    learners = train_learners(episodes)
    curves = {}
    for ver, L in learners.items():
        rets = [r for r, _ in L.curve]
        wins = [1.0 if w else 0.0 for _, w in L.curve]
        curves[ver] = {"return": _rolling(rets, 40)[::4], "winrate": _rolling(wins, 40)[::4],
                       "episodes": len(L.curve), "states": len(L.q), "stride": 4}

    league_seeds = league_seeds or list(range(0, 8))
    wp, cp = extended_pools({"q_v1": learners["v1"], "q_v2": learners["v2"]})
    league = run_league("T2", league_seeds, rounds=league_rounds, wardens=wp, challengers=cp).to_dict()

    return {
        "manifest": {
            "arena_version": __version__, "generated_at": generated_at,
            "tiers": list(TIERS), "eval_seeds": [min(eval_seeds), max(eval_seeds), len(eval_seeds)],
            "train_seeds": [min(TRAIN_SEEDS), max(TRAIN_SEEDS), len(TRAIN_SEEDS)],
            "rl_episodes": episodes, "league": {"tier": "T2", "seeds": len(league_seeds),
                                                "rounds": league_rounds},
            "command": "python -m arena export --out site/public/data/results.json",
        },
        "wardens": [{"key": k, "label": l, "generation": g, "description": d} for k, l, g, d in WARDENS],
        "challengers": [{"key": k, "label": l, "description": d} for k, l, d in CHALLENGERS],
        "leaderboard": run_leaderboard(learners, eval_seeds, workers),
        "league": league,
        "rl": {"curves": curves,
               "ablation": [] if quick else run_rl_ablation(eval_seeds=eval_seeds[:40])},
        "action_mix": _action_mix(eval_seeds[:20]),
        "replay": sample_replay(eval_seeds),
    }
