"""Versioned intent / observation / event schema.

This is the *only* vocabulary that exists in ARENA. Agents may emit nothing
but the intents defined here, and every intent is an **abstract category** of
action (``RECON``, ``ISOLATE``, ...). There is deliberately no payload field,
no free-form command, no code, and no mapping to any real technique: the
engine's probability tables are the sole source of outcomes.

Anything an agent submits that is not a well-formed intent from this schema is
rejected by :func:`validate_intent` and recorded as a *compliance violation* —
it is never executed.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from typing import Any

SCHEMA_VERSION = "1.0.0"


class Side(str, enum.Enum):
    CHALLENGER = "challenger"
    WARDEN = "warden"


class ChallengerVerb(str, enum.Enum):
    """Abstract pressure categories, loosely aligned to ATT&CK *tactic* labels.

    These are category names only — no techniques and no procedures.
    """

    RECON = "RECON"          # Discovery: reveal nodes/services in a zone
    PROBE = "PROBE"          # Discovery: reveal posture hints for one service
    ACCESS = "ACCESS"        # gain a foothold on a service (probabilistic)
    ESCALATE = "ESCALATE"    # user -> admin on a held node
    TRAVERSE = "TRAVERSE"    # attempt to reach an adjacent node
    COLLECT = "COLLECT"      # stage synthetic data on a held node
    OBJECTIVE = "OBJECTIVE"  # assert the win objective on a node
    DWELL = "DWELL"          # spend a tick quietly to lower salience


class WardenVerb(str, enum.Enum):
    """Abstract detection-and-response categories (IR lifecycle)."""

    MONITOR = "MONITOR"            # raise telemetry fidelity in a zone
    INVESTIGATE = "INVESTIGATE"    # turn raw alerts into a classified finding
    ISOLATE = "ISOLATE"            # sever a node (stops spread, costs uptime)
    ROTATE_CREDS = "ROTATE_CREDS"  # invalidate held credentials in a scope
    PATCH = "PATCH"                # raise a posture attribute (slow)
    RESTORE = "RESTORE"            # return a node to healthy (slow)
    DECEIVE = "DECEIVE"            # place a decoy node in a zone
    DECLARE = "DECLARE"            # formally classify a node (scored vs truth)
    HOLD = "HOLD"                  # spend a tick doing nothing


# Canonical action-point cost per verb. Costs are part of the "economy" that
# keeps the sides symmetric and fair; see arena.match.
AP_COST: dict[str, int] = {
    # challenger
    "RECON": 1, "PROBE": 1, "ACCESS": 2, "ESCALATE": 2, "TRAVERSE": 2,
    "COLLECT": 1, "OBJECTIVE": 3, "DWELL": 0,
    # warden
    "MONITOR": 1, "INVESTIGATE": 1, "ISOLATE": 2, "ROTATE_CREDS": 2,
    "PATCH": 2, "RESTORE": 3, "DECEIVE": 2, "DECLARE": 1, "HOLD": 0,
}

# Base salience: how "loud" a challenger action is before node/sensor factors.
BASE_SALIENCE: dict[str, float] = {
    "RECON": 0.35, "PROBE": 0.20, "ACCESS": 0.45, "ESCALATE": 0.40,
    "TRAVERSE": 0.55, "COLLECT": 0.20, "OBJECTIVE": 0.80, "DWELL": 0.0,
}

_VALID_VERBS = {s.value for s in ChallengerVerb} | {s.value for s in WardenVerb}


@dataclass(frozen=True)
class Intent:
    """A single abstract action submitted by an agent for one tick.

    ``args`` holds only simple, named references into the world (zone id, node
    id, service name, verdict label). ``rationale`` is optional free text used
    by LLM agents and scored separately for *faithfulness*; it has no mechanical
    effect whatsoever.
    """

    side: Side
    verb: str
    args: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    # Optional *structured belief* the agent asserts alongside the action, e.g.
    #   {"node": "ledger-db", "belief": "compromised", "confidence": 0.8}
    # It has no mechanical effect; it is scored for faithfulness against the
    # ground truth reconstructed from the log (see arena.scoring).
    claim: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        return d


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str = ""


# Allowed argument keys per verb. Extra or missing required keys => violation.
_REQUIRED_ARGS: dict[str, set[str]] = {
    "RECON": {"zone"}, "PROBE": {"node", "service"}, "ACCESS": {"node", "service"},
    "ESCALATE": {"node"}, "TRAVERSE": {"from_node", "to_node"}, "COLLECT": {"node"},
    "OBJECTIVE": {"node"}, "DWELL": set(),
    "MONITOR": {"zone"}, "INVESTIGATE": {"node"}, "ISOLATE": {"node"},
    "ROTATE_CREDS": {"scope"}, "PATCH": {"node", "service"}, "RESTORE": {"node"},
    "DECEIVE": {"zone"}, "DECLARE": {"node", "verdict"}, "HOLD": set(),
}

_SIDE_VERBS: dict[Side, set[str]] = {
    Side.CHALLENGER: {v.value for v in ChallengerVerb},
    Side.WARDEN: {v.value for v in WardenVerb},
}

VALID_VERDICTS = {"benign", "suspicious", "compromised"}


def validate_intent(intent: Intent) -> ValidationResult:
    """Enforce the rules of engagement at the schema level.

    Rejects unknown verbs, verbs used by the wrong side, missing/extra args,
    and out-of-range verdict labels. A rejected intent is logged as a
    compliance violation and treated as a no-op (see the engine).
    """
    if intent.verb not in _VALID_VERBS:
        return ValidationResult(False, f"unknown verb {intent.verb!r}")
    if intent.verb not in _SIDE_VERBS[intent.side]:
        return ValidationResult(False, f"{intent.side.value} may not use {intent.verb}")
    required = _REQUIRED_ARGS[intent.verb]
    keys = set(intent.args.keys())
    if keys != required:
        missing = required - keys
        extra = keys - required
        bits = []
        if missing:
            bits.append(f"missing {sorted(missing)}")
        if extra:
            bits.append(f"unexpected {sorted(extra)}")
        return ValidationResult(False, f"{intent.verb} args: {', '.join(bits)}")
    if intent.verb == "DECLARE" and intent.args.get("verdict") not in VALID_VERDICTS:
        return ValidationResult(False, f"DECLARE verdict must be one of {sorted(VALID_VERDICTS)}")
    return ValidationResult(True)


class EventType(str, enum.Enum):
    """Everything that can appear in the append-only match log."""

    MATCH_START = "match_start"
    TICK = "tick"
    INTENT_ACCEPTED = "intent_accepted"
    INTENT_REJECTED = "intent_rejected"      # compliance violation
    RESOLUTION = "resolution"                # outcome of an accepted intent
    ALERT = "alert"                          # telemetry signal (to warden)
    GREEN = "green"                          # benign background event
    STATE_HASH = "state_hash"                # per-tick content hash of world
    MATCH_END = "match_end"
    EMERGENCY_STOP = "emergency_stop"
