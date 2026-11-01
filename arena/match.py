"""The match loop — ties engine, agents, AP economy, control plane and log.

Each tick:
    1. the control plane is checked (emergency stop / resource ceilings),
    2. both sides regenerate action points (capped),
    3. observations are built and filtered per side (asymmetric, partial),
    4. each agent proposes one intent; unaffordable intents become no-ops,
    5. the engine resolves the tick deterministically,
    6. the telemetry feed + findings are carried to the Warden's next decision,
    7. terminal conditions are evaluated (objective / eviction / limits).

The whole match is reproducible from ``(scenario_id, seed)`` plus the agents'
(also seeded) policies — the determinism gate relies on this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .control import ControlPlane, ResourceLimits
from .engine import Engine
from .log import MatchLog
from .rng import DeterministicRNG
from .schema import AP_COST, EventType, Intent, Side
from .scenario import generate_world, tier_sensor_coverage
from .world import Exposure, NodeStatus, World

AP_START = 3
AP_REGEN = 2
AP_CAP = 6
EVICTION_HOLD_TICKS = 5


class Policy(Protocol):
    side: Side
    def observe(self, obs: dict, ap_remaining: int) -> Intent: ...


@dataclass
class MatchResult:
    match_id: str
    scenario_id: str
    seed: int
    ticks: int
    winner: str                 # "warden" | "challenger" | "draw"
    reason: str
    log: MatchLog
    final_hash: str
    availability_final: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


def _build_challenger_obs(w: World) -> dict:
    nodes: dict[str, dict] = {}
    for nid in sorted(w.discovered_nodes):
        node = w.nodes.get(nid)
        if not node:
            continue
        # the challenger only sees discovered neighbours
        neigh = [n for n in w.adjacency.get(nid, []) if n in w.discovered_nodes]
        nodes[nid] = {"zone": node.zone, "neighbors": sorted(neigh)}
    footholds = {
        n.id: {"privilege": n.privilege}
        for n in w.nodes.values()
        if n.compromised() and n.status != NodeStatus.ISOLATED
    }
    public_services: dict[str, list[str]] = {}
    services_known: dict[str, float] = {}
    for nid in sorted(w.discovered_nodes):
        node = w.nodes.get(nid)
        if not node:
            continue
        pub = [s.name for s in node.services.values() if s.exposure == Exposure.PUBLIC]
        if pub:
            public_services[nid] = sorted(pub)
        for sname, svc in node.services.items():
            key = f"{nid}/{sname}"
            if key in w.discovered_services:
                services_known[key] = svc.weakness()
    return {
        "tick": w.tick,
        "objective": w.objective_node,
        "zones": list(w.zones),
        "nodes": nodes,
        "footholds": footholds,
        "public_services": public_services,
        "services_known": services_known,
    }


def _build_warden_obs(w: World, feed: list[dict], findings: list[dict]) -> dict:
    inventory = {
        n.id: {
            "zone": n.zone, "role": n.role, "value": n.value,
            "business": any(s.business_critical for s in n.services.values()),
            "telemetry": n.telemetry_fidelity,
        }
        for n in w.nodes.values()
    }
    # the warden feed hides the ground-truth 'benign' flag — it must infer.
    masked_feed = [
        {"tick": it["tick"], "node": it.get("node"), "zone": it.get("zone"),
         "strength": it.get("strength"), "category": it.get("category")}
        for it in feed
    ]
    return {
        "tick": w.tick,
        "availability": w.availability(),
        "sla_floor": w.sla_floor,
        "inventory": inventory,
        "feed": masked_feed,
        "findings": findings,
        "zones": list(w.zones),
    }


def _affordable(intent: Intent, ap: int) -> bool:
    return AP_COST.get(intent.verb, 99) <= ap


def run_match(
    scenario_id: str,
    seed: int,
    challenger: Policy,
    warden: Policy,
    *,
    limits: ResourceLimits | None = None,
    control: ControlPlane | None = None,
    sensor_coverage: float | None = None,
    match_id: str | None = None,
) -> MatchResult:
    world = generate_world(scenario_id, seed)
    rng = DeterministicRNG(seed)
    if sensor_coverage is None:
        sensor_coverage = tier_sensor_coverage(scenario_id)
    engine = Engine(world, rng, sensor_coverage=sensor_coverage)
    control = control or ControlPlane(limits)
    mid = match_id or f"{scenario_id}-{seed}"
    log = MatchLog(match_id=mid)

    log.append({
        "type": EventType.MATCH_START.value, "match_id": mid,
        "scenario_id": scenario_id, "seed": seed, "sla_floor": world.sla_floor,
        "objective": world.objective_node, "schema_version": "1.0.0",
        "initial_hash": world.state_hash(),
    })

    challenger.reset() if hasattr(challenger, "reset") else None
    warden.reset() if hasattr(warden, "reset") else None

    ap_c, ap_w = AP_START, AP_START
    pending_feed: list[dict] = []
    pending_findings: list[dict] = []
    ever_foothold = False
    clean_streak = 0
    winner, reason = "draw", "unresolved"

    while True:
        halt = control.check(tick=world.tick, node_count=len(world.nodes))
        if halt is not None:
            # a limit/stop with no objective reached is a defender survival
            winner = "challenger" if world.objective_reached else "warden"
            reason = halt
            log.append({"type": EventType.EMERGENCY_STOP.value
                        if halt.startswith("emergency_stop") else EventType.MATCH_END.value,
                        "tick": world.tick, "reason": halt, "winner": winner})
            break

        ap_c = min(AP_CAP, ap_c + AP_REGEN)
        ap_w = min(AP_CAP, ap_w + AP_REGEN)

        c_obs = _build_challenger_obs(world)
        w_obs = _build_warden_obs(world, pending_feed, pending_findings)

        c_intent = challenger.observe(c_obs, ap_c)
        w_intent = warden.observe(w_obs, ap_w)

        if not _affordable(c_intent, ap_c):
            c_intent = Intent(Side.CHALLENGER, "DWELL", {}, "unaffordable->dwell")
        if not _affordable(w_intent, ap_w):
            w_intent = Intent(Side.WARDEN, "HOLD", {}, "unaffordable->hold")

        ap_c -= AP_COST.get(c_intent.verb, 0)
        ap_w -= AP_COST.get(w_intent.verb, 0)

        log.append({"type": EventType.TICK.value, "tick": world.tick + 1,
                    "ap_challenger": ap_c, "ap_warden": ap_w})

        outcome = engine.resolve_tick(c_intent, w_intent)
        log.extend(outcome.events)

        # feed resolution results back to agents that want them (optional hooks)
        if hasattr(challenger, "note_result"):
            for ev in outcome.events:
                if ev.get("type") == EventType.RESOLUTION.value and ev.get("side") == Side.CHALLENGER.value:
                    challenger.note_result(ev)

        pending_feed = outcome.warden_feed
        pending_findings = outcome.findings

        # eviction tracking
        active = [n for n in world.nodes.values() if n.status == NodeStatus.FOOTHOLD]
        if active:
            ever_foothold = True
            clean_streak = 0
        else:
            clean_streak += 1

        if outcome.terminal:
            winner = "challenger"
            reason = outcome.terminal_reason
            log.append({"type": EventType.MATCH_END.value, "tick": world.tick,
                        "reason": reason, "winner": winner})
            break

        if ever_foothold and clean_streak >= EVICTION_HOLD_TICKS:
            winner = "warden"
            reason = "eviction_hold"
            log.append({"type": EventType.MATCH_END.value, "tick": world.tick,
                        "reason": reason, "winner": winner})
            break

    final_hash = world.state_hash()
    return MatchResult(
        match_id=mid, scenario_id=scenario_id, seed=seed, ticks=world.tick,
        winner=winner, reason=reason, log=log, final_hash=final_hash,
        availability_final=world.availability(),
        extra={"ever_foothold": ever_foothold},
    )
