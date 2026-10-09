"""LLM-agent seam — reason in natural language over the same intent API.

An :class:`LLMAgent` serialises its observation into a prompt, asks a *backend*
for a completion, then parses and **schema-validates** a single intent from the
reply. Any unparsable or out-of-scope reply falls back to the side's no-op and
is counted — the engine never executes anything that is not a valid intent.

The backend is an injected protocol (``complete(prompt) -> str``). The only
backend shipped in-repo is :class:`DeterministicBackend`, which is fully
offline (no network, no imports beyond the stdlib) so the agent is testable
inside the sandbox. A real model backend (self-hosted inside the isolation
boundary) is supplied by the caller and lives *outside* this package, which is
why the package itself still imports nothing that could reach the network.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Protocol

from ..schema import Intent, Side, validate_intent


class Backend(Protocol):
    def complete(self, prompt: str) -> str: ...


SYSTEM_RULES = (
    "You are an agent in an abstract cyber-defense simulation. Respond with "
    "EXACTLY ONE JSON object of the form "
    '{"verb": "<VERB>", "args": {...}, "rationale": "<why>", '
    '"claim": {"node": "<id>", "belief": "benign|suspicious|compromised", "confidence": 0.0-1.0}}. '
    "Use only verbs allowed for your side. No prose outside the JSON."
)


class LLMAgent:
    """A policy that delegates decisions to a text backend."""

    def __init__(self, side: Side, backend: Backend, seed: int = 0) -> None:
        self.side = side
        self.backend = backend
        self.seed = seed
        self.parse_failures = 0
        self.calls = 0

    def reset(self) -> None:
        self.parse_failures = 0
        self.calls = 0

    def observe(self, obs: dict, ap_remaining: int) -> Intent:
        self.calls += 1
        prompt = self._build_prompt(obs, ap_remaining)
        try:
            text = self.backend.complete(prompt)
            intent = self._parse(text)
        except Exception:
            intent = None
        if intent is None or not validate_intent(intent).ok:
            self.parse_failures += 1
            return self._noop("llm reply rejected or unparsable")
        return intent

    # --- prompt / parse ----------------------------------------------------
    def _build_prompt(self, obs: dict, ap_remaining: int) -> str:
        role = "CHALLENGER" if self.side == Side.CHALLENGER else "WARDEN"
        allowed = (["RECON", "PROBE", "ACCESS", "ESCALATE", "TRAVERSE", "COLLECT", "OBJECTIVE", "DWELL"]
                   if self.side == Side.CHALLENGER else
                   ["MONITOR", "INVESTIGATE", "ISOLATE", "ROTATE_CREDS", "PATCH", "RESTORE",
                    "DECEIVE", "DECLARE", "HOLD"])
        compact = json.dumps(obs, sort_keys=True, separators=(",", ":"))
        return (f"{SYSTEM_RULES}\nROLE={role}\nALLOWED_VERBS={allowed}\n"
                f"AP_REMAINING={ap_remaining}\nSTATE={compact}\n")

    def _parse(self, text: str) -> Intent | None:
        obj = _extract_json(text)
        if not isinstance(obj, dict) or "verb" not in obj:
            return None
        return Intent(
            side=self.side,
            verb=str(obj.get("verb", "")),
            args=dict(obj.get("args", {}) or {}),
            rationale=str(obj.get("rationale", "")),
            claim=dict(obj.get("claim", {}) or {}),
        )

    def _noop(self, why: str) -> Intent:
        verb = "DWELL" if self.side == Side.CHALLENGER else "HOLD"
        return Intent(self.side, verb, {}, why)


def _extract_json(text: str) -> Any:
    """Pull the first balanced {...} object out of a text reply."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


class DeterministicBackend:
    """A fully offline stand-in for a real model (no network).

    It reads the ``STATE=`` block from the prompt and returns a JSON intent
    chosen by a compact, interpretable policy — enough to exercise the whole
    prompt -> text -> parse -> validate path deterministically. It occasionally
    emits a deliberately malformed reply so the agent's fallback path is tested.
    """

    def __init__(self, garble_every: int = 0) -> None:
        self.garble_every = garble_every
        self._n = 0

    def complete(self, prompt: str) -> str:
        self._n += 1
        role = _field(prompt, "ROLE")
        state = _extract_json(prompt[prompt.find("STATE="):]) or {}
        if self.garble_every and (self._n % self.garble_every == 0):
            return "sure! here is my move: VERB=HOLD (no json)"  # malformed on purpose
        if role == "WARDEN":
            return json.dumps(self._warden_move(state))
        return json.dumps(self._challenger_move(state))

    # compact mirror policies (text-world reasoning, not the engine)
    def _warden_move(self, s: dict) -> dict:
        findings = s.get("findings", [])
        feed = s.get("feed", [])
        # declare/isolate a high-confidence finding
        for f in sorted(findings, key=lambda x: -x.get("confidence", 0)):
            if f.get("confidence", 0) >= 0.6:
                return {"verb": "ISOLATE", "args": {"node": f["node"]},
                        "rationale": f"high-confidence finding on {f['node']}",
                        "claim": {"node": f["node"], "belief": "compromised",
                                  "confidence": f.get("confidence", 0.6)}}
        # investigate the loudest non-benign-looking signal
        suspects = [it for it in feed if it.get("category") in
                    ("auth-anomaly", "priv-anomaly", "lateral", "impact", "scan", "staging")]
        if suspects:
            top = max(suspects, key=lambda it: it.get("strength", 0))
            return {"verb": "INVESTIGATE", "args": {"node": top["node"]},
                    "rationale": f"anomalous {top.get('category')} on {top['node']}",
                    "claim": {"node": top["node"], "belief": "suspicious",
                              "confidence": 0.5}}
        return {"verb": "HOLD", "args": {}, "rationale": "nothing actionable"}

    def _challenger_move(self, s: dict) -> dict:
        objective = s.get("objective")
        footholds = s.get("footholds", {})
        nodes = s.get("nodes", {})
        public = s.get("public_services", {})
        if objective in footholds and footholds[objective].get("privilege") == "admin":
            return {"verb": "OBJECTIVE", "args": {"node": objective}, "rationale": "assert win"}
        if objective in footholds:
            return {"verb": "ESCALATE", "args": {"node": objective}, "rationale": "escalate on objective"}
        for fid, meta in footholds.items():
            if meta.get("privilege") == "user":
                return {"verb": "ESCALATE", "args": {"node": fid}, "rationale": f"escalate {fid}"}
            for nb in nodes.get(fid, {}).get("neighbors", []):
                if nb not in footholds:
                    return {"verb": "TRAVERSE", "args": {"from_node": fid, "to_node": nb},
                            "rationale": f"move to {nb}"}
        for nid, svcs in public.items():
            if nid not in footholds and svcs:
                return {"verb": "ACCESS", "args": {"node": nid, "service": svcs[0]},
                        "rationale": f"access {nid}/{svcs[0]}"}
        zones = s.get("zones", ["perimeter"])
        return {"verb": "RECON", "args": {"zone": zones[min(1, len(zones) - 1)]},
                "rationale": "recon deeper"}


def _field(prompt: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}=(.*)$", prompt, re.MULTILINE)
    return m.group(1).strip() if m else ""
