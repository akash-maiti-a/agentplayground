"""Agent implementations.

All agents share one narrow contract (:class:`~arena.agents.base.Agent`): they
receive a read-only observation dict for the tick and return exactly one
:class:`~arena.schema.Intent`. Agents never touch world state directly and
never execute anything — the engine owns all outcomes.
"""

from .base import Agent
from .challenger import RuleChallenger
from .warden import RuleWarden
from .scripted import ScriptedAgent

__all__ = ["Agent", "RuleChallenger", "RuleWarden", "ScriptedAgent"]
