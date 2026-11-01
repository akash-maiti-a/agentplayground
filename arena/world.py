"""The world model — fictional, fully synthetic state.

Nothing here corresponds to real software. A "service" has a ``name`` (a
label) and a ``posture`` of three numeric attributes in [0, 1]. A "weakness"
is just a low posture number that raises the success probability of an abstract
intent. Credentials are opaque reference strings. All of it is generated per
seed by :mod:`arena.scenario`.

The :meth:`World.state_hash` method produces a canonical content hash of the
full state; the determinism gate compares these across runs and machines.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any

__all__ = ["NodeStatus", "Exposure", "Service", "Node", "World"]


class NodeStatus(str, enum.Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FOOTHOLD = "foothold"        # challenger has a foothold here
    ISOLATED = "isolated"        # warden has severed this node
    RESTORING = "restoring"      # warden is recovering this node


class Exposure(str, enum.Enum):
    PUBLIC = "public"
    INTERNAL = "internal"


@dataclass
class Service:
    name: str
    exposure: Exposure
    # Posture attributes in [0, 1]; higher = more hardened.
    auth_strength: float
    patch_level: float
    config_hygiene: float
    business_critical: bool = False  # counts toward the availability SLA

    def weakness(self) -> float:
        """Aggregate softness in [0, 1]; higher means easier to act against."""
        hardened = (self.auth_strength + self.patch_level + self.config_hygiene) / 3.0
        return round(1.0 - hardened, 6)


@dataclass
class Node:
    id: str
    zone: str
    role: str
    value: int                      # 0..10 asset value (crown jewels are high)
    services: dict[str, Service] = field(default_factory=dict)
    privilege: str = "user"         # "user" | "admin" (challenger's level here)
    held_credentials: list[str] = field(default_factory=list)
    telemetry_fidelity: float = 0.5  # 0..1 observability to the warden
    status: NodeStatus = NodeStatus.HEALTHY
    is_decoy: bool = False          # warden-placed deception
    # ground truth survives containment: an isolated foothold is still a
    # compromised host until it is restored (matches scoring's timeline)
    contained_foothold: bool = False
    # engine bookkeeping for multi-tick actions (ticks remaining)
    restoring_ticks: int = 0
    patching: dict[str, int] = field(default_factory=dict)  # service -> ticks

    def compromised(self) -> bool:
        return self.status == NodeStatus.FOOTHOLD

    def truly_compromised(self) -> bool:
        """Ground truth for verdicts: an active *or contained* foothold."""
        return self.compromised() or self.contained_foothold


@dataclass
class World:
    """Full ground-truth state of one match."""

    scenario_id: str
    seed: int
    tick: int = 0
    zones: list[str] = field(default_factory=list)
    nodes: dict[str, Node] = field(default_factory=dict)
    adjacency: dict[str, list[str]] = field(default_factory=dict)  # node -> neighbours
    objective_node: str = ""        # the node the challenger must reach
    sla_floor: float = 0.8          # min fraction of business services up
    # discovery state (what the challenger has revealed)
    discovered_nodes: set[str] = field(default_factory=set)
    discovered_services: set[str] = field(default_factory=set)  # "node/service"
    # terminal flags
    objective_reached: bool = False

    # --- queries -----------------------------------------------------------
    def nodes_in_zone(self, zone: str) -> list[Node]:
        return [n for n in self.nodes.values() if n.zone == zone]

    def business_services(self) -> list[tuple[str, Service]]:
        out = []
        for n in self.nodes.values():
            for s in n.services.values():
                if s.business_critical:
                    out.append((n.id, s))
        return out

    def availability(self) -> float:
        """Fraction of business-critical services currently 'up'.

        A business service is down if its node is isolated or restoring.
        """
        biz = self.business_services()
        if not biz:
            return 1.0
        up = 0
        for node_id, _svc in biz:
            node = self.nodes[node_id]
            if node.status not in (NodeStatus.ISOLATED, NodeStatus.RESTORING):
                up += 1
        return round(up / len(biz), 6)

    def foothold_nodes(self) -> list[str]:
        return [n.id for n in self.nodes.values() if n.compromised()]

    # --- canonical hashing -------------------------------------------------
    def _canonical(self) -> dict[str, Any]:
        """A deterministic, order-stable dict of the full state."""
        def node_dict(n: Node) -> dict[str, Any]:
            d = asdict(n)
            d["status"] = n.status.value
            d["services"] = {
                k: {**asdict(v), "exposure": v.exposure.value}
                for k, v in sorted(n.services.items())
            }
            d["held_credentials"] = sorted(n.held_credentials)
            d["patching"] = dict(sorted(n.patching.items()))
            return d

        return {
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "tick": self.tick,
            "zones": list(self.zones),
            "nodes": {k: node_dict(self.nodes[k]) for k in sorted(self.nodes)},
            "adjacency": {k: sorted(self.adjacency.get(k, [])) for k in sorted(self.adjacency)},
            "objective_node": self.objective_node,
            "sla_floor": self.sla_floor,
            "discovered_nodes": sorted(self.discovered_nodes),
            "discovered_services": sorted(self.discovered_services),
            "objective_reached": self.objective_reached,
        }

    def state_hash(self) -> str:
        blob = json.dumps(self._canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
