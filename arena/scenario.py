"""Scenario generator — seed -> validated fictional world.

Scenarios are *parameterized generators*, not hand-built maps, so difficulty
is tunable and results are comparable across seeds. ``scenario_id`` fixes the
*distribution* (tier parameters); ``seed`` fixes the *draw*.

Every generated world must pass :func:`validate_world` before play:
    * the objective node is reachable from a public entry point,
    * the availability SLA is satisfiable (enough business services exist),
    * every required node is connected,
    * difficulty metrics fall within the tier's declared band.
Worlds that fail validation are regenerated with a perturbed sub-seed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .rng import DeterministicRNG
from .world import Exposure, Node, NodeStatus, Service, World

__all__ = ["TIERS", "TierSpec", "generate_world", "validate_world", "WorldInvalid"]


@dataclass(frozen=True)
class TierSpec:
    scenario_id: str
    zones: tuple[str, ...]
    commons_min: int
    commons_max: int
    telemetry_lo: float      # fidelity range for noisy zones
    telemetry_hi: float
    posture_lo: float        # posture range (lower = softer = harder for warden)
    posture_hi: float
    green_rate: float        # expected benign events per tick
    sla_floor: float


TIERS: dict[str, TierSpec] = {
    # T0 — tutorial: tiny, highly observable, soft perimeter, little noise.
    "T0": TierSpec("T0", ("perimeter", "core"), 1, 2, 0.85, 0.95,
                   0.55, 0.85, 0.2, 0.75),
    # T1 — segmented: perimeter/commons/core, moderate telemetry and noise.
    "T1": TierSpec("T1", ("perimeter", "commons", "core"), 3, 6, 0.55, 0.8,
                   0.4, 0.8, 1.0, 0.8),
    # T2 — noisy enterprise: full topology, uneven telemetry, heavy noise.
    "T2": TierSpec("T2", ("perimeter", "commons", "core", "ops"), 6, 12,
                   0.3, 0.75, 0.3, 0.8, 2.5, 0.8),
}

# Fictional name pools (purely cosmetic labels).
_PERIMETER = ["gate", "relay", "portalhost"]
_CORE = [("ledger-db", 10), ("vault-kms", 10), ("directory-svc", 8)]
_OPS = [("watchtower", 6), ("forge", 7)]
_SERVICE_NAMES = ["portal", "api", "share", "mail", "console", "sync"]


class WorldInvalid(Exception):
    """Raised when a generated world fails its invariants."""


def _mk_service(rng, spec: TierSpec, name: str, exposure: Exposure,
                business: bool) -> Service:
    r = rng.stream("scenario.gen")

    def posture() -> float:
        return round(r.uniform(spec.posture_lo, spec.posture_hi), 3)

    return Service(
        name=name,
        exposure=exposure,
        auth_strength=posture(),
        patch_level=posture(),
        config_hygiene=posture(),
        business_critical=business,
    )


def _build(rng: DeterministicRNG, spec: TierSpec, seed: int) -> World:
    r = rng.stream("scenario.gen")
    w = World(scenario_id=spec.scenario_id, seed=seed, zones=list(spec.zones),
              sla_floor=spec.sla_floor)

    # Perimeter: 1-2 public entry nodes with at least one weak public service.
    n_perim = r.randint(1, 2)
    for i in range(n_perim):
        nid = f"{r.choice(_PERIMETER)}-{i:02d}"
        node = Node(id=nid, zone="perimeter", role="entry", value=2,
                    telemetry_fidelity=round(r.uniform(spec.telemetry_lo, spec.telemetry_hi), 3))
        # one public-facing service, biased soft so a match is winnable
        svc = _mk_service(rng, spec, "portal", Exposure.PUBLIC, business=True)
        # bias perimeter softer to guarantee a viable entry point
        svc.auth_strength = round(min(svc.auth_strength, spec.posture_lo + 0.15), 3)
        node.services[svc.name] = svc
        w.nodes[nid] = node

    # Commons: workstations (only in tiers that have the zone).
    if "commons" in spec.zones:
        n_common = r.randint(spec.commons_min, spec.commons_max)
        for i in range(n_common):
            nid = f"desk-{i:02d}"
            node = Node(id=nid, zone="commons", role="workstation", value=3,
                        telemetry_fidelity=round(r.uniform(spec.telemetry_lo, spec.telemetry_hi), 3))
            for sname in r.sample(_SERVICE_NAMES, k=r.randint(1, 2)):
                node.services[sname] = _mk_service(
                    rng, spec, sname, Exposure.INTERNAL, business=(r.random() < 0.3))
            w.nodes[nid] = node

    # Core: high-value crown jewels.
    core_choices = r.sample(_CORE, k=min(len(_CORE), r.randint(2, len(_CORE))))
    for name, value in core_choices:
        node = Node(id=name, zone="core", role="asset", value=value,
                    telemetry_fidelity=round(r.uniform(spec.telemetry_lo + 0.05, spec.telemetry_hi), 3))
        node.services["api"] = _mk_service(rng, spec, "api", Exposure.INTERNAL, business=True)
        w.nodes[name] = node

    # Ops (if present).
    if "ops" in spec.zones:
        for name, value in _OPS:
            node = Node(id=name, zone="ops", role="infra", value=value,
                        telemetry_fidelity=round(r.uniform(spec.telemetry_lo, spec.telemetry_hi), 3))
            node.services["console"] = _mk_service(rng, spec, "console", Exposure.INTERNAL, business=True)
            w.nodes[name] = node

    # Objective: the most valuable core asset.
    core_nodes = [n for n in w.nodes.values() if n.zone == "core"]
    w.objective_node = max(core_nodes, key=lambda n: (n.value, n.id)).id

    _wire_adjacency(rng, w)

    # The challenger starts with perimeter nodes already visible.
    for n in w.nodes.values():
        if n.zone == "perimeter":
            w.discovered_nodes.add(n.id)
    return w


def _wire_adjacency(rng: DeterministicRNG, w: World) -> None:
    """Connect zones in a layered chain: perimeter -> commons -> core/ops.

    Within a zone nodes form a light mesh; between adjacent layers a few
    cross-links are added so the objective is reachable but not trivially so.
    """
    r = rng.stream("scenario.topology")
    layer_order = [z for z in ("perimeter", "commons", "core", "ops") if z in w.zones]
    by_zone = {z: [n.id for n in w.nodes.values() if n.zone == z] for z in layer_order}

    def link(a: str, b: str) -> None:
        if b not in w.adjacency.setdefault(a, []):
            w.adjacency[a].append(b)
        if a not in w.adjacency.setdefault(b, []):
            w.adjacency[b].append(a)

    # intra-zone light mesh
    for z, ids in by_zone.items():
        for i, nid in enumerate(ids):
            if i + 1 < len(ids):
                link(nid, ids[i + 1])

    # inter-zone cross links (perimeter->next, ...->core, ...->ops)
    for lo, hi in zip(layer_order, layer_order[1:]):
        lows, highs = by_zone[lo], by_zone[hi]
        if not lows or not highs:
            continue
        k = max(1, min(len(highs), 2))
        for hid in r.sample(highs, k=k):
            link(r.choice(lows), hid)

    # ensure perimeter connects toward core even if commons/ops sit between
    if "core" in by_zone and by_zone["core"]:
        # connect any orphaned core node to the nearest previous layer
        prev_layers = [z for z in layer_order if z != "core"]
        pool = [nid for z in prev_layers for nid in by_zone[z]]
        for cid in by_zone["core"]:
            if not w.adjacency.get(cid):
                link(r.choice(pool), cid)


def _reachable_from_perimeter(w: World) -> set[str]:
    starts = [n.id for n in w.nodes.values() if n.zone == "perimeter"]
    seen: set[str] = set(starts)
    q = deque(starts)
    while q:
        cur = q.popleft()
        for nb in w.adjacency.get(cur, []):
            if nb not in seen:
                seen.add(nb)
                q.append(nb)
    return seen


def validate_world(w: World) -> None:
    """Raise :class:`WorldInvalid` if any invariant fails."""
    if not w.nodes:
        raise WorldInvalid("empty world")
    if not any(n.zone == "perimeter" for n in w.nodes.values()):
        raise WorldInvalid("no perimeter entry point")
    if not w.objective_node or w.objective_node not in w.nodes:
        raise WorldInvalid("missing objective node")
    reachable = _reachable_from_perimeter(w)
    if w.objective_node not in reachable:
        raise WorldInvalid("objective not reachable from perimeter")
    if len(reachable) != len(w.nodes):
        orphans = sorted(set(w.nodes) - reachable)
        raise WorldInvalid(f"unreachable nodes: {orphans}")
    # SLA satisfiable: there must be at least one business service, and the
    # floor must be < 1.0 so the warden has room to isolate at least one.
    biz = w.business_services()
    if not biz:
        raise WorldInvalid("no business-critical services")
    if not (0.0 < w.sla_floor < 1.0):
        raise WorldInvalid(f"sla_floor {w.sla_floor} out of (0,1)")
    # there must exist at least one viable entry (a public service soft enough)
    soft_public = [
        s for n in w.nodes.values() for s in n.services.values()
        if s.exposure == Exposure.PUBLIC and s.weakness() > 0.2
    ]
    if not soft_public:
        raise WorldInvalid("no viable public entry service")


def generate_world(scenario_id: str, seed: int, max_attempts: int = 16) -> World:
    """Generate and validate a world, perturbing the seed on failure."""
    if scenario_id not in TIERS:
        raise KeyError(f"unknown scenario {scenario_id!r}; known: {sorted(TIERS)}")
    spec = TIERS[scenario_id]
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        # perturb deterministically so a bad draw is skipped reproducibly
        rng = DeterministicRNG(seed + attempt * 1_000_003)
        w = _build(rng, spec, seed)
        try:
            validate_world(w)
            return w
        except WorldInvalid as e:  # pragma: no cover - rare with these params
            last_err = e
            continue
    raise WorldInvalid(f"could not generate valid world after {max_attempts} attempts: {last_err}")
