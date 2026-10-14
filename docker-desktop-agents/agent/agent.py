"""Local-LLM computer-use agent loop driving the container's X desktop.

No cloud API calls — this talks only to the Ollama server running inside the
same container (127.0.0.1:11434), which serves a vision model baked into the
image at build time. Screenshots come from ImageMagick's `import`; input is
driven with `xdotool`. The model has no native "computer tool," so it's
prompted to reply with exactly one JSON action object per turn, which is
parsed and executed.

Fairness knobs (meant to be identical across every agent in the compose
file, so differences in behavior trace back to the model, not the harness):
OLLAMA_TEMPERATURE, OLLAMA_SEED, OLLAMA_NUM_PREDICT, MAX_TURNS,
TURN_DELAY_SECONDS, WALL_CLOCK_LIMIT_SECONDS, plus the compose-level cpus/
mem_limit on the container itself.
"""

from __future__ import annotations  # the base image's python3 is 3.8; this enables `list[str]`/`X | None`

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

PUBLIC_DIR = "/opt/agent/public"

DISPLAY = os.environ.get("DISPLAY", ":1")
WIDTH = int(os.environ.get("DISPLAY_WIDTH", "1280"))
HEIGHT = int(os.environ.get("DISPLAY_HEIGHT", "800"))
AGENT_NAME = os.environ.get("AGENT_NAME", "agent")
TASK = os.environ.get("TASK", "Open a terminal and look around the desktop.")

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "moondream")
OLLAMA_TEMPERATURE = float(os.environ.get("OLLAMA_TEMPERATURE", "0.2"))
OLLAMA_SEED = int(os.environ.get("OLLAMA_SEED", "42"))
OLLAMA_NUM_PREDICT = int(os.environ.get("OLLAMA_NUM_PREDICT", "256"))

MAX_TURNS = int(os.environ.get("MAX_TURNS", "200"))
TURN_DELAY_SECONDS = float(os.environ.get("TURN_DELAY_SECONDS", "1.0"))
WALL_CLOCK_LIMIT_SECONDS = float(os.environ.get("WALL_CLOCK_LIMIT_SECONDS", "1800"))
HISTORY_WINDOW = 12  # most recent actions kept in the text history shown to the model

SYSTEM_PROMPT = f"""You are controlling a {WIDTH}x{HEIGHT} Linux desktop through screenshots.
You will be shown the current screenshot and a short history of your past actions.
Reply with EXACTLY ONE JSON object and nothing else (no markdown fences, no prose).

Valid actions:
{{"action": "left_click", "coordinate": [x, y]}}
{{"action": "right_click", "coordinate": [x, y]}}
{{"action": "double_click", "coordinate": [x, y]}}
{{"action": "left_click_drag", "start_coordinate": [x, y], "coordinate": [x, y]}}
{{"action": "mouse_move", "coordinate": [x, y]}}
{{"action": "type", "text": "..."}}
{{"action": "key", "text": "Return"}}
{{"action": "scroll", "coordinate": [x, y], "scroll_direction": "down", "scroll_amount": 3}}
{{"action": "wait", "duration": 1}}
{{"action": "done"}}

Coordinates are absolute pixels within the {WIDTH}x{HEIGHT} screen. Use "done" once the
task is complete. Pick exactly one action per turn based on the current screenshot."""

env = {**os.environ, "DISPLAY": DISPLAY}


def log(msg: str) -> None:
    print(f"[{AGENT_NAME}] {msg}", flush=True)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, capture_output=True)


def screenshot_b64() -> str:
    import base64
    import shutil

    path = "/tmp/shot.png"
    run(["import", "-window", "root", path])
    try:
        shutil.copyfile(path, f"{PUBLIC_DIR}/latest.png")
    except OSError:
        pass  # observability is best-effort; never let it break the agent loop
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def write_status(turn: int, reply: str, action: dict | None, note: str | None) -> None:
    status = {
        "agent_name": AGENT_NAME,
        "model": OLLAMA_MODEL,
        "task": TASK,
        "turn": turn,
        "max_turns": MAX_TURNS,
        "model_reply": reply,
        "parsed_action": action,
        "note": note,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        tmp = f"{PUBLIC_DIR}/status.json.tmp"
        with open(tmp, "w") as f:
            json.dump(status, f, indent=2)
        os.replace(tmp, f"{PUBLIC_DIR}/status.json")
    except OSError:
        pass


def xdotool(*args: str) -> None:
    run(["xdotool", *args])


def execute_action(action: dict) -> str | None:
    """Executes one action. Returns a text note, if any."""
    kind = action.get("action")

    if kind in ("left_click", "right_click", "double_click"):
        x, y = action.get("coordinate", [None, None])
        if x is not None:
            xdotool("mousemove", str(x), str(y))
        if kind == "double_click":
            xdotool("click", "--repeat", "2", "--delay", "100", "1")
        else:
            button = {"left_click": "1", "right_click": "3"}[kind]
            xdotool("click", button)
        return None

    if kind == "left_click_drag":
        start = action.get("start_coordinate")
        end = action.get("coordinate")
        if start:
            xdotool("mousemove", str(start[0]), str(start[1]))
        xdotool("mousedown", "1")
        if end:
            xdotool("mousemove", str(end[0]), str(end[1]))
        xdotool("mouseup", "1")
        return None

    if kind == "mouse_move":
        x, y = action.get("coordinate", [0, 0])
        xdotool("mousemove", str(x), str(y))
        return None

    if kind == "type":
        xdotool("type", "--delay", "30", action.get("text", ""))
        return None

    if kind == "key":
        xdotool("key", action.get("text", ""))
        return None

    if kind == "scroll":
        x, y = action.get("coordinate", [WIDTH // 2, HEIGHT // 2])
        amount = int(action.get("scroll_amount", 3))
        direction = action.get("scroll_direction", "down")
        button = {"up": "4", "down": "5", "left": "6", "right": "7"}.get(direction, "5")
        xdotool("mousemove", str(x), str(y))
        for _ in range(amount):
            xdotool("click", button)
        return None

    if kind == "wait":
        time.sleep(float(action.get("duration", 1)))
        return None

    if kind == "done":
        return "done"

    return f"unsupported action: {kind!r}"


def normalize_action(action: dict) -> None:
    """Tolerate small local models' schema drift uniformly across all agents:
    an alternate "type" key instead of "action", and mixed-case action names."""
    if "action" not in action and "type" in action:
        action["action"] = action.pop("type")
    if isinstance(action.get("action"), str):
        action["action"] = action["action"].lower()


def extract_json(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        start = text.find("{", idx)
        if start == -1:
            return None
        try:
            obj, _ = decoder.raw_decode(text, start)
            return obj
        except json.JSONDecodeError:
            idx = start + 1
    return None


def wait_for_ollama(timeout: float = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(2)
    return False


def call_model(history_text: str, screenshot: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "options": {
            "temperature": OLLAMA_TEMPERATURE,
            "seed": OLLAMA_SEED,
            "num_predict": OLLAMA_NUM_PREDICT,
        },
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Task: {TASK}\n\n"
                    f"Recent action history (most recent last):\n{history_text or '(none yet)'}\n\n"
                    "Here is the current screenshot. Respond with exactly one JSON action."
                ),
                "images": [screenshot],
            },
        ],
    }
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("message", {}).get("content", "")


def main() -> None:
    log(f"waiting for local Ollama ({OLLAMA_MODEL}) to come up...")
    if not wait_for_ollama():
        log("Ollama never became ready; idling so the desktop is still viewable.")
        while True:
            time.sleep(3600)

    log(f"starting task: {TASK!r} on display {DISPLAY} with model {OLLAMA_MODEL!r}")

    history: list[str] = []
    start_time = time.time()

    for turn in range(1, MAX_TURNS + 1):
        if time.time() - start_time > WALL_CLOCK_LIMIT_SECONDS:
            log("wall-clock limit reached; stopping.")
            break

        shot = screenshot_b64()
        try:
            reply = call_model("\n".join(history[-HISTORY_WINDOW:]), shot)
        except Exception as exc:  # local server hiccup, model crash, OOM, etc.
            log(f"Ollama call failed: {exc}; retrying in 5s")
            write_status(turn, f"(Ollama call failed: {exc})", None, None)
            time.sleep(5)
            continue

        action = extract_json(reply)
        if action is None:
            log(f"turn {turn}: no parseable JSON in reply: {reply[:200]!r}")
            history.append(f"turn {turn}: invalid reply, no action taken")
            write_status(turn, reply, None, "no parseable JSON")
            time.sleep(TURN_DELAY_SECONDS)
            continue

        normalize_action(action)
        note = execute_action(action)
        log(f"turn {turn}: {action}" + (f" -> {note}" if note else ""))
        history.append(f"turn {turn}: {json.dumps(action)}")
        write_status(turn, reply, action, note)

        if action.get("action") == "done":
            log("model signaled done.")
            break

        time.sleep(TURN_DELAY_SECONDS)
    else:
        log("max turns reached.")

    log("agent loop finished; idling so the desktop stays viewable.")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
