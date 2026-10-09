"""The agent contract.

An agent is a policy: ``observe(obs) -> Intent``. It is given a plain dict
observation (already filtered to what that side may see) plus its remaining
action points for the tick, and must return a single schema-valid intent. If
it wants to do nothing it returns the side's no-op (DWELL / HOLD).

This is the *only* surface an agent has. There is no access to the world, the
log, the scorer, or any network — which is what makes the sandbox meaningful.
"""

from __future__ import annotations

import abc

from ..schema import Intent, Side


class Agent(abc.ABC):
    side: Side

    def __init__(self, side: Side, seed: int = 0) -> None:
        self.side = side
        self.seed = seed

    @abc.abstractmethod
    def observe(self, obs: dict, ap_remaining: int) -> Intent:
        """Return one schema-valid intent for this tick."""

    def reset(self) -> None:  # optional hook for stateful agents
        pass
