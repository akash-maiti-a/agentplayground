# Safety requirements checklist

ARENA is a fully abstract research/education simulator. Its safety rests first
on design (nothing in a match maps to a real-world action) and second on
defense-in-depth. This checklist tracks the requirements from the project plan
and where each is enforced.

| # | Requirement | Status | Where enforced |
|---|---|---|---|
| 1 | **No code execution by agents** — only schema-validated abstract intents; engine owns all outcomes | ✅ | `arena/schema.py` (`validate_intent`), `arena/engine.py` |
| 2 | **No real-world mapping** — no real CVEs, payloads, protocols, or reusable procedures anywhere | ✅ | abstract verbs + numeric postures only; no network/system calls |
| 3 | **Synthetic everything** — hosts, users, services, traffic generated per seed | ✅ | `arena/scenario.py`, green noise in `arena/engine.py` |
| 4 | **Zero egress, verified** — package imports no networking/process modules; a match runs with sockets failing closed | ✅ | `tests/test_safety.py` (static import scan + runtime monkeypatch) |
| 5 | **Isolated infrastructure** — air-gapped / no-egress runners (defense-in-depth) | ☐ deploy-time | documented; enforce in the runner image/network policy when deployed |
| 6 | **Resource limits** — tick cap, wall-clock cap, node-count cap, AP budgets | ✅ | `arena/control.py`, `arena/match.py` |
| 7 | **Full event logging** — append-only, content-hashed, single source of truth | ✅ | `arena/log.py` (hash chain + `verify_chain`) |
| 8 | **Deterministic replay** — pure seeded engine; identical state-hash transcript | ✅ | `arena/rng.py`, `arena/world.py`, `tests/test_determinism.py` |
| 9 | **Reset mechanism** — clean, identical world from `(scenario, seed)` | ✅ | `arena/scenario.py`, `tests/test_safety.py` |
| 10 | **Human emergency stop** — one switch halts the match and records the reason | ✅ | `arena/control.py` (`emergency_stop`), `tests/test_safety.py` |
| 11 | **Scope guard** — adding realism (real protocols/CVEs/executable components) requires governance sign-off | ☐ process | policy; keep `FORBIDDEN_IMPORTS` in the safety test as a tripwire |
| 12 | **Incident register** — safety anomalies logged, triaged, fixed, regression-tested | ☐ process | add a regression test here for every safety-class incident |
| 13 | **Adversarial scoring review** — degenerate strategies must not win the scalar | ✅ | `tests/test_reward_hacking.py` (rule Warden must beat `passive`/`isolate_all`/`declare_all`) |
| 14 | **LLM backend stays inside the boundary** — the package ships only an offline backend; real models are injected and self-hosted | ✅ (in-repo) / ☐ (deploy) | `arena/agents/llm.py` (injected `Backend`); static import scan still green |
| 15 | **Parallelism without new processes/egress** — batch parallelism uses threads only | ✅ | `arena/harness.py` (`ThreadPoolExecutor`); `multiprocessing`/`subprocess` remain in `FORBIDDEN_IMPORTS` |

Legend: ✅ enforced in code/tests · ☐ deploy-time or process control (documented, to be owned operationally).

## If you extend ARENA

* Keep the engine dependency-free and free of the modules listed in
  `FORBIDDEN_IMPORTS` (`tests/test_safety.py`). That static check is the
  tripwire for scope creep toward real-world capability.
* Every new intent verb must be an **abstract category** resolved by the
  engine's probability tables — never a description of how a real technique
  works.
* Any new safety-relevant behavior gets a test in `tests/test_safety.py`.
