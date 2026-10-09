"""Degenerate baseline agents — the dominated strategies scoring must resist.

These encode the obvious ways to game a *single* scoring axis. The reward-
hacking tests (``tests/test_reward_hacking.py``) assert that none of them beats
the balanced rule-based agent on the scalar readout — i.e. the multi-axis,
non-fungible scoring punishes each degenerate strategy on the axes it ignores.
"""

from __future__ import annotations

from ..schema import Intent, Side


class PassiveWarden:
    """Does nothing. Maximises availability and zero false alarms — but never
    detects or contains anything."""

    side = Side.WARDEN

    def __init__(self, side: Side = Side.WARDEN, seed: int = 0) -> None:
        self.side = side
        self.seed = seed

    def reset(self) -> None:
        pass

    def observe(self, obs: dict, ap_remaining: int) -> Intent:
        return Intent(Side.WARDEN, "HOLD", {}, "passive baseline")


class IsolateAllWarden:
    """Isolates every node it can, one per tick. Maximises containment/recall
    but destroys availability — the SLA axis should punish it."""

    side = Side.WARDEN

    def __init__(self, side: Side = Side.WARDEN, seed: int = 0) -> None:
        self.side = side
        self.seed = seed
        self._done: set[str] = set()

    def reset(self) -> None:
        self._done = set()

    def observe(self, obs: dict, ap_remaining: int) -> Intent:
        from ..schema import AP_COST
        for nid in sorted(obs["inventory"]):
            if nid not in self._done and AP_COST["ISOLATE"] <= ap_remaining:
                self._done.add(nid)
                return Intent(Side.WARDEN, "ISOLATE", {"node": nid}, "isolate everything")
        return Intent(Side.WARDEN, "HOLD", {}, "all isolated")


class DeclareAllWarden:
    """Declares every node compromised. Maximises recall but tanks precision
    and false-alarm score."""

    side = Side.WARDEN

    def __init__(self, side: Side = Side.WARDEN, seed: int = 0) -> None:
        self.side = side
        self.seed = seed
        self._done: set[str] = set()

    def reset(self) -> None:
        self._done = set()

    def observe(self, obs: dict, ap_remaining: int) -> Intent:
        for nid in sorted(obs["inventory"]):
            if nid not in self._done:
                self._done.add(nid)
                return Intent(Side.WARDEN, "DECLARE", {"node": nid, "verdict": "compromised"},
                              "declare everything compromised",
                              claim={"node": nid, "belief": "compromised", "confidence": 0.99})
        return Intent(Side.WARDEN, "HOLD", {}, "all declared")


class NullChallenger:
    """Never acts — a floor opponent for sanity checks."""

    side = Side.CHALLENGER

    def __init__(self, side: Side = Side.CHALLENGER, seed: int = 0) -> None:
        self.side = side
        self.seed = seed

    def reset(self) -> None:
        pass

    def observe(self, obs: dict, ap_remaining: int) -> Intent:
        return Intent(Side.CHALLENGER, "DWELL", {}, "null challenger")
