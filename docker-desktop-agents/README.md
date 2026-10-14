# docker-desktop-agents

Two Dockerized "computers," two local-LLM computer-use agents on each,
every desktop viewable live in a browser via noVNC. **Fully offline** —
no cloud API, no API key, nothing leaves the container at runtime.

**This is a separate, intentionally more open-scope component from the rest
of this repo.** `arena/` (the root README/SAFETY.md) is a shell-less,
socket-less abstract simulation — that is its core safety property. The
agents here are the opposite: real computer-use agents that take real
screenshots and drive a real mouse/keyboard on a real Linux desktop inside
their container. Treat this directory's containers as untrusted, isolated
environments, not as part of ARENA's safety boundary.

## How it works

Each of the four agent containers is self-contained:

- a full Ubuntu desktop (Xvfb + LXDE + x11vnc + noVNC, from
  `dorowu/ubuntu-desktop-lxde-vnc`),
- a local **Ollama** server (`agent/Dockerfile` installs it and pulls the
  vision model *at build time*, so the model's weights are baked into the
  image — the container needs zero network access once it's running), and
- `agent/agent.py`: a small loop that screenshots the desktop (ImageMagick),
  asks the local model what to do next, and executes the answer with
  `xdotool`.

Local vision models don't have Anthropic's built-in "computer use" tool, so
the loop prompts the model to reply with **exactly one JSON action object**
per turn (`left_click`, `type`, `key`, `scroll`, `done`, …), parses it, and
runs it. See the `SYSTEM_PROMPT` in `agent/agent.py` for the exact contract.

## Layout

- `agent/` — the one Docker image (Dockerfile, `agent.py`,
  `supervisor-agent.conf` which runs `ollama serve` + the agent loop as two
  supervisord-managed processes alongside the base image's own Xvfb/VNC).
- `docker-compose.yml` — four containers built from that image:
  `computer1-agent1`, `computer1-agent2`, `computer2-agent1`,
  `computer2-agent2`. Each pair shares a network (`computer1-net` /
  `computer2-net`) that is `internal: true` — no outbound internet from
  either "computer" — while each desktop's noVNC port is still published to
  the host (inbound port mapping isn't blocked by `internal`).
- `viewer/index.html` — a one-page link list to all four desktops, served by
  a tiny nginx container on port 8080.

## Run it

Requires Docker with enough RAM allocated for 4 containers × the model you
pick (see "Hardware" below). No GPU required; add one yourself if you have
it (see note at the bottom).

```bash
cd docker-desktop-agents
cp .env.example .env     # defaults work out of the box; tune models/tasks if you like
docker compose up --build
```

Then open:

- http://localhost:8080 — link list
- http://localhost:6901 — computer1 / agent1
- http://localhost:6902 — computer1 / agent2
- http://localhost:6903 — computer2 / agent1
- http://localhost:6904 — computer2 / agent2

Each noVNC page prompts for the VNC password (`VNC_PASSWORD` in `.env`,
default `playground`).

## Making it an actually fair competition

The whole point of running this with local models is to compare them, so
the compose file keeps everything *except the model* identical by default:

- **Same task** for all four agents (`SHARED_TASK` in `.env`) — override
  individual `COMPUTERx_AGENTy_TASK` vars only if you deliberately want
  different tasks instead.
- **Same decoding params**: `OLLAMA_TEMPERATURE`, `OLLAMA_SEED`,
  `OLLAMA_NUM_PREDICT` — shared across agents via one set of env vars.
- **Same turn/wall-clock budget**: `MAX_TURNS`, `TURN_DELAY_SECONDS`,
  `WALL_CLOCK_LIMIT_SECONDS`.
- **Same compute**: `AGENT_CPUS` / `AGENT_MEM_LIMIT` apply identically to
  every container (`cpus:`/`mem_limit:` in the compose file) — a model
  isn't competing fairly if it secretly gets more cores than its rival.
- **Same screen size/resolution** for every desktop.
- **Same action vocabulary and system prompt** (`agent/agent.py`'s
  `SYSTEM_PROMPT`, shared code, not per-agent).
- **No internet for anyone** — neither "computer" can reach the network at
  runtime, so no agent can cheat by browsing for the answer.

The one thing meant to vary is the model: set
`COMPUTER1_AGENT1_MODEL` / `COMPUTER1_AGENT2_MODEL` / `COMPUTER2_AGENT1_MODEL`
/ `COMPUTER2_AGENT2_MODEL` in `.env` to whatever you want to pit against each
other, then `docker compose build` (model changes require a rebuild — they're
pulled into the image, not downloaded at runtime) and `docker compose up`.

Any Ollama vision model works: `moondream` (default — small, ~1.8B, fast on
CPU), `llava`, `llava-llama3`, `llava-phi3`, `bakllava`, `minicpm-v`,
`llama3.2-vision` (bigger, needs considerably more RAM and time per turn).

## Hardware

Four containers run four separate Ollama instances simultaneously. With the
default `moondream` on all four and `AGENT_CPUS=2.0` / `AGENT_MEM_LIMIT=4g`
each, budget roughly 8 cores / 16 GB free for the whole stack to not thrash.
Bigger models (e.g. `llama3.2-vision`) need proportionally more — drop to 2
containers (comment out two services) if your machine can't fit four.

Optional GPU: add an `nvidia` runtime/`deploy.resources.reservations.devices`
block to `x-agent-common` in `docker-compose.yml` yourself if you have an
NVIDIA GPU and the `nvidia-container-toolkit` installed — just apply it to
all four services equally, or the comparison stops being fair.

## Notes / limits

- If Ollama never comes up (e.g. the model failed to pull at build time),
  the agent logs it and idles with the desktop still viewable, so you can
  still poke around manually over VNC.
- `MAX_TURNS` / `WALL_CLOCK_LIMIT_SECONDS` cap each agent's loop so a stuck
  model doesn't run forever; it idles (desktop stays viewable) once hit.
- `agent/agent.py` is intentionally minimal (single JSON-action-per-turn
  loop, short rolling text history, no retries beyond basic backoff on a
  failed Ollama call) — it's a playground harness, not a production agent
  framework. Small local vision models will frequently misread coordinates
  or emit malformed JSON; that's expected and part of what you're comparing.
