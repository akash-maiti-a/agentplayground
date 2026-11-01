"""Post-hoc scoring — computed from the match log only, never during play.

Scoring is deliberately **multi-objective and non-fungible**: it reports a
*vector* of metrics per side. A single "win" scalar is only a transparent,
configurable weighting of that vector and is never fed back to the agents. This
is the core defence against reward hacking — e.g. a Warden that isolates
everything maximises containment but is immediately punished on the
availability and false-alarm axes, so it cannot dominate the scalar.

Everything here reads :class:`~arena.log.MatchLog` and reconstructs ground
truth from the logged resolution events, so results are reproducible and
auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .log import MatchLog
from .schema import EventType, Side

# Transparent default weights (sum = 1; the scalar is just a readout).
#
# ``mission_success`` is deliberately the largest axis and is SLA-gated: a
# defender only earns it by protecting the objective *while keeping services
# available*. This is what makes the scalar reward-hacking resistant — a
# "declare everything, never contain" policy scores 0 on mission (the objective
# falls), and an "isolate everything" policy only earns a fraction of mission
# (the SLA collapses) and is further punished on availability and false alarms.
WARDEN_WEIGHTS = {
    "mission_success": 0.30, "mttd_score": 0.10, "mttc_score": 0.12,
    "classification_f1": 0.15, "availability": 0.10,
    "containment_quality": 0.13, "false_alarm_score": 0.10,
}
CHALLENGER_WEIGHTS = {
    "objective_progress": 0.5, "stealth": 0.3, "efficiency": 0.2,
}

_MALICIOUS_VERBS = {"RECON", "PROBE", "ACCESS", "ESCALATE", "TRAVERSE", "COLLECT", "OBJECTIVE"}


def _compromise_timeline(resolutions: list[dict]) -> dict[str, list[tuple[int, bool]]]:
    """Reconstruct, per node, the ordered (tick, is_compromised) transitions.

    A node becomes compromised on a successful ACCESS, or a successful
    non-decoy TRAVERSE into it, and returns to benign when a RESTORE completes.
    This is the ground truth the Warden's stated beliefs are scored against.
    """
    transitions: dict[str, list[tuple[int, bool]]] = {}
    for r in resolutions:
        if not r.get("success"):
            continue
        d = r.get("detail", {})
        tick = r.get("tick", 0)
        if r["verb"] == "ACCESS" and d.get("node"):
            transitions.setdefault(d["node"], []).append((tick, True))
        elif r["verb"] == "TRAVERSE" and d.get("to") and not d.get("decoy"):
            transitions.setdefault(d["to"], []).append((tick, True))
        elif r["verb"] == "RESTORE" and d.get("completed") and d.get("node"):
            transitions.setdefault(d["node"], []).append((tick, False))
    for node in transitions:
        transitions[node].sort()
    return transitions


def _truth_at(timeline: dict[str, list[tuple[int, bool]]], node: str, tick: int) -> bool:
    state = False
    for t, s in timeline.get(node, []):
        if t <= tick:
            state = s
        else:
            break
    return state


@dataclass
class ScoreCard:
    scenario_id: str
    seed: int
    winner: str
    ticks: int
    warden: dict[str, float] = field(default_factory=dict)
    challenger: dict[str, float] = field(default_factory=dict)
    integrity: dict[str, float] = field(default_factory=dict)
    warden_scalar: float = 0.0
    challenger_scalar: float = 0.0
    notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id, "seed": self.seed, "winner": self.winner,
            "ticks": self.ticks, "warden": self.warden, "challenger": self.challenger,
            "integrity": self.integrity, "warden_scalar": round(self.warden_scalar, 4),
            "challenger_scalar": round(self.challenger_scalar, 4), "notes": self.notes,
        }


def _norm_latency(latency: int | None, horizon: int) -> float:
    """Map a latency in ticks to [0,1] where sooner is better; None => 0."""
    if latency is None or horizon <= 0:
        return 0.0
    return round(max(0.0, 1.0 - latency / horizon), 4)


def score_match(log: MatchLog) -> ScoreCard:
    start = next(iter(log.events_of(EventType.MATCH_START.value)), {})
    end = next(iter(reversed(log.events_of(EventType.MATCH_END.value))), {})
    scenario_id = start.get("scenario_id", "?")
    seed = start.get("seed", -1)

    resolutions = log.events_of(EventType.RESOLUTION.value)
    accepted = log.events_of(EventType.INTENT_ACCEPTED.value)
    rejected = log.events_of(EventType.INTENT_REJECTED.value)
    state_hashes = log.events_of(EventType.STATE_HASH.value)
    greens = log.events_of(EventType.GREEN.value)
    alerts = log.events_of(EventType.ALERT.value)

    ticks = state_hashes[-1]["tick"] if state_hashes else 0
    horizon = max(ticks, 1)

    # --- reconstruct ground truth from resolutions -------------------------
    ever_compromised: set[str] = set()
    for r in resolutions:
        if r.get("side") != Side.CHALLENGER.value or not r.get("success"):
            continue
        d = r.get("detail", {})
        if r["verb"] == "ACCESS" and d.get("node"):
            ever_compromised.add(d["node"])
        if r["verb"] == "TRAVERSE" and d.get("to") and not d.get("decoy"):
            ever_compromised.add(d["to"])

    # first malicious challenger action
    first_malicious = next(
        (a["tick"] for a in accepted
         if a.get("side") == Side.CHALLENGER.value and a.get("verb") in _MALICIOUS_VERBS),
        None,
    )

    # declares
    declares = [r for r in resolutions if r["verb"] == "DECLARE" and r.get("success")]
    declared_compromised = {
        r["detail"]["node"] for r in declares
        if r["detail"].get("verdict") == "compromised"
    }
    first_correct_declare = next(
        (r["tick"] for r in declares if r["detail"].get("correct")), None
    )

    # containment
    isolate_success = [r for r in resolutions if r["verb"] == "ISOLATE" and r.get("success")]
    first_isolate = isolate_success[0]["tick"] if isolate_success else None

    # --- warden metrics ----------------------------------------------------
    mttd = (first_correct_declare - first_malicious) if (first_correct_declare is not None and first_malicious is not None) else None
    # Containment that precedes the formal verdict is zero-latency, not "never"
    # (v0.1 returned None here, scoring isolate-then-declare as the worst MTTC).
    mttc = max(0, first_isolate - first_correct_declare) if (first_isolate is not None and first_correct_declare is not None) else None

    tp = len(declared_compromised & ever_compromised)
    fp = len(declared_compromised - ever_compromised)
    fn = len(ever_compromised - declared_compromised)
    precision = tp / (tp + fp) if (tp + fp) else (1.0 if not ever_compromised else 0.0)
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    avail_series = [s.get("availability", 1.0) for s in state_hashes]
    availability = sum(avail_series) / len(avail_series) if avail_series else 1.0

    # collateral: isolations/restores on nodes never actually compromised
    needless_isolations = sum(
        1 for r in isolate_success if r["detail"].get("node") not in ever_compromised
    )
    objective_reached = end.get("reason") == "objective_reached" or any(
        r["verb"] == "OBJECTIVE" and r.get("success") for r in resolutions
    )
    crown_protected = 0.0 if objective_reached else 1.0
    collateral_penalty = min(1.0, needless_isolations / max(1, len(ever_compromised) + 1))
    containment_quality = round(max(0.0, crown_protected - 0.5 * collateral_penalty), 4)

    false_declares = fp
    benign_volume = max(1, len(greens))
    false_alarm_rate = round((false_declares + needless_isolations) / benign_volume, 4)
    false_alarm_score = round(max(0.0, 1.0 - false_alarm_rate), 4)

    restored = sum(
        1 for r in resolutions
        if r["verb"] == "RESTORE" and r.get("detail", {}).get("completed")
    )
    recovery = round(restored / max(1, len(ever_compromised)), 4) if ever_compromised else 1.0

    # mission success: protect the objective AND keep services up (SLA-gated).
    sla_floor = float(start.get("sla_floor", 0.8)) or 0.8
    objective_protected = 0.0 if objective_reached else 1.0
    sla_respected = min(1.0, availability / sla_floor)
    mission_success = round(objective_protected * sla_respected, 4)

    warden = {
        "mission_success": mission_success,
        "mttd_ticks": float(mttd) if mttd is not None else -1.0,
        "mttc_ticks": float(mttc) if mttc is not None else -1.0,
        "mttd_score": _norm_latency(mttd, horizon),
        "mttc_score": _norm_latency(mttc, horizon),
        "classification_precision": round(precision, 4),
        "classification_recall": round(recall, 4),
        "classification_f1": round(f1, 4),
        "availability": round(availability, 4),
        "containment_quality": containment_quality,
        "false_alarm_rate": false_alarm_rate,
        "false_alarm_score": false_alarm_score,
        "recovery": recovery,
    }

    # --- challenger metrics ------------------------------------------------
    any_foothold = bool(ever_compromised)
    any_admin = any(r["verb"] == "ESCALATE" and r.get("success") for r in resolutions)
    objective_node = start.get("objective")
    objective_foothold = objective_node in ever_compromised
    progress = (0.2 * any_foothold + 0.2 * any_admin
                + 0.3 * objective_foothold + 0.3 * objective_reached)
    progress = round(min(1.0, progress), 4)

    challenger_actions = [a for a in accepted if a.get("side") == Side.CHALLENGER.value
                          and a.get("verb") in _MALICIOUS_VERBS]
    n_actions = max(1, len(challenger_actions))
    malicious_alerts = [al for al in alerts if not al.get("benign", False)]
    detected_fraction = min(1.0, len(malicious_alerts) / n_actions)
    stealth = round(1.0 - detected_fraction, 4)
    efficiency = round(progress / (n_actions / horizon + 1e-9) if progress else 0.0, 4)
    efficiency = round(min(1.0, progress * horizon / n_actions), 4)

    challenger = {
        "objective_progress": progress,
        "stealth": stealth,
        "efficiency": efficiency,
        "actions": float(len(challenger_actions)),
    }

    # --- integrity / shared ------------------------------------------------
    total_intents = len(accepted) + len(rejected)
    rule_compliance = round(1.0 - (len(rejected) / total_intents if total_intents else 0.0), 4)

    # --- explanation faithfulness (RQ6): calibration of stated beliefs -----
    # For every Warden action that carries a structured `claim`, compare the
    # asserted belief/confidence to the ground truth reconstructed from the log.
    timeline = _compromise_timeline(resolutions)
    claims = [a for a in accepted if a.get("side") == Side.WARDEN.value and a.get("claim")]
    brier_sum = 0.0
    correct = 0
    for a in claims:
        c = a["claim"]
        node = c.get("node")
        belief = c.get("belief", "compromised")
        conf = float(c.get("confidence", 0.5))
        # probability the agent assigns to "compromised"
        if belief == "compromised":
            p = conf
        elif belief == "benign":
            p = 1.0 - conf
        else:  # suspicious / unknown
            p = 0.5
        truth_val = 1.0 if _truth_at(timeline, node, a.get("tick", 0)) else 0.0
        brier_sum += (p - truth_val) ** 2
        if (p >= 0.5) == (truth_val == 1.0):
            correct += 1
    n_claims = len(claims)
    faithfulness = round(1.0 - (brier_sum / n_claims), 4) if n_claims else 1.0
    claim_accuracy = round(correct / n_claims, 4) if n_claims else 1.0

    integrity = {
        "rule_compliance": rule_compliance,
        "violations": float(len(rejected)),
        "explanation_faithfulness": faithfulness,   # 1 - mean Brier score
        "claim_accuracy": claim_accuracy,
        "n_claims": float(n_claims),
    }

    card = ScoreCard(
        scenario_id=scenario_id, seed=seed,
        winner=end.get("winner", "draw"), ticks=ticks,
        warden=warden, challenger=challenger, integrity=integrity,
        notes={
            "ever_compromised": sorted(ever_compromised),
            "declared_compromised": sorted(declared_compromised),
            "objective_reached": objective_reached,
            "chain_valid": log.verify_chain(),
        },
    )
    # scalar readouts (penalise both sides for rule violations)
    card.warden_scalar = round(
        sum(warden[k] * w for k, w in WARDEN_WEIGHTS.items()) * rule_compliance, 4
    )
    card.challenger_scalar = round(
        sum(challenger[k] * w for k, w in CHALLENGER_WEIGHTS.items()) * rule_compliance, 4
    )
    return card
