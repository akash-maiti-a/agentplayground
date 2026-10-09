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
python -m arena batch --scenario T1 --seeds 0-49

# verify a saved log's hash chain and re-score it (post-hoc, from the log only)
python -m arena replay --log match.jsonl
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
| Evaluation harness (batch + variance) | `arena/harness.py` |
| Control plane (emergency stop, limits) | `arena/control.py` |
| Deterministic RNG (named sub-streams) | `arena/rng.py` |

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

Each is a *seeded generator*; `scenario_id` fixes the distribution and `seed`
fixes the draw. Every generated world is validated (objective reachable, SLA
satisfiable, all nodes connected) before play.

## Scoring (reward-hacking resistant by design)

Scoring is **multi-objective, post-hoc, and ground-truth-referenced**. It
reports a *vector* per side (MTTD/MTTC, classification F1, availability,
containment quality, false-alarm rate, recovery / objective-progress, stealth,
efficiency). The scalar "win" is only a transparent weighting of that vector
and is **never fed back to agents mid-match** — so, e.g., a Warden that isolates
everything is immediately punished on the availability and false-alarm axes and
cannot dominate the scalar.

## Tests

```bash
python -m pytest -q
```

Covers: the **determinism gate** (identical per-tick state-hash transcripts
across runs for T0/T1/T2 × many seeds), **zero-egress** (static + runtime),
**emergency stop**, **schema fuzzing** (malformed intents rejected, not
executed), **scenario invariants**, and **log tamper-evidence**.

## Status & roadmap

Implemented: Phase 0 (foundations, safety harness, determinism gate) and the
Phase 1 single-match MVP with rule-based agents, scenario generator, post-hoc
scoring and a batch harness. Next (per the plan): RL/LLM agents behind the same
intent API (Phase 2), the co-evolution league and parallel orchestration
(Phase 3), and reward-hacking hardening + a public abstract-only benchmark
(Phase 4). See `SAFETY.md` for the safety-requirements checklist.
