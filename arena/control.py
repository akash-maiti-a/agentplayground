"""Control plane — emergency stop and resource limits.

A single, human-operable kill switch plus hard ceilings that bound any match.
The match loop consults the :class:`ControlPlane` every tick; tripping the stop
or exceeding a ceiling ends the match immediately and cleanly, with the reason
recorded in the log.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class ResourceLimits:
    max_ticks: int = 200            # hard tick ceiling per match
    max_wall_seconds: float = 60.0  # wall-clock ceiling per match
    max_nodes: int = 2_000          # guard against runaway world growth (decoys)


class ControlPlane:
    """Watchdog + emergency stop shared across a match (or a batch)."""

    def __init__(self, limits: ResourceLimits | None = None) -> None:
        self.limits = limits or ResourceLimits()
        self._stopped = False
        self._stop_reason = ""
        self._start = time.monotonic()

    def emergency_stop(self, reason: str = "operator") -> None:
        """Human-operated kill switch: halt everything immediately."""
        self._stopped = True
        self._stop_reason = reason

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def stop_reason(self) -> str:
        return self._stop_reason

    def check(self, *, tick: int, node_count: int) -> str | None:
        """Return a halt reason if a limit is breached, else None."""
        if self._stopped:
            return f"emergency_stop:{self._stop_reason}"
        if tick >= self.limits.max_ticks:
            return "tick_limit"
        if (time.monotonic() - self._start) > self.limits.max_wall_seconds:
            return "wall_clock_limit"
        if node_count > self.limits.max_nodes:
            return "node_limit"
        return None
