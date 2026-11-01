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

import math

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
                belief_conf = min(0.99, score / 2.0)
                return self._i("INVESTIGATE", {"node": nid},
                               f"investigate {nid} (suspicion {score:.2f})",
                               claim={"node": nid, "belief": "compromised", "confidence": round(belief_conf, 3)})

        # 3) Declare a confidently-found node we have not declared yet.
        for nid, conf in self.confidence.items():
            if conf >= DECLARE_CONFIDENCE and nid not in self.declared and afford("DECLARE"):
                self.declared.add(nid)
                return self._i("DECLARE", {"node": nid, "verdict": "compromised"},
                               f"declare {nid} compromised (confidence {conf:.2f})",
                               claim={"node": nid, "belief": "compromised", "confidence": round(conf, 3)})

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

    def _i(self, verb: str, args: dict, why: str, claim: dict | None = None) -> Intent:
        return Intent(side=Side.WARDEN, verb=verb, args=args, rationale=why, claim=claim or {})


# ---------------------------------------------------------------------------
# AdaptiveWarden (v2) — fidelity-aware detection.
#
# Diagnosis of the v1 Warden: the engine's INVESTIGATE returns a confidence of
# roughly ``0.3 + 0.6*f`` for a truly compromised node and ``0.3 - 0.2*f`` for
# a benign one (f = the node's telemetry fidelity, which the Warden can see in
# its inventory). A *fixed* 0.6 bar therefore misses most true compromises on
# low-fidelity tiers (T2/T3), which is why v1's classification F1 is low. The
# Bayes-optimal bar is the midpoint of the two class means, ``0.3 + 0.2*f``,
# and the gap between the classes grows with f — so v2 (a) thresholds per
# node at that midpoint, (b) raises fidelity with MONITOR before investigating
# a low-fidelity node, (c) re-investigates borderline findings, (d) reports a
# calibrated P(compromised) instead of a raw score, and (e) can plant decoys in
# deep zones where visibility is poor.
# ---------------------------------------------------------------------------

LOW_FIDELITY = 0.5         # below this, MONITOR before investigating
BORDERLINE = 0.06          # |conf - midpoint| below this => re-investigate
CALIBRATION_SCALE = 0.07   # logistic temperature for P(compromised)
_MALICIOUS_CATEGORIES = {"scan", "auth-anomaly", "priv-anomaly", "lateral", "staging", "impact", "unknown"}


def _midpoint(fidelity: float) -> float:
    return 0.3 + 0.2 * fidelity


def calibrated_p(conf: float, fidelity: float) -> float:
    """Map a raw finding confidence to a calibrated P(compromised)."""
    # the class gap shrinks with fidelity, so soften the slope when f is low
    scale = CALIBRATION_SCALE * (1.0 + (0.6 - min(fidelity, 0.6)))
    z = (conf - _midpoint(fidelity)) / scale
    return round(min(0.97, max(0.03, 1.0 / (1.0 + math.exp(-z)))), 3)


class AdaptiveWarden(RuleWarden):
    """v2 Warden: fidelity-aware thresholds, calibrated claims, optional decoys."""

    def __init__(self, side: Side = Side.WARDEN, seed: int = 0, *, use_decoys: bool = False,
                 hunt: bool = True, hunt_gap: int = 8) -> None:
        super().__init__(side, seed)
        self.use_decoys = use_decoys
        self.hunt = hunt
        self.hunt_gap = hunt_gap
        self.posterior: dict[str, float] = {}       # node -> calibrated P(compromised)
        self.monitored_zone_tick: dict[str, int] = {}
        self.decoy_zones: set[str] = set()
        self.hot: dict[str, int] = {}               # node -> tick of last malicious-category alert

    def reset(self) -> None:
        self.__init__(self.side, self.seed, use_decoys=self.use_decoys,
                      hunt=self.hunt, hunt_gap=self.hunt_gap)

    def observe(self, obs: dict, ap_remaining: int) -> Intent:
        tick = obs["tick"]
        availability = obs["availability"]
        floor = obs["sla_floor"]
        inventory = obs["inventory"]
        self._ingest(obs, tick)
        for item in obs.get("feed", []):
            if item.get("node") and item.get("category") in _MALICIOUS_CATEGORIES:
                self.hot[item["node"]] = tick

        def afford(verb: str) -> bool:
            return AP_COST[verb] <= ap_remaining

        biz_count = max(1, sum(1 for m in inventory.values() if m["business"]))

        for f in obs.get("findings", []):
            nid = f["node"]
            fid = inventory.get(nid, {}).get("telemetry", 0.5)
            self.confidence[nid] = f["confidence"]
            self.posterior[nid] = calibrated_p(f["confidence"], fid)

        ranked = sorted(self.suspicion.items(), key=lambda kv: kv[1], reverse=True)
        by_p = sorted(self.posterior.items(), key=lambda kv: kv[1], reverse=True)

        # 1) Contain a positively-classified, uncontained node (SLA-aware).
        for nid, p in by_p:
            if p < 0.5 or nid in self.isolated:
                continue
            meta = inventory.get(nid, {})
            projected = availability - ((1.0 / biz_count) if meta.get("business") else 0.0)
            crown = meta.get("value", 0) >= 8
            if (projected >= floor or crown or p >= 0.9) and afford("ISOLATE"):
                self.isolated.add(nid)
                self.restoring.discard(nid)
                return self._i("ISOLATE", {"node": nid},
                               f"contain {nid} (P={p:.2f}, crown={crown})",
                               claim={"node": nid, "belief": "compromised", "confidence": p})

        # 2) Declare a positively-classified node (cheap, raises F1 + MTTD).
        for nid, p in by_p:
            if p >= 0.5 and nid not in self.declared and afford("DECLARE"):
                self.declared.add(nid)
                return self._i("DECLARE", {"node": nid, "verdict": "compromised"},
                               f"declare {nid} compromised (P={p:.2f})",
                               claim={"node": nid, "belief": "compromised", "confidence": p})

        # 3) Investigate: strong/stale suspicion, or a borderline earlier finding.
        for nid, score in ranked:
            if score < INVESTIGATE_THRESHOLD:
                break
            if nid in self.isolated:
                continue
            meta = inventory.get(nid, {})
            fid = meta.get("telemetry", 1.0)
            zone = meta.get("zone", "perimeter")
            last = self.last_investigated.get(nid, -999)
            borderline = (nid in self.confidence
                          and abs(self.confidence[nid] - _midpoint(fid)) < BORDERLINE)
            gap = 2 if borderline else REINVESTIGATE_GAP
            if (tick - last) < gap:
                continue
            # raise visibility first on a low-fidelity node (at most every 6 ticks per zone)
            if fid < LOW_FIDELITY and tick - self.monitored_zone_tick.get(zone, -999) >= 6 \
                    and afford("MONITOR"):
                self.monitored_zone_tick[zone] = tick
                return self._i("MONITOR", {"zone": zone},
                               f"raise telemetry in {zone} before investigating {nid}")
            if afford("INVESTIGATE"):
                self.last_investigated[nid] = tick
                return self._i("INVESTIGATE", {"node": nid},
                               f"investigate {nid} (suspicion {score:.2f})")

        # 3b) Alert triage: any node named in a malicious-category alert since
        #     it was last investigated gets looked at, even if the summed
        #     suspicion is still below the v1 bar (on low-fidelity tiers one
        #     real alert rarely crosses it).
        for nid, at in sorted(self.hot.items(), key=lambda kv: -kv[1]):
            if nid in self.isolated or self.last_investigated.get(nid, -999) >= at:
                continue
            if afford("INVESTIGATE"):
                self.last_investigated[nid] = tick
                return self._i("INVESTIGATE", {"node": nid}, f"triage alert on {nid}")

        # 4) Recover isolated nodes once quiet.
        for nid in sorted(self.isolated):
            if nid in self.restoring:
                continue
            if self.suspicion.get(nid, 0.0) < 0.3 and afford("RESTORE"):
                self.restoring.add(nid)
                return self._i("RESTORE", {"node": nid}, f"recover {nid}")

        # 4b) Threat hunting with idle AP: keep a reserve for containment, then
        #     sweep the least-recently-checked nodes (high value first).
        if self.hunt and ap_remaining >= AP_COST["INVESTIGATE"] + AP_COST["ISOLATE"] + 1:
            pool = [n for n, m in inventory.items()
                    if m.get("role") != "decoy" and n not in self.isolated]
            if pool:
                nid = min(pool, key=lambda n: (self.last_investigated.get(n, -999),
                                                -inventory[n].get("value", 0), n))
                if tick - self.last_investigated.get(nid, -999) >= self.hunt_gap:
                    self.last_investigated[nid] = tick
                    return self._i("INVESTIGATE", {"node": nid}, f"hunt sweep {nid}")

        # 5) Deception: plant one decoy per deep, poorly-observed zone.
        if self.use_decoys and afford("DECEIVE") and ranked and ranked[0][1] >= 0.5:
            zone_fid: dict[str, list[float]] = {}
            for m in inventory.values():
                if m.get("role") != "decoy":
                    zone_fid.setdefault(m["zone"], []).append(m.get("telemetry", 1.0))
            for zone in ("core", "ops", "commons"):
                if zone in zone_fid and zone not in self.decoy_zones:
                    if sum(zone_fid[zone]) / len(zone_fid[zone]) < LOW_FIDELITY:
                        self.decoy_zones.add(zone)
                        return self._i("DECEIVE", {"zone": zone}, f"plant decoy in {zone}")

        # 6) Raise telemetry where suspicion is rising but visibility is low.
        for nid, score in ranked:
            if score < 0.5:
                break
            meta = inventory.get(nid, {})
            if meta.get("telemetry", 1.0) < 0.6 and afford("MONITOR"):
                zone = meta.get("zone", "perimeter")
                self.monitored_zone_tick[zone] = tick
                return self._i("MONITOR", {"zone": zone}, f"raise telemetry in {zone}")

        return self._i(NOOP, {}, "hold")
