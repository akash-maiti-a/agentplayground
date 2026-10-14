# Agent Playground Runtime Guide

_As of 2026-10-14_

This repo holds two independent runtime environments: **ARENA**, a pure-Python
abstract cyber-defense simulator run from the CLI, and
**docker-desktop-agents**, a Docker Compose lab of real computer-use agents on
virtual desktops. They don't share a safety boundary or a startup path — this
guide covers both end to end.

## Overview

| | ARENA | docker-desktop-agents |
| --- | --- | --- |
| What it is | Deterministic, abstract cyber-defense simulation (Challenger vs. Warden) | Four Dockerized Linux desktops running real local-LLM computer-use agents |
| Runtime | Python stdlib CLI (`python -m arena ...`), zero runtime deps | Docker Compose, Ollama models baked into images |
| Network/shell access | None — no sockets, no subprocess, pure state machine | Real — shell (`run_shell`), SSH, nmap, GUI control via xdotool |
| Safety boundary | The abstraction itself (see `SAFETY.md`) | Isolation via Docker networks only — treat containers as untrusted |
| Where it lives | `arena/` (package), entry point `arena.cli:main` | `docker-desktop-agents/` |

They do not share a startup path, a network, or a safety model — run and
observe each independently using the sections below.

## Prerequisites

**For ARENA:**
- Python 3.11+
- No runtime dependencies; `pytest>=7` for running the test suite

**For docker-desktop-agents:**
- Docker Desktop with Compose v2 (confirm with `docker compose version`)
- Enough RAM/CPU headroom: four containers x two ~7B Ollama models each —
  budget roughly 12 cores / 24 GB free for the whole stack at default
  settings (`AGENT_CPUS=3.0`, `AGENT_MEM_LIMIT=8g` per container)
- No GPU required; no cloud API key; no internet access needed once images
  are built — models are baked in at build time
- A slow first build: two ~7B models get pulled during `docker compose
  build` (shared cache layer across all four images, so each unique model
  name downloads once) — can take up to a couple of hours on a slow
  connection

## ARENA: install, run, and observe

### Install (editable, dev extras for tests)

```bash
pip install -e ".[dev]"
```

(Already installed in this environment as `arena-cyber-range` 0.1.0.)

### Run a single match

```bash
python -m arena run --scenario T1 --seed 4471
```

Prints the match outcome (`winner`, `reason`, `ticks`, final availability)
and the full post-hoc score card as JSON. Add `--log match.jsonl` to write
the append-only, hash-chained event log, and `--verbose` to also print every
`resolution`/`alert`/`match_end` event inline as the match plays out — this
is the fastest way to *watch* a match happen in real time from the terminal.

### Replay and verify a saved log

```bash
python -m arena replay --log match.jsonl
```

Verifies the hash chain (`chain_valid`) and re-derives the score card purely
from the log — useful for confirming a run wasn't tampered with after the
fact.

### Batch runs across seeds

```bash
python -m arena batch --scenario T1 --seeds 0-49 --workers 4
```

Runs many seeds in parallel (thread-based), reporting win rates and
per-metric mean/std plus a version manifest. Add `--out report.json` to
persist the full report.

### Train an RL Warden

```bash
python -m arena train --scenario T1 --episodes 150 --train-seeds 0-29 --eval-seeds 100-119
```

Trains a tabular-Q Warden and compares its held-out win rate / availability
against the rule-based baseline.

### Co-evolution league

```bash
python -m arena league --scenario T1 --seeds 0-7 --rounds 5
```

Plays populations of policies across rounds and reports Elo ratings plus a
stable/cycling/dominance verdict.

### Standard benchmark leaderboard

```bash
python -m arena benchmark --workers 4
```

Runs the reproducible benchmark suite (balanced rule agents vs. degenerate
baselines: `passive`, `isolate_all`, `declare_all`) and prints the ranking.

### Observing a match

There is no live dashboard for ARENA — it is a CLI-only, deterministic
simulation. Observability comes from three places:

1. **`--verbose` on `run`** — streams key events (resolutions, alerts, match
   end) to stdout as the match plays.
2. **The JSONL log** (`--log match.jsonl`) — a full append-only,
   content-hash-chained record of every event; replay and verify it any time
   with `arena replay`.
3. **The score card** — printed after every `run`/`replay`/`batch`, a
   multi-objective vector (mission-success, MTTD/MTTC, F1, availability,
   containment quality, false-alarm rate, stealth, efficiency, explanation
   faithfulness) rather than a single win/loss.

### Tests (safety + determinism gates)

```bash
python -m pytest -q
```

Covers the determinism gate (identical per-tick state hashes across repeated
runs), the zero-egress gate (static import check + a runtime
`socket`-monkeypatch test), emergency stop, schema fuzzing, scenario
invariants, log tamper-evidence, and the reward-hacking tripwire (the
balanced rule Warden must out-score every degenerate baseline).

## docker-desktop-agents: startup

### 1. Configure environment

```bash
cd docker-desktop-agents
cp .env.example .env
```

Defaults work out of the box (adversarial recon task, `llava`/`qwen2.5`
models, seeded weak SSH credential `labuser`/`hunter2`, `AGENT_CPUS=3.0`,
`AGENT_MEM_LIMIT=8g`). Edit `.env` first if you want to:
- swap models (`COMPUTERx_AGENTy_VISION_MODEL` / `_TEXT_MODEL`) to compare
  different local LLMs
- switch to a neutral, non-adversarial task (`SHARED_TASK=...`)
- raise/lower `AGENT_CPUS` / `AGENT_MEM_LIMIT` if agents look starved or
  your machine is tight
- change `MAX_TURNS`, `WALL_CLOCK_LIMIT_SECONDS`, `SHELL_TIMEOUT_SECONDS` to
  bound how long a run can go

### 2. Build and start

```bash
docker compose up --build
```

- **First build is slow**: two ~7B Ollama models get pulled into the image
  per unique model name (shared build-cache layer, so a given model name is
  only downloaded once across all four containers) — on a slow connection
  this can take a couple of hours.
- **Subsequent builds** (code-only changes to `agent/agent.py`, etc.) are
  seconds, reusing the cached model layer.
- Changing a model name in `.env` requires a rebuild (`docker compose
  build`) since models are pulled at build time, not runtime.

This brings up six containers: `computer1-agent1`, `computer1-agent2`,
`computer2-agent1`, `computer2-agent2` (the four desktop/agent containers),
`viewer` (nginx link page on :8080), and `logs` (Dozzle on :9999).

### 3. Confirm it's up

```bash
docker compose ps
```

All six services should show `running`/`healthy`. Then open
**http://localhost:8080** for the full link list to every desktop, live
view, and Dozzle.

## docker-desktop-agents: observing the lab

| Tool | URL | What it shows |
| --- | --- | --- |
| Link hub | http://localhost:8080 | Nginx page linking every desktop, live view, and Dozzle |
| Dozzle (aggregated logs) | http://localhost:9999 | Live, searchable logs from all 5 containers in one tab, including each agent's own reasoning (`agent.py` logs to stdout) |
| Live view (no VNC needed) | http://localhost:7901–7904 | Per-agent: latest screenshot, the vision model's caption, the text model's raw reply, the parsed action, and shell output — refreshes every 2s |
| noVNC desktop | http://localhost:6901–6904?password=$VNC_PASSWORD | The actual Linux desktop — watch, or take over manually |

Agent-to-port mapping: `computer1-agent1` = 6901/7901, `computer1-agent2` =
6902/7902, `computer2-agent1` = 6903/7903, `computer2-agent2` = 6904/7904.

### Scripting against it directly

```bash
docker exec <container-name> cat /opt/agent/public/status.json
```

Returns the same structured data (screenshot caption, model reply, parsed
action, shell output) that the live-view page renders — useful for polling
or logging outside a browser.

### Tailing one agent's logs from the CLI

```bash
docker compose logs -f computer1-agent1
```

### What "done" looks like

Each agent idles (desktop stays viewable, no more actions) once it hits
`MAX_TURNS`, `WALL_CLOCK_LIMIT_SECONDS`, calls `done`, or Ollama fails to
come up (e.g. a model failed to pull at build time) — check Dozzle or the
live view for which of these happened.

## Stopping and cleanup

**ARENA** is stateless per invocation — nothing to stop or clean up beyond
deleting any `.jsonl` logs or `report.json` files you asked it to write.

**docker-desktop-agents:**

```bash
# stop the stack, keep containers/images for next time
docker compose stop

# stop and remove containers (keeps built images + the Ollama model layer cache)
docker compose down

# also remove the built images (forces a full model re-pull on next `up --build`)
docker compose down --rmi all
```

Prefer `docker compose down` (not `--rmi all`) between sessions if you want
to avoid re-pulling the ~7B models next time — the image layer cache is what
keeps rebuilds fast.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `python -m arena` fails with `ModuleNotFoundError: arena` | Package not installed | `pip install -e .` from the repo root |
| `pytest` fails on the zero-egress test | A new import added a networking/process-spawning module | Check `tests/test_safety.py` for which import triggered it; remove the dependency or move it behind the LLM-agent seam |
| Agent idles immediately, desktop still loads | Ollama failed to come up (model failed to pull at build time) | Check Dozzle (`http://localhost:9999`) for that container's `ollama serve` logs; rebuild with `docker compose build <service>` |
| `docker compose up --build` hangs for a very long time | First-time model pull (two ~7B models per unique name) | Expected on first build; subsequent builds are fast. Confirm progress in the build output, not silence |
| Agents never reach each other | `lab-net` missing or a firewall blocking Docker's internal bridge | Confirm `docker network inspect docker-desktop-agents_lab-net` lists all four agent containers |
| Live view / noVNC pages time out | Container not yet healthy, or model still loading | `docker compose ps` to check status; give it another minute after `up` completes — Ollama model load can lag container start |
| Comparison feels unfair between models | A fairness knob was changed per-agent instead of globally | Re-check `.env`: `AGENT_CPUS`, `AGENT_MEM_LIMIT`, `OLLAMA_TEMPERATURE/SEED/NUM_PREDICT`, `MAX_TURNS`, `TURN_DELAY_SECONDS` should be identical across all four agents unless that's the deliberate variable |
| Want to inspect a single run after the fact | — | ARENA: `python -m arena replay --log match.jsonl`. docker-desktop-agents: `docker exec <container> cat /opt/agent/public/status.json` or `docker compose logs <container>` |
| `error during connect: Get "http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/...": open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.` | Docker Desktop's background engine isn't running — the named pipe the CLI talks to doesn't exist yet. This happens when the Docker Desktop **app** (tray icon) isn't open, is still starting up, or crashed/was reset, even though the `docker`/`docker compose` CLI is installed. | Start the Docker Desktop app (Start menu → "Docker Desktop", or `Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"`) and wait for its tray icon to show the engine as running (whale icon steady, not animating) before retrying `docker compose up`. If it won't start: check `sc query com.docker.service` for the service state, try "Restart" from the Docker Desktop tray menu, or reboot if the service is stuck. |
