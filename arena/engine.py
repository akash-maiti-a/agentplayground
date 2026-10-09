"""The simulation engine — the single authority on world state.

A tick is resolved as a (seeded) pure-ish function of
``(world, challenger_intent, warden_intent, rng)``. The engine is the *only*
place outcomes are decided, from abstract probability tables; it never runs
agent-supplied code and performs no I/O.

Resolution order within a tick is fixed (so results are deterministic):

1. advance ongoing multi-tick actions (restore / patch),
2. resolve the challenger's accepted intent,
3. resolve the warden's accepted intent (acting on the *previous* tick's
   observations — it cannot see this tick's alerts yet),
4. generate the telemetry stream for the warden's *next* decision
   (detection alerts from the challenger's action + benign green noise),
5. check terminal conditions,
6. emit the per-tick state hash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .rng import DeterministicRNG
from .schema import (
    BASE_SALIENCE, EventType, Intent, Side, validate_intent,
)
from .world import Exposure, Node, NodeStatus, Service, World

__all__ = ["Engine", "TickOutcome"]

RESTORE_TICKS = 3
PATCH_TICKS = 2


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class TickOutcome:
    """Everything produced by resolving one tick."""

    events: list[dict[str, Any]] = field(default_factory=list)
    # telemetry for the warden's NEXT decision (alerts + benign noise, mixed)
    warden_feed: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)  # INVESTIGATE results
    terminal: bool = False
    terminal_reason: str = ""


class Engine:
    def __init__(self, world: World, rng: DeterministicRNG, *, sensor_coverage: float = 1.0):
        self.w = world
        self.rng = rng
        self.sensor_coverage = sensor_coverage

    # --- public API --------------------------------------------------------
    def resolve_tick(self, challenger: Intent | None, warden: Intent | None) -> TickOutcome:
        out = TickOutcome()
        self.w.tick += 1
        self._advance_ongoing(out)

        c_action = self._accept(challenger, Side.CHALLENGER, out)
        w_action = self._accept(warden, Side.WARDEN, out)

        if c_action is not None:
            self._resolve_challenger(c_action, out)
        if w_action is not None:
            self._resolve_warden(w_action, out)

        self._generate_feed(c_action, out)
        self._check_terminal(out)

        out.events.append({
            "type": EventType.STATE_HASH.value,
            "tick": self.w.tick,
            "hash": self.w.state_hash(),
            "availability": self.w.availability(),
        })
        return out

    # --- acceptance / compliance ------------------------------------------
    def _accept(self, intent: Intent | None, side: Side, out: TickOutcome) -> Intent | None:
        if intent is None:
            return None
        res = validate_intent(intent)
        if not res.ok:
            out.events.append({
                "type": EventType.INTENT_REJECTED.value,
                "tick": self.w.tick, "side": side.value,
                "verb": intent.verb, "reason": res.reason,
            })
            return None
        out.events.append({
            "type": EventType.INTENT_ACCEPTED.value,
            "tick": self.w.tick, "side": side.value,
            "verb": intent.verb, "args": dict(intent.args),
        })
        return intent

    def _log_res(self, out: TickOutcome, side: Side, verb: str, success: bool,
                 detail: dict[str, Any] | None = None) -> None:
        out.events.append({
            "type": EventType.RESOLUTION.value, "tick": self.w.tick,
            "side": side.value, "verb": verb, "success": success,
            "detail": detail or {},
        })

    # --- ongoing multi-tick actions ---------------------------------------
    def _advance_ongoing(self, out: TickOutcome) -> None:
        for node in self.w.nodes.values():
            if node.status == NodeStatus.RESTORING and node.restoring_ticks > 0:
                node.restoring_ticks -= 1
                if node.restoring_ticks <= 0:
                    node.status = NodeStatus.HEALTHY
                    node.privilege = "user"
                    node.held_credentials.clear()
                    self._log_res(out, Side.WARDEN, "RESTORE", True,
                                  {"node": node.id, "completed": True})
            if node.patching:
                for svc_name in list(node.patching.keys()):
                    node.patching[svc_name] -= 1
                    if node.patching[svc_name] <= 0:
                        del node.patching[svc_name]
                        svc = node.services.get(svc_name)
                        if svc:
                            svc.auth_strength = _clamp(svc.auth_strength + 0.2)
                            svc.patch_level = _clamp(svc.patch_level + 0.2)
                            svc.config_hygiene = _clamp(svc.config_hygiene + 0.1)
                        self._log_res(out, Side.WARDEN, "PATCH", True,
                                      {"node": node.id, "service": svc_name, "completed": True})

    # --- challenger resolution --------------------------------------------
    def _resolve_challenger(self, intent: Intent, out: TickOutcome) -> None:
        verb, a = intent.verb, intent.args
        r = self.rng.stream("engine.challenger")

        if verb == "DWELL":
            self._log_res(out, Side.CHALLENGER, verb, True, {"quiet": True})
            return

        if verb == "RECON":
            revealed = self._recon(a["zone"])
            self._log_res(out, Side.CHALLENGER, verb, True,
                          {"zone": a["zone"], "revealed": revealed})
            return

        if verb == "PROBE":
            nid, sname = a["node"], a["service"]
            node = self.w.nodes.get(nid)
            ok = bool(node and nid in self.w.discovered_nodes and sname in node.services)
            if ok:
                self.w.discovered_services.add(f"{nid}/{sname}")
            self._log_res(out, Side.CHALLENGER, verb, ok, {"node": nid, "service": sname})
            return

        if verb == "ACCESS":
            self._access(a["node"], a["service"], r, out)
            return
        if verb == "ESCALATE":
            self._escalate(a["node"], r, out)
            return
        if verb == "TRAVERSE":
            self._traverse(a["from_node"], a["to_node"], r, out)
            return
        if verb == "COLLECT":
            node = self.w.nodes.get(a["node"])
            ok = bool(node and node.compromised())
            self._log_res(out, Side.CHALLENGER, verb, ok, {"node": a["node"]})
            return
        if verb == "OBJECTIVE":
            self._objective(a["node"], out)
            return

    def _recon(self, zone: str) -> list[str]:
        """Reveal nodes in a zone that are adjacent to something already held/seen."""
        frontier = set(self.w.discovered_nodes) | set(self.w.foothold_nodes())
        newly: list[str] = []
        for node in self.w.nodes_in_zone(zone):
            if node.id in self.w.discovered_nodes:
                continue
            # reachable if it neighbours a discovered node, or is a perimeter node
            neighbours = set(self.w.adjacency.get(node.id, []))
            if node.zone == "perimeter" or (neighbours & frontier):
                self.w.discovered_nodes.add(node.id)
                newly.append(node.id)
        return sorted(newly)

    def _best_weakness(self, node: Node) -> float:
        if not node.services:
            return 0.0
        return max(s.weakness() for s in node.services.values())

    def _access(self, nid: str, sname: str, r, out: TickOutcome) -> None:
        node = self.w.nodes.get(nid)
        svc = node.services.get(sname) if node else None
        reason = None
        if not node or nid not in self.w.discovered_nodes:
            reason = "node not discovered"
        elif svc is None:
            reason = "no such service"
        elif node.status == NodeStatus.ISOLATED:
            reason = "node isolated"
        elif svc.exposure != Exposure.PUBLIC and f"{nid}/{sname}" not in self.w.discovered_services:
            reason = "service not probed"
        if reason:
            self._log_res(out, Side.CHALLENGER, "ACCESS", False, {"node": nid, "reason": reason})
            return
        if node.is_decoy:
            # engaging a decoy never yields a foothold; it is a loud tell.
            self._log_res(out, Side.CHALLENGER, "ACCESS", False, {"node": nid, "decoy": True})
            return
        p = _clamp(0.15 + 0.6 * svc.weakness())
        success = r.random() < p
        if success and not node.compromised():
            node.status = NodeStatus.FOOTHOLD
            node.privilege = "user"
        self._log_res(out, Side.CHALLENGER, "ACCESS", success,
                      {"node": nid, "service": sname, "p": round(p, 3)})

    def _escalate(self, nid: str, r, out: TickOutcome) -> None:
        node = self.w.nodes.get(nid)
        if not node or not node.compromised():
            self._log_res(out, Side.CHALLENGER, "ESCALATE", False, {"node": nid, "reason": "no foothold"})
            return
        if node.privilege == "admin":
            self._log_res(out, Side.CHALLENGER, "ESCALATE", True, {"node": nid, "already": True})
            return
        p = _clamp(0.2 + 0.6 * self._best_weakness(node))
        success = r.random() < p
        if success:
            node.privilege = "admin"
            node.held_credentials.append(f"cred:{nid}")
        self._log_res(out, Side.CHALLENGER, "ESCALATE", success, {"node": nid, "p": round(p, 3)})

    def _traverse(self, from_id: str, to_id: str, r, out: TickOutcome) -> None:
        src = self.w.nodes.get(from_id)
        dst = self.w.nodes.get(to_id)
        reason = None
        if not src or not src.compromised():
            reason = "source not held"
        elif src.status == NodeStatus.ISOLATED:
            reason = "source isolated"
        elif not dst:
            reason = "no such target"
        elif to_id not in self.w.adjacency.get(from_id, []):
            reason = "not adjacent"
        elif dst.status == NodeStatus.ISOLATED:
            reason = "target isolated"
        if reason:
            self._log_res(out, Side.CHALLENGER, "TRAVERSE", False,
                          {"from": from_id, "to": to_id, "reason": reason})
            return
        base = 0.25 if src.privilege == "admin" else 0.1
        p = _clamp(base + 0.5 * self._best_weakness(dst))
        success = r.random() < p
        if success:
            self.w.discovered_nodes.add(to_id)
            if not dst.is_decoy and not dst.compromised():
                dst.status = NodeStatus.FOOTHOLD
                dst.privilege = "user"
        self._log_res(out, Side.CHALLENGER, "TRAVERSE", success,
                      {"from": from_id, "to": to_id, "p": round(p, 3),
                       "decoy": bool(dst.is_decoy)})

    def _objective(self, nid: str, out: TickOutcome) -> None:
        node = self.w.nodes.get(nid)
        ok = bool(
            node and nid == self.w.objective_node and node.compromised()
            and node.privilege == "admin" and node.status != NodeStatus.ISOLATED
        )
        if ok:
            self.w.objective_reached = True
        self._log_res(out, Side.CHALLENGER, "OBJECTIVE", ok, {"node": nid})

    # --- warden resolution -------------------------------------------------
    def _resolve_warden(self, intent: Intent, out: TickOutcome) -> None:
        verb, a = intent.verb, intent.args

        if verb == "HOLD":
            self._log_res(out, Side.WARDEN, verb, True, {})
            return

        if verb == "MONITOR":
            zone = a["zone"]
            touched = []
            for node in self.w.nodes_in_zone(zone):
                node.telemetry_fidelity = _clamp(node.telemetry_fidelity + 0.15, hi=0.98)
                touched.append(node.id)
            self._log_res(out, Side.WARDEN, verb, bool(touched), {"zone": zone, "nodes": touched})
            return

        if verb == "INVESTIGATE":
            self._investigate(a["node"], out)
            return

        if verb == "ISOLATE":
            node = self.w.nodes.get(a["node"])
            if node and node.status != NodeStatus.ISOLATED:
                node.status = NodeStatus.ISOLATED
                self._log_res(out, Side.WARDEN, verb, True, {"node": a["node"]})
            else:
                self._log_res(out, Side.WARDEN, verb, False, {"node": a["node"], "reason": "unknown or already isolated"})
            return

        if verb == "ROTATE_CREDS":
            scope = a["scope"]
            affected = []
            for node in self.w.nodes.values():
                if scope == "all" or node.zone == scope:
                    if node.held_credentials or node.privilege == "admin":
                        node.held_credentials.clear()
                        node.privilege = "user"
                        affected.append(node.id)
            self._log_res(out, Side.WARDEN, verb, bool(affected), {"scope": scope, "nodes": affected})
            return

        if verb == "PATCH":
            node = self.w.nodes.get(a["node"])
            sname = a["service"]
            ok = bool(node and sname in node.services and node.status != NodeStatus.ISOLATED)
            if ok:
                node.patching[sname] = PATCH_TICKS
            self._log_res(out, Side.WARDEN, verb, ok, {"node": a["node"], "service": sname})
            return

        if verb == "RESTORE":
            node = self.w.nodes.get(a["node"])
            ok = bool(node and node.status in (NodeStatus.FOOTHOLD, NodeStatus.ISOLATED, NodeStatus.DEGRADED))
            if ok:
                node.status = NodeStatus.RESTORING
                node.restoring_ticks = RESTORE_TICKS
            self._log_res(out, Side.WARDEN, verb, ok, {"node": a["node"]})
            return

        if verb == "DECEIVE":
            self._deceive(a["zone"], out)
            return

        if verb == "DECLARE":
            self._declare(a["node"], a["verdict"], out)
            return

    def _investigate(self, nid: str, out: TickOutcome) -> None:
        node = self.w.nodes.get(nid)
        if not node:
            self._log_res(out, Side.WARDEN, "INVESTIGATE", False, {"node": nid, "reason": "unknown"})
            return
        r = self.rng.stream("engine.investigate")
        truly = node.compromised()
        fidelity = node.telemetry_fidelity
        # higher fidelity => confidence tracks ground truth more closely
        if truly:
            confidence = _clamp(0.3 + 0.6 * fidelity + r.uniform(-0.1, 0.1))
        else:
            confidence = _clamp(0.3 - 0.2 * fidelity + r.uniform(-0.1, 0.1))
        finding = {"node": nid, "tick": self.w.tick, "confidence": round(confidence, 3),
                   "signal": "anomaly" if confidence > 0.5 else "nominal"}
        out.findings.append(finding)
        self._log_res(out, Side.WARDEN, "INVESTIGATE", True, finding)

    def _deceive(self, zone: str, out: TickOutcome) -> None:
        if zone not in self.w.zones:
            self._log_res(out, Side.WARDEN, "DECEIVE", False, {"zone": zone, "reason": "no such zone"})
            return
        existing = sum(1 for n in self.w.nodes.values() if n.is_decoy and n.zone == zone)
        did = f"decoy-{zone}-{existing:02d}"
        node = Node(id=did, zone=zone, role="decoy", value=0, telemetry_fidelity=0.95, is_decoy=True)
        node.services["portal"] = Service("portal", Exposure.INTERNAL, 0.1, 0.1, 0.1, business_critical=False)
        self.w.nodes[did] = node
        # attach to an existing node in the zone so the challenger can find it
        peers = [n.id for n in self.w.nodes_in_zone(zone) if n.id != did and not n.is_decoy]
        if peers:
            anchor = sorted(peers)[0]
            self.w.adjacency.setdefault(did, []).append(anchor)
            self.w.adjacency.setdefault(anchor, []).append(did)
        self._log_res(out, Side.WARDEN, "DECEIVE", True, {"zone": zone, "decoy": did})

    def _declare(self, nid: str, verdict: str, out: TickOutcome) -> None:
        node = self.w.nodes.get(nid)
        if not node:
            self._log_res(out, Side.WARDEN, "DECLARE", False, {"node": nid, "reason": "unknown"})
            return
        truth = "compromised" if node.compromised() else "benign"
        correct = (verdict == truth) or (verdict == "suspicious" and truth == "compromised")
        self._log_res(out, Side.WARDEN, "DECLARE", True,
                      {"node": nid, "verdict": verdict, "truth": truth, "correct": correct})

    # --- telemetry feed (detection + benign noise) ------------------------
    def _generate_feed(self, c_action: Intent | None, out: TickOutcome) -> None:
        # (1) detection alerts from the challenger's action this tick
        if c_action is not None and c_action.verb != "DWELL":
            base = BASE_SALIENCE.get(c_action.verb, 0.0)
            nid = c_action.args.get("node") or c_action.args.get("to_node") \
                or c_action.args.get("from_node")
            node = self.w.nodes.get(nid) if nid else None
            fidelity = node.telemetry_fidelity if node else 0.4  # zone-wide actions
            decoy_boost = 1.0
            if node is not None and node.is_decoy:
                decoy_boost = 2.0  # tripping a decoy is very loud
            p_detect = _clamp(base * fidelity * self.sensor_coverage * decoy_boost)
            if self.rng.chance("engine.detect", p_detect):
                alert = {
                    "type": EventType.ALERT.value, "tick": self.w.tick,
                    "node": nid, "zone": node.zone if node else c_action.args.get("zone"),
                    "strength": round(p_detect, 3), "benign": False,
                    "category": _alert_category(c_action.verb),
                }
                out.warden_feed.append(alert)
                out.events.append(alert)

        # (2) benign green noise
        r = self.rng.stream("engine.green")
        rate = _green_rate_for(self.w.scenario_id)
        n_green = _poisson_like(r, rate)
        candidates = [n.id for n in self.w.nodes.values() if not n.is_decoy]
        for _ in range(n_green):
            if not candidates:
                break
            gid = r.choice(candidates)
            node = self.w.nodes[gid]
            green = {
                "type": EventType.GREEN.value, "tick": self.w.tick, "node": gid,
                "zone": node.zone, "strength": round(r.uniform(0.1, 0.5), 3),
                "benign": True, "category": r.choice(["login", "file", "build", "mail"]),
            }
            out.warden_feed.append(green)
            out.events.append(green)

    # --- terminal conditions ----------------------------------------------
    def _check_terminal(self, out: TickOutcome) -> None:
        if self.w.objective_reached:
            out.terminal = True
            out.terminal_reason = "objective_reached"


def _alert_category(verb: str) -> str:
    return {
        "RECON": "scan", "PROBE": "scan", "ACCESS": "auth-anomaly",
        "ESCALATE": "priv-anomaly", "TRAVERSE": "lateral", "COLLECT": "staging",
        "OBJECTIVE": "impact",
    }.get(verb, "unknown")


def _green_rate_for(scenario_id: str) -> float:
    from .scenario import TIERS
    spec = TIERS.get(scenario_id)
    return spec.green_rate if spec else 1.0


def _poisson_like(r, mean: float) -> int:
    """A small, deterministic integer count with the given mean (Knuth)."""
    if mean <= 0:
        return 0
    import math
    L = math.exp(-mean)
    k, p = 0, 1.0
    while True:
        k += 1
        p *= r.random()
        if p <= L:
            return k - 1
