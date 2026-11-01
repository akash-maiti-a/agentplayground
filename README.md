# ARENA — a sandboxed cyber-defense sparring range for AI agents

ARENA is a **fully abstract, research/education** simulation in which two AI
agents spar inside a fictional "cyber landscape":

* the **Challenger** — a *constrained challenge generator* — tries to reach an
  objective inside a synthetic network, and
* the **Warden** — a *detection-and-response system* — detects, contains and
  recovers, while keeping business services available above an SLA floor.

The point is to study **defensive resilience, agent behavior, and safe
evaluation methodology** — not to access real systems or produce anything
reusable for real intrusion.

This repository is the Phase-0/Phase-1 implementation of the project plan in
`~/.claude/plans/…peaceful-music.md`.

## The central safety property: the abstraction *is* the isolation

Agents never receive a shell, a socket, a real network stack, or the ability
to run code. A match is a **deterministic state machine**. Agents emit typed,
schema-validated *intents* (`RECON`, `ACCESS`, `ISOLATE`, …) and the engine
resolves them against *fictional* hosts whose "weaknesses" are abstract numeric
attributes in `[0, 1]`. There are **no real CVEs, payloads, protocols, or
procedures anywhere** — so no step of a match maps to a real-world action and
there is nothing that could "leak" into a real environment.

Infrastructure isolation (no-egress runners, etc.) is retained as
defense-in-depth, and the test suite enforces the core of it:

* **`tests/test_safety.py` statically asserts the package imports no
  networking or process-spawning modules** (`socket`, `urllib`, `subprocess`,
  …), and
* runs a full match with `socket` monkeypatched to fail closed.

The whole engine runs on the Python **standard library only** (zero runtime
dependencies), which is what makes that guarantee easy to audit.

## Install & run

```bash
pip install -e .            # editable install (no runtime deps; pytest for dev)

# a single match (rule-based Challenger vs Warden)
python -m arena run --scenario T1 --seed 4471

# write the append-only, hash-chained log and show key events
python -m arena run --scenario T1 --seed 4471 --log match.jsonl --verbose

# a batch across seeds, with per-metric mean/std and a version manifest
python -m arena batch --scenario T1 --seeds 0-49 --workers 4

# verify a saved log's hash chain and re-score it (post-hoc, from the log only)
python -m arena replay --log match.jsonl

# train a tabular-Q Warden and compare it to the rule baseline on held-out seeds
python -m arena train --scenario T1 --episodes 150 --train-seeds 0-29 --eval-seeds 100-119

# co-evolution league: Elo ratings + a stable/cycling/dominance verdict
python -m arena league --scenario T1 --seeds 0-7 --rounds 5

# the standard benchmark leaderboard (balanced vs degenerate baselines)
python -m arena benchmark --workers 4
```

## Architecture (maps 1:1 to the plan)

| Plan component | Module |
|---|---|
| Simulation engine (deterministic FSM) | `arena/engine.py` |
| Intent/observation/event schema + RoE | `arena/schema.py` |
| World model + canonical state hash | `arena/world.py` |
| Scenario generator (T0–T2) + invariants | `arena/scenario.py` |
| Agent interface + rule-based / scripted agents | `arena/agents/` |
| Match loop, AP economy, observability | `arena/match.py` |
| Append-only, content-hashed log | `arena/log.py` |
| Post-hoc, multi-objective scoring | `arena/scoring.py` |
| Evaluation harness (batch + variance, thread-parallel) | `arena/harness.py` |
| Control plane (emergency stop, limits) | `arena/control.py` |
| Deterministic RNG (named sub-streams) | `arena/rng.py` |
| RL env (Gym-shaped) + tabular-Q Warden | `arena/rl.py` |
| LLM-agent seam + offline deterministic backend | `arena/agents/llm.py` |
| Degenerate baselines (reward-hacking tripwires) | `arena/agents/baselines.py` |
| Co-evolution league (Elo + dynamics) | `arena/league.py` |
| Reproducible benchmark + leaderboard | `arena/benchmark.py` |

```
 challenger ─intents▶ ┌───────────────┐ ◀intents─ warden
 (sandboxed)          │ SIMULATION    │          (sandboxed)
      ▲  partial obs  │ ENGINE (pure, │  partial obs ▲
      └───────────────│ seeded FSM)   │──events──────┘
                      └──────┬────────┘        │
        scenario ───────────┘                  ▼
        generator                       append-only log ──▶ post-hoc scoring
                                               ▲                    │
             control plane (stop / limits) ────┘            evaluation harness
```

## Scenarios

* **T0 — tutorial:** tiny, highly observable, soft perimeter, little noise.
* **T1 — segmented:** perimeter / commons / core, moderate telemetry + noise.
* **T2 — noisy enterprise:** full topology, uneven telemetry, heavy green noise.
* **T3 — stealth & deception:** low telemetry, heavy noise, reduced global
  sensor coverage (partial outages) — the Warden must lean on deception
  (decoys) and careful investigation rather than raw signal.
* **T4 — co-evolution league:** not a single world but a *distribution* — see
  `arena/league.py`, which plays populations of policies over repeated rounds.

Each is a *seeded generator*; `scenario_id` fixes the distribution and `seed`
fixes the draw. Every generated world is validated (objective reachable, SLA
satisfiable, all nodes connected) before play.

## Agents: rule-based, learned (RL), and LLM

All three paradigms share the one narrow intent API (RQ4):

* **Rule-based** (`agents/challenger.py`, `agents/warden.py`) — transparent
  priority ladders; the interpretable baselines.
* **RL** (`arena/rl.py`) — a Gymnasium-*shaped* `WardenEnv` (`reset`/`step`,
  a fixed 6-way macro-action space, a 6-dim feature vector) with a tabular
  Q-learner and a match-loop-compatible `QWarden`. Trains against a fixed
  Challenger and generalises to held-out seeds.
* **LLM** (`agents/llm.py`) — reasons in natural language over the same API via
  an **injected backend** (`complete(prompt) -> str`). The only in-repo backend
  is fully offline and deterministic, so the whole prompt→parse→validate path
  is testable in the sandbox; a real self-hosted model is supplied by the
  caller and lives outside this package (which is why the package still imports
  nothing that can reach the network). Malformed replies fall back to a no-op
  and are counted — nothing invalid ever reaches the engine.

> **Training reward vs. evaluation score.** The RL env's per-tick reward uses
> ground truth (the env *is* the simulator) — that is a training signal only.
> The **evaluation** benchmark always uses the hidden, post-hoc, multi-objective
> score in `arena/scoring.py`.

## Scoring (reward-hacking resistant by design)

Scoring is **multi-objective, post-hoc, and ground-truth-referenced**. It
reports a *vector* per side (mission-success, MTTD/MTTC, classification F1,
availability, containment quality, false-alarm rate, recovery / objective-
progress, stealth, efficiency) plus integrity axes (rule compliance and
**explanation faithfulness** — the calibration of the Warden's stated beliefs
against reconstructed ground truth, scored by Brier). The scalar "win" is only
a transparent weighting of that vector and is **never fed back to agents
mid-match**.

The largest axis, **mission-success**, is SLA-gated: a defender earns it only
by protecting the objective *while keeping services available*. This is what
makes the scalar reward-hacking resistant, and it is enforced as a test
(`tests/test_reward_hacking.py`): the balanced rule Warden must out-score every
degenerate baseline (`passive`, `isolate_all`, `declare_all`). If a scoring
change ever lets a degenerate strategy win, that test fails — the "adversarial
scoring review" tripwire from the plan. Current leaderboard
(`python -m arena benchmark`, T1–T3 × 20 seeds, v0.2, with bootstrap 95% CIs):

```
adaptive     scalar≈0.89 [0.85,0.93]  winrate≈0.85  f1≈0.99  avail≈0.94  faith≈0.98
rule         scalar≈0.78 [0.72,0.84]  winrate≈0.82  f1≈0.47  avail≈0.95  faith≈0.93
declare_all  scalar≈0.51 [0.46,0.57]  winrate≈0.32  f1≈0.71  avail≈1.00  faith≈0.13
isolate_all  scalar≈0.35 [0.34,0.37]  winrate≈1.00  f1≈0.03  avail≈0.46  faith≈1.00
passive      scalar≈0.34 [0.29,0.39]  winrate≈0.32  f1≈0.00  avail≈1.00  faith≈1.00
```

## v0.2 experiment & results site

`python -m arena export --out site/public/data/results.json` runs the full
v1-vs-v2 experiment (7 Wardens × 2 Challengers × tiers T0–T3 × 50 held-out
seeds, a co-evolution league, RL learning curves and an ablation, and a replay
of one match) into one JSON file. `site/` is a Vite + React page that renders
it; `.github/workflows/pages.yml` deploys it to GitHub Pages
(`cd site && npm ci && npm run dev` to view locally).

What changed in v0.2, and why:

* **Cross-process determinism bug (fixed).** `match.py` iterated a `set` of
  node ids when building observations, so results depended on
  `PYTHONHASHSEED`. The v0.1 RL experiment swung from 15% to 90% Warden win
  rate on hash seed alone. Now sorted, and `tests/test_improvements.py` checks
  identical output across three hash seeds in separate processes.
* **Engine/scorer ground-truth fix.** Declaring a host *after* isolating it
  was judged wrong (the engine only counted active footholds), and
  containment before the verdict scored as "never contained". Nodes now carry
  `contained_foothold`, and MTTC is `max(0, …)`.
* **`AdaptiveWarden`** (`agents/warden.py`): fidelity-aware decision
  threshold, alert triage, idle-AP threat hunting, calibrated claims.
  Optional decoys measured no effect and are off by default.
* **`StealthChallenger`** (`agents/challenger.py`): lies low after loud moves;
  drops the rule Warden's win rate sharply.
* **RL v2** (`rl.py`, `version="v2"`): target-aware macro decoding,
  calibrated features, epsilon decay, train/eval opponent parity, learning
  curves.
* **Benchmark CIs** (`benchmark.bootstrap_ci`) and `league.extended_pools()`.
  Caveat surfaced by the league: Elo is win/loss only, so `isolate_all` rates
  highest there. Rank agents by the multi-objective score, not win-only Elo.

## Tests

```bash
python -m pytest -q
```

Covers: the **determinism gate** (identical per-tick state-hash transcripts
across runs for T0/T1/T2 × many seeds), **zero-egress** (static + runtime),
**emergency stop**, **schema fuzzing** (malformed intents rejected, not
executed), **scenario invariants**, and **log tamper-evidence**.

## Status & roadmap

Implemented end-to-end:

* **Phase 0** — foundations, safety harness, determinism gate.
* **Phase 1** — single-match MVP: rule-based agents, scenario generator (T0–T2),
  post-hoc scoring, batch harness.
* **Phase 2** — RL env + tabular-Q Warden and an LLM-agent seam behind the same
  intent API; T3 scenarios; real explanation-faithfulness scoring.
* **Phase 3** — co-evolution league (Elo + stable/cycling/dominance verdict) and
  thread-parallel batch orchestration (process/cluster parallelism is left to an
  external orchestrator to keep the package import-clean).
* **Phase 4** — reward-hacking hardening (mission-success axis + degenerate-
  baseline tripwire tests) and a reproducible benchmark + leaderboard; CI runs
  the determinism and zero-egress gates on every push.

Where real frontier models would plug in: supply an `LLMAgent` backend that
talks to a **self-hosted model inside the isolation boundary**. See `SAFETY.md`
for the safety-requirements checklist.
