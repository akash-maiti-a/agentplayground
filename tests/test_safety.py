"""Safety suite (continuous): the properties that make the range safe.

1. **No network imports** anywhere in the package — the "zero egress" property
   is trivially auditable because the engine only ever uses the standard
   library's non-networking modules.
2. **Zero egress at runtime** — a full match runs with ``socket`` monkeypatched
   to fail closed; if the engine tried to open a socket the match would error.
3. **Emergency stop** halts a match immediately and records the reason.
4. **Schema fuzzing** — malformed / out-of-scope intents are rejected and never
   change world state (they are logged as compliance violations instead).
5. **Reset** — scenario generation restores a clean, identical world.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from arena.agents import RuleChallenger, RuleWarden
from arena.control import ControlPlane
from arena.engine import Engine
from arena.match import run_match
from arena.rng import DeterministicRNG
from arena.scenario import generate_world
from arena.schema import EventType, Intent, Side

ARENA_DIR = pathlib.Path(__file__).resolve().parents[1] / "arena"

# Modules that would imply the ability to reach the network / spawn processes.
FORBIDDEN_IMPORTS = {
    "socket", "ssl", "asyncio", "urllib", "http", "ftplib", "smtplib",
    "telnetlib", "requests", "httpx", "aiohttp", "subprocess", "multiprocessing",
    "ctypes",
}


def _iter_imports(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                yield n.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.module.split(".")[0]


def test_no_network_or_process_imports_in_package():
    offenders = []
    for path in ARENA_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for mod in _iter_imports(tree):
            if mod in FORBIDDEN_IMPORTS:
                offenders.append(f"{path.name}: {mod}")
    assert not offenders, f"forbidden imports found: {offenders}"


def test_zero_egress_at_runtime(monkeypatch):
    import socket

    def _boom(*a, **k):
        raise AssertionError("network access attempted inside the sandbox!")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    # a full match must complete without ever touching the network
    res = run_match("T1", 7, RuleChallenger(), RuleWarden())
    assert res.ticks > 0
    assert res.winner in {"warden", "challenger", "draw"}


def test_emergency_stop_halts_immediately():
    control = ControlPlane()
    control.emergency_stop("unit-test")
    res = run_match("T1", 1, RuleChallenger(), RuleWarden(), control=control)
    assert res.reason.startswith("emergency_stop")
    assert res.ticks == 0
    assert res.log.events_of(EventType.EMERGENCY_STOP.value)


def test_malformed_intents_are_rejected_not_executed():
    w = generate_world("T1", 5)
    before = w.state_hash()
    eng = Engine(w, DeterministicRNG(5))
    bad = [
        Intent(Side.CHALLENGER, "ISOLATE", {"node": "gate-00"}),   # warden verb
        Intent(Side.CHALLENGER, "NUKE", {"node": "x"}),            # unknown verb
        Intent(Side.CHALLENGER, "ACCESS", {"node": "x"}),          # missing args
        Intent(Side.WARDEN, "DECLARE", {"node": "x", "verdict": "pwned"}),  # bad verdict
    ]
    for intent in bad:
        w2 = generate_world("T1", 5)  # fresh world each time
        e2 = Engine(w2, DeterministicRNG(5))
        h0 = w2.state_hash()
        out = e2.resolve_tick(intent if intent.side == Side.CHALLENGER else None,
                              intent if intent.side == Side.WARDEN else None)
        rejected = [e for e in out.events if e["type"] == EventType.INTENT_REJECTED.value]
        assert rejected, f"expected rejection for {intent.verb}"
        # only the tick counter changed; no gameplay state mutated by a bad intent
        w2.tick = 0
        assert w2.state_hash() == h0
    assert before == generate_world("T1", 5).state_hash()  # generation is clean/repeatable


def test_reset_world_is_clean_and_identical():
    a = generate_world("T2", 321)
    b = generate_world("T2", 321)
    assert a.state_hash() == b.state_hash()
    assert all(n.status.value == "healthy" for n in a.nodes.values())
    assert not a.objective_reached
