"""A transparent, rule-based Challenger (baseline).

This is a *challenge generator*, not an attack tool: it chooses among abstract
intent categories to apply pressure the Warden can learn to detect. Its policy
is a simple, interpretable priority ladder:

    hold objective (admin)  ->  OBJECTIVE
    foothold on objective   ->  ESCALATE to admin
    objective adjacent to an admin foothold -> TRAVERSE toward it
    softest reachable service -> PROBE then ACCESS, then ESCALATE/expand
    nothing better to do     -> RECON to reveal more of the map, else DWELL

Everything is driven by the observation the engine exposes; the agent holds a
little memory to avoid retrying the same dead end every tick.
"""

from __future__ import annotations

from ..schema import AP_COST, Intent, Side

NOOP = "DWELL"


class RuleChallenger:
    side = Side.CHALLENGER

    _ZONE_ORDER = ("perimeter", "commons", "core", "ops")

    def __init__(self, side: Side = Side.CHALLENGER, seed: int = 0) -> None:
        self.side = side
        self.seed = seed
        self._probed: set[str] = set()
        self._access_fail: dict[str, int] = {}
        self._recon_idx = 0

    def reset(self) -> None:
        self._probed.clear()
        self._access_fail.clear()
        self._recon_idx = 0

    def observe(self, obs: dict, ap_remaining: int) -> Intent:
        objective = obs["objective"]
        nodes = obs["nodes"]                 # id -> {zone, neighbors:[...]}
        footholds = obs["footholds"]         # id -> {privilege}
        known_services = obs["services_known"]   # "node/svc" -> weakness
        public = obs["public_services"]      # node -> [service names]

        def afford(verb: str) -> bool:
            return AP_COST[verb] <= ap_remaining

        # 1) Win if we already own the objective with admin.
        if objective in footholds and footholds[objective]["privilege"] == "admin":
            if afford("OBJECTIVE"):
                return self._i("OBJECTIVE", {"node": objective},
                               "objective held with admin; assert win")

        # 2) Escalate on the objective if we have a foothold but only 'user'.
        if objective in footholds and footholds[objective]["privilege"] == "user":
            if afford("ESCALATE"):
                return self._i("ESCALATE", {"node": objective}, "escalate on objective")

        # 3) Move toward the objective if it is adjacent to an admin foothold.
        for fid, meta in footholds.items():
            if meta["privilege"] != "admin":
                continue
            neigh = nodes.get(fid, {}).get("neighbors", [])
            if objective in neigh:
                if afford("TRAVERSE"):
                    return self._i("TRAVERSE", {"from_node": fid, "to_node": objective},
                                   "traverse into objective")

        # 4) Expand footholds: escalate a user-level foothold to open traversal.
        for fid, meta in footholds.items():
            if meta["privilege"] == "user" and afford("ESCALATE"):
                return self._i("ESCALATE", {"node": fid}, f"escalate on {fid} to enable lateral move")

        # 5) Traverse from any admin foothold to an un-held discovered neighbour.
        for fid, meta in footholds.items():
            if meta["privilege"] != "admin":
                continue
            for nb in nodes.get(fid, {}).get("neighbors", []):
                if nb not in footholds and afford("TRAVERSE"):
                    return self._i("TRAVERSE", {"from_node": fid, "to_node": nb},
                                   f"expand laterally to {nb}")

        # 6) Gain an initial foothold on the softest reachable public/known service.
        target = self._pick_access_target(nodes, footholds, known_services, public)
        if target is not None:
            nid, svc, probed = target
            if not probed and afford("PROBE"):
                self._probed.add(f"{nid}/{svc}")
                return self._i("PROBE", {"node": nid, "service": svc}, f"probe {nid}/{svc}")
            if afford("ACCESS"):
                return self._i("ACCESS", {"node": nid, "service": svc}, f"attempt access on {nid}/{svc}")

        # 7) Reveal more of the map.
        unexplored = self._zone_to_recon(obs)
        if unexplored and afford("RECON"):
            return self._i("RECON", {"zone": unexplored}, f"recon {unexplored}")

        # 8) Nothing affordable/useful: stay quiet.
        return self._i(NOOP, {}, "dwell")

    # --- helpers -----------------------------------------------------------
    def _pick_access_target(self, nodes, footholds, known_services, public):
        """Return (node, service, already_probed) for the softest target, or None.

        Targets that have failed repeatedly are de-prioritised but never
        permanently abandoned (a weak public service always has a non-zero
        success chance, so persistence eventually pays off).
        """
        best = None  # (score, node, svc, probed)
        for nid, meta in nodes.items():
            if nid in footholds:
                continue
            svc_names = set(public.get(nid, []))
            for key in known_services:
                n, s = key.split("/", 1)
                if n == nid:
                    svc_names.add(s)
            for svc in svc_names:
                key = f"{nid}/{svc}"
                weakness = known_services.get(key, 0.5)  # 0.5 = unknown guess
                probed = key in self._probed or key in known_services
                # soft penalty for repeated failures instead of a hard ban
                score = weakness - 0.05 * self._access_fail.get(key, 0)
                cand = (score, nid, svc, probed)
                if best is None or cand[0] > best[0]:
                    best = cand
        if best is None:
            return None
        return best[1], best[2], best[3]

    def _zone_to_recon(self, obs) -> str | None:
        """Recon the next layer beyond our deepest presence, then rotate.

        The challenger cannot see undiscovered nodes, so it reveals the zone
        one step deeper than its deepest discovered node; if that yields
        nothing new over time it rotates through all zones to catch stragglers.
        """
        zones = [z for z in self._ZONE_ORDER if z in obs["zones"]]
        if not zones:
            return None
        rank = {z: i for i, z in enumerate(self._ZONE_ORDER)}
        deepest = max((rank.get(m["zone"], 0) for m in obs["nodes"].values()), default=0)
        # prefer the next layer in
        for z in zones:
            if rank[z] == min(deepest + 1, max(rank[zz] for zz in zones)):
                target = z
                break
        else:
            target = zones[0]
        # rotate occasionally so we also re-scan shallower zones for new links
        self._recon_idx = (self._recon_idx + 1) % (len(zones) + 1)
        if self._recon_idx == len(zones):
            return zones[deepest % len(zones)]
        return target

    def note_result(self, event: dict) -> None:
        """Optional feedback hook the match loop may call with resolution events."""
        if event.get("verb") == "ACCESS" and not event.get("success"):
            d = event.get("detail", {})
            key = f"{d.get('node')}/{d.get('service')}"
            self._access_fail[key] = self._access_fail.get(key, 0) + 1

    def _i(self, verb: str, args: dict, why: str) -> Intent:
        return Intent(side=Side.CHALLENGER, verb=verb, args=args, rationale=why)
