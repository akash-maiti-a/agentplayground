"""A transparent, rule-based Warden (baseline).

The Warden must detect and contain while keeping business services available
above the SLA floor. It maintains a running *suspicion* score per node from the
(noisy, benign-mixed) telemetry feed, investigates the most suspicious node to
turn raw signal into a confidence, then declares and contains — but weighs
containment against the availability cost so it does not win by simply
isolating everything (that is exactly the degenerate strategy the scoring
penalises).

Priority ladder each tick (highest affordable action wins):

    confident + uncontained compromise   -> ISOLATE (if SLA allows) then plan RESTORE
    high suspicion, stale investigation   -> INVESTIGATE
    confident finding, undeclared         -> DECLARE compromised
    isolated + quiet                      -> RESTORE to recover availability
    suspicion rising under low telemetry  -> MONITOR the zone
    otherwise                             -> HOLD
"""

from __future__ import annotations

from ..schema import AP_COST, Intent, Side

NOOP = "HOLD"

# How much each coarse signal category contributes to suspicion.
_CATEGORY_WEIGHT = {
    "scan": 0.5, "auth-anomaly": 0.9, "priv-anomaly": 1.0, "lateral": 1.0,
    "staging": 0.7, "impact": 1.2, "unknown": 0.6,
    # benign-looking categories still carry a little weight (false-alarm pressure)
    "login": 0.12, "file": 0.1, "build": 0.08, "mail": 0.1,
}

DECAY = 0.85
INVESTIGATE_THRESHOLD = 0.9
DECLARE_CONFIDENCE = 0.6
REINVESTIGATE_GAP = 4


class RuleWarden:
    side = Side.WARDEN

    def __init__(self, side: Side = Side.WARDEN, seed: int = 0) -> None:
        self.side = side
        self.seed = seed
        self.suspicion: dict[str, float] = {}
        self.last_investigated: dict[str, int] = {}
        self.confidence: dict[str, float] = {}     # node -> last finding confidence
        self.declared: set[str] = set()
        self.isolated: set[str] = set()            # our own containment memory
        self.restoring: set[str] = set()

    def reset(self) -> None:
        self.__init__(self.side, self.seed)

    def observe(self, obs: dict, ap_remaining: int) -> Intent:
        tick = obs["tick"]
        availability = obs["availability"]
        floor = obs["sla_floor"]
        inventory = obs["inventory"]

        self._ingest(obs, tick)

        def afford(verb: str) -> bool:
            return AP_COST[verb] <= ap_remaining

        biz_count = max(1, sum(1 for m in inventory.values() if m["business"]))

        # update findings memory
        for f in obs.get("findings", []):
            self.confidence[f["node"]] = f["confidence"]

        ranked = sorted(self.suspicion.items(), key=lambda kv: kv[1], reverse=True)

        # 1) Contain a confidently-compromised, uncontained node (SLA-aware).
        for nid, conf in sorted(self.confidence.items(), key=lambda kv: kv[1], reverse=True):
            if conf < DECLARE_CONFIDENCE or nid in self.isolated:
                continue
            meta = inventory.get(nid, {})
            projected = availability - ((1.0 / biz_count) if meta.get("business") else 0.0)
            crown = meta.get("value", 0) >= 8
            if (projected >= floor or crown) and afford("ISOLATE"):
                self.isolated.add(nid)
                return self._i("ISOLATE", {"node": nid},
                               f"contain {nid} (confidence {conf:.2f}, crown={crown})")

        # 2) Investigate the most suspicious node if its signal is strong/stale.
        for nid, score in ranked:
            if score < INVESTIGATE_THRESHOLD:
                break
            stale = (tick - self.last_investigated.get(nid, -999)) >= REINVESTIGATE_GAP
            if stale and afford("INVESTIGATE"):
                self.last_investigated[nid] = tick
                return self._i("INVESTIGATE", {"node": nid},
                               f"investigate {nid} (suspicion {score:.2f})")

        # 3) Declare a confidently-found node we have not declared yet.
        for nid, conf in self.confidence.items():
            if conf >= DECLARE_CONFIDENCE and nid not in self.declared and afford("DECLARE"):
                self.declared.add(nid)
                return self._i("DECLARE", {"node": nid, "verdict": "compromised"},
                               f"declare {nid} compromised (confidence {conf:.2f})")

        # 4) Recover: restore an isolated node once things are quiet, to regain uptime.
        for nid in sorted(self.isolated):
            if nid in self.restoring:
                continue
            if self.suspicion.get(nid, 0.0) < 0.3 and afford("RESTORE"):
                self.restoring.add(nid)
                return self._i("RESTORE", {"node": nid}, f"recover {nid}")

        # 5) Raise telemetry where suspicion is rising but visibility is low.
        for nid, score in ranked:
            if score < 0.5:
                break
            meta = inventory.get(nid, {})
            if meta.get("telemetry", 1.0) < 0.6 and afford("MONITOR"):
                return self._i("MONITOR", {"zone": meta.get("zone", "perimeter")},
                               f"raise telemetry in {meta.get('zone')}")

        return self._i(NOOP, {}, "hold")

    # --- helpers -----------------------------------------------------------
    def _ingest(self, obs: dict, tick: int) -> None:
        # decay all suspicion, then add this tick's feed
        for nid in list(self.suspicion):
            self.suspicion[nid] *= DECAY
            if self.suspicion[nid] < 0.02:
                del self.suspicion[nid]
        for item in obs.get("feed", []):
            nid = item.get("node")
            if not nid:
                continue
            w = _CATEGORY_WEIGHT.get(item.get("category", "unknown"), 0.3)
            self.suspicion[nid] = self.suspicion.get(nid, 0.0) + w * float(item.get("strength", 0.0)) + w * 0.4

    def _i(self, verb: str, args: dict, why: str) -> Intent:
        return Intent(side=Side.WARDEN, verb=verb, args=args, rationale=why)
