"""A scripted agent that replays a fixed list of intents.

Used for deterministic "golden transcript" tests and as a static opponent in
single-agent harness tests. When the script is exhausted it emits the side's
no-op forever.
"""

from __future__ import annotations

from ..schema import Intent, Side


class ScriptedAgent:
    def __init__(self, side: Side, script: list[Intent], seed: int = 0) -> None:
        self.side = side
        self.seed = seed
        self._script = list(script)
        self._i = 0

    def reset(self) -> None:
        self._i = 0

    def observe(self, obs: dict, ap_remaining: int) -> Intent:
        noop = "DWELL" if self.side == Side.CHALLENGER else "HOLD"
        while self._i < len(self._script):
            intent = self._script[self._i]
            self._i += 1
            return intent
        return Intent(side=self.side, verb=noop, args={}, rationale="script exhausted")
