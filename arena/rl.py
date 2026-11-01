"""RL seam — a Gymnasium-style environment and a tabular learning Warden.

This exposes a match as a single-agent, step-able environment (the Warden
learns against a fixed Challenger policy) using the *shape* of the Gymnasium
API — ``reset(seed) -> (obs, info)`` and
``step(action) -> (obs, reward, terminated, truncated, info)`` — **without
importing gymnasium**, so the package stays dependency-free and the zero-egress
guarantee is trivially auditable. Standard RL tooling can wrap this env
unmodified (it only needs those two methods and the space descriptors).

To keep the action space fixed and RL-friendly, the Warden chooses among a
small menu of *macro actions* (hold / monitor / investigate / isolate /
declare / restore) that are decoded into concrete intents against the current
top suspect. The same feature/macro logic is shared with :class:`QWarden`, a
match-loop-compatible agent that plays a learned Q-table.

Note on rewards vs. scoring: the per-tick training reward below uses ground
truth (the env *is* the simulator). That is a training signal only — the
*evaluation* benchmark still uses the hidden, post-hoc, multi-objective
score in :mod:`arena.scoring`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .agents.challenger import RuleChallenger
from .engine import Engine
from .rng import DeterministicRNG
from .schema import AP_COST, EventType, Intent, Side
from .scenario import generate_world
from .world import NodeStatus, World

# Fixed macro-action menu.
MACRO_ACTIONS = ["HOLD", "MONITOR", "INVESTIGATE", "ISOLATE", "DECLARE", "RESTORE"]
N_ACTIONS = len(MACRO_ACTIONS)

AP_REGEN = 2
AP_CAP = 6
EVICTION_HOLD_TICKS = 5

_CATEGORY_WEIGHT = {
    "scan": 0.5, "auth-anomaly": 0.9, "priv-anomaly": 1.0, "lateral": 1.0,
    "staging": 0.7, "impact": 1.2, "unknown": 0.6,
    "login": 0.12, "file": 0.1, "build": 0.08, "mail": 0.1,
}
DECAY = 0.85


class SuspicionTracker:
    """Shared, decaying per-node suspicion derived from the telemetry feed."""

    def __init__(self) -> None:
        self.score: dict[str, float] = {}

    def ingest(self, feed: list[dict]) -> None:
        for nid in list(self.score):
            self.score[nid] *= DECAY
            if self.score[nid] < 0.02:
                del self.score[nid]
        for item in feed:
            nid = item.get("node")
            if not nid:
                continue
            w = _CATEGORY_WEIGHT.get(item.get("category", "unknown"), 0.3)
            self.score[nid] = self.score.get(nid, 0.0) + w * float(item.get("strength", 0.0)) + w * 0.4

    def ranked(self) -> list[str]:
        return [k for k, _ in sorted(self.score.items(), key=lambda kv: kv[1], reverse=True)]


@dataclass
class WardenContext:
    ranked: list[str]
    finding_conf: dict[str, float]
    isolated: set[str]
    inventory: dict[str, dict]
    zones: list[str]
    top_suspicion: float = 0.0


def discretize(obs_vec: list[float]) -> tuple:
    """Bucket a feature vector into a small discrete state key for tabular Q."""
    def b(x: float, n: int = 4) -> int:
        return max(0, min(n - 1, int(x * n)))
    return tuple(b(x) for x in obs_vec)


def features(availability: float, tracker: SuspicionTracker, isolated: set[str],
             finding_conf: dict[str, float], tick: int, horizon: int,
             n_nodes: int) -> list[float]:
    ranked = tracker.ranked()
    top1 = tracker.score.get(ranked[0], 0.0) if ranked else 0.0
    top2 = tracker.score.get(ranked[1], 0.0) if len(ranked) > 1 else 0.0
    best_conf = max(finding_conf.values(), default=0.0)
    return [
        availability,
        min(1.0, top1 / 3.0),
        min(1.0, top2 / 3.0),
        min(1.0, len(isolated) / max(1, n_nodes)),
        best_conf,
        min(1.0, tick / max(1, horizon)),
    ]


OBS_DIM = {"v1": 6, "v2": 8}


def _posterior(ctx: "WardenContext") -> dict[str, float]:
    """Calibrated P(compromised) per investigated node (v2 only)."""
    from .agents.warden import calibrated_p
    return {n: calibrated_p(c, ctx.inventory.get(n, {}).get("telemetry", 0.5))
            for n, c in ctx.finding_conf.items()}


def features_v2(availability: float, tracker: SuspicionTracker, isolated: set[str],
                finding_conf: dict[str, float], tick: int, horizon: int,
                n_nodes: int, inventory: dict[str, dict], declared: set[str]) -> list[float]:
    """v1 features plus the two signals v1's decoder was blind to: whether a
    *confirmed* (calibrated P>=0.5) node is still uncontained / undeclared."""
    base = features(availability, tracker, isolated, finding_conf, tick, horizon, n_nodes)
    from .agents.warden import calibrated_p
    post = {n: calibrated_p(c, inventory.get(n, {}).get("telemetry", 0.5))
            for n, c in finding_conf.items()}
    open_pos = max((p for n, p in post.items() if n not in isolated), default=0.0)
    undeclared = 1.0 if any(p >= 0.5 and n not in declared for n, p in post.items()) else 0.0
    base[4] = max(post.values(), default=0.0)   # calibrated, not raw, confidence
    return base + [open_pos, undeclared]


def decode_macro_v2(action: int, ctx: "WardenContext", declared: set[str]) -> Intent:
    """Target-aware decoding: each macro picks the node it makes sense for,
    instead of always acting on the top-suspicion node."""
    verb = MACRO_ACTIONS[action]
    post = _posterior(ctx)
    suspects = [n for n in ctx.ranked if n not in ctx.isolated]
    if verb == "HOLD":
        return Intent(Side.WARDEN, "HOLD", {}, "hold")
    if verb == "MONITOR":
        top = suspects[0] if suspects else None
        zone = ctx.inventory.get(top, {}).get("zone") if top else None
        zone = zone or (ctx.zones[0] if ctx.zones else "perimeter")
        return Intent(Side.WARDEN, "MONITOR", {"zone": zone}, f"monitor {zone}")
    if verb == "INVESTIGATE":
        # top suspect not yet confirmed; else sweep the least-known node
        cand = [n for n in suspects if n not in post] or suspects
        if not cand:
            cand = sorted(n for n in ctx.inventory if n not in ctx.isolated and n not in post)
        if not cand:
            return Intent(Side.WARDEN, "HOLD", {}, "nothing to investigate")
        return Intent(Side.WARDEN, "INVESTIGATE", {"node": cand[0]}, f"investigate {cand[0]}")
    if verb == "ISOLATE":
        cand = sorted(((p, n) for n, p in post.items() if p >= 0.5 and n not in ctx.isolated), reverse=True)
        if not cand:
            return Intent(Side.WARDEN, "HOLD", {}, "no confirmed node to isolate")
        p, n = cand[0]
        return Intent(Side.WARDEN, "ISOLATE", {"node": n}, f"isolate {n}",
                      claim={"node": n, "belief": "compromised", "confidence": p})
    if verb == "DECLARE":
        cand = sorted(((p, n) for n, p in post.items() if p >= 0.5 and n not in declared), reverse=True)
        if not cand:
            return Intent(Side.WARDEN, "HOLD", {}, "no confirmed node to declare")
        p, n = cand[0]
        return Intent(Side.WARDEN, "DECLARE", {"node": n, "verdict": "compromised"}, f"declare {n}",
                      claim={"node": n, "belief": "compromised", "confidence": p})
    if verb == "RESTORE":
        quiet = [n for n in sorted(ctx.isolated) if n not in ctx.ranked[:3]]
        if not quiet:
            return Intent(Side.WARDEN, "HOLD", {}, "nothing quiet to restore")
        return Intent(Side.WARDEN, "RESTORE", {"node": quiet[0]}, f"restore {quiet[0]}")
    return Intent(Side.WARDEN, "HOLD", {}, "noop")


def decode_macro(action: int, ctx: WardenContext) -> Intent:
    """Turn a macro-action index into a concrete, schema-valid Warden intent."""
    verb = MACRO_ACTIONS[action]
    top = ctx.ranked[0] if ctx.ranked else None

    if verb == "HOLD" or top is None:
        return Intent(Side.WARDEN, "HOLD", {}, "hold")
    if verb == "MONITOR":
        zone = ctx.inventory.get(top, {}).get("zone", ctx.zones[0] if ctx.zones else "perimeter")
        return Intent(Side.WARDEN, "MONITOR", {"zone": zone}, f"monitor {zone}")
    if verb == "INVESTIGATE":
        conf = min(0.99, ctx.top_suspicion / 2.0)
        return Intent(Side.WARDEN, "INVESTIGATE", {"node": top}, f"investigate {top}",
                      claim={"node": top, "belief": "compromised", "confidence": round(conf, 3)})
    if verb == "ISOLATE":
        if top in ctx.isolated:
            return Intent(Side.WARDEN, "HOLD", {}, "already isolated")
        return Intent(Side.WARDEN, "ISOLATE", {"node": top}, f"isolate {top}")
    if verb == "DECLARE":
        conf = max(ctx.finding_conf.get(top, 0.0), min(0.99, ctx.top_suspicion / 2.0))
        return Intent(Side.WARDEN, "DECLARE", {"node": top, "verdict": "compromised"},
                      f"declare {top}", claim={"node": top, "belief": "compromised", "confidence": round(conf, 3)})
    if verb == "RESTORE":
        target = sorted(ctx.isolated)[0] if ctx.isolated else top
        return Intent(Side.WARDEN, "RESTORE", {"node": target}, f"restore {target}")
    return Intent(Side.WARDEN, "HOLD", {}, "noop")


class WardenEnv:
    """Gymnasium-shaped single-agent env: the Warden vs. a fixed Challenger."""

    action_n = N_ACTIONS

    def __init__(self, scenario_id: str = "T1", sensor_coverage: float = 1.0,
                 max_ticks: int = 200, version: str = "v1"):
        self.version = version
        self.observation_dim = OBS_DIM[version]
        self._declared: set[str] = set()
        self.scenario_id = scenario_id
        self.sensor_coverage = sensor_coverage
        self.max_ticks = max_ticks
        self.world: World | None = None
        self.engine: Engine | None = None
        self.challenger: RuleChallenger | None = None
        self.tracker = SuspicionTracker()
        self._ap = 3
        self._pending_feed: list[dict] = []
        self._finding_conf: dict[str, float] = {}
        self._isolated: set[str] = set()
        self._ever_foothold = False
        self._clean = 0
        self._ap_c = 3

    # --- Gymnasium-shaped API ---------------------------------------------
    def reset(self, seed: int = 0) -> tuple[list[float], dict]:
        self.world = generate_world(self.scenario_id, seed)
        self.engine = Engine(self.world, DeterministicRNG(seed), sensor_coverage=self.sensor_coverage)
        self.challenger = RuleChallenger(seed=seed)
        self.challenger.reset()
        self.tracker = SuspicionTracker()
        self._ap = 3
        self._ap_c = 3
        self._pending_feed = []
        self._finding_conf = {}
        self._isolated = set()
        self._ever_foothold = False
        self._clean = 0
        self._declared = set()
        return self._obs(), {"tick": 0}

    def step(self, action: int) -> tuple[list[float], float, bool, bool, dict]:
        assert self.world and self.engine and self.challenger
        w, eng = self.world, self.engine

        self.tracker.ingest(self._pending_feed)
        self._ap = min(AP_CAP, self._ap + AP_REGEN)
        self._ap_c = min(AP_CAP, self._ap_c + AP_REGEN)

        ctx = self._context()
        if self.version == "v2":
            w_intent = decode_macro_v2(action, ctx, self._declared)
        else:
            w_intent = decode_macro(action, ctx)
        if AP_COST.get(w_intent.verb, 0) > self._ap:
            w_intent = Intent(Side.WARDEN, "HOLD", {}, "unaffordable")
        self._ap -= AP_COST.get(w_intent.verb, 0)
        if w_intent.verb == "ISOLATE":
            self._isolated.add(w_intent.args["node"])
        elif self.version == "v2" and w_intent.verb == "DECLARE":
            self._declared.add(w_intent.args["node"])
        elif self.version == "v2" and w_intent.verb == "RESTORE":
            self._isolated.discard(w_intent.args["node"])
            self._finding_conf.pop(w_intent.args["node"], None)

        c_obs = self._challenger_obs()
        c_intent = self.challenger.observe(c_obs, self._ap_c)
        if AP_COST.get(c_intent.verb, 0) > self._ap_c:
            c_intent = Intent(Side.CHALLENGER, "DWELL", {}, "unaffordable")
        self._ap_c -= AP_COST.get(c_intent.verb, 0)

        prev_avail = w.availability()
        outcome = eng.resolve_tick(c_intent, w_intent)
        self._pending_feed = outcome.warden_feed
        if self.version == "v2":
            # v1 skipped this, so the training opponent differed from the one
            # met in real matches (the match loop always feeds results back)
            for ev in outcome.events:
                if ev.get("type") == EventType.RESOLUTION.value and ev.get("side") == Side.CHALLENGER.value:
                    self.challenger.note_result(ev)
        for f in outcome.findings:
            self._finding_conf[f["node"]] = f["confidence"]

        reward, terminated, truncated, info = self._reward(w_intent, outcome, prev_avail)
        return self._obs(), reward, terminated, truncated, info

    # --- helpers -----------------------------------------------------------
    def _context(self) -> WardenContext:
        assert self.world
        ranked = self.tracker.ranked()
        inv = {n.id: {"zone": n.zone, "value": n.value, "telemetry": n.telemetry_fidelity}
               for n in self.world.nodes.values()}
        return WardenContext(
            ranked=ranked, finding_conf=dict(self._finding_conf), isolated=set(self._isolated),
            inventory=inv, zones=list(self.world.zones),
            top_suspicion=self.tracker.score.get(ranked[0], 0.0) if ranked else 0.0,
        )

    def _obs(self) -> list[float]:
        assert self.world
        if self.version == "v2":
            inv = {n.id: {"telemetry": n.telemetry_fidelity} for n in self.world.nodes.values()}
            return features_v2(self.world.availability(), self.tracker, self._isolated,
                               self._finding_conf, self.world.tick, self.max_ticks,
                               len(self.world.nodes), inv, self._declared)
        return features(self.world.availability(), self.tracker, self._isolated,
                        self._finding_conf, self.world.tick, self.max_ticks,
                        len(self.world.nodes))

    def _challenger_obs(self) -> dict:
        from .match import _build_challenger_obs
        return _build_challenger_obs(self.world)

    def _reward(self, w_intent: Intent, outcome, prev_avail: float):
        assert self.world
        w = self.world
        r = 0.0
        # availability shaping
        r += (w.availability() - prev_avail) * 2.0
        # containment / declaration correctness (uses ground truth: training only)
        node_id = w_intent.args.get("node")
        if node_id and node_id in w.nodes:
            truly = w.nodes[node_id].compromised() or w.nodes[node_id].status == NodeStatus.ISOLATED
            if w_intent.verb == "ISOLATE":
                r += 1.0 if truly else -1.0
            elif w_intent.verb == "DECLARE":
                r += 1.0 if truly else -1.0
        for ev in outcome.events:
            if ev.get("type") == EventType.RESOLUTION.value and ev.get("verb") == "RESTORE" \
                    and ev.get("detail", {}).get("completed"):
                r += 0.5
        active = any(n.status == NodeStatus.FOOTHOLD for n in w.nodes.values())
        if active:
            self._ever_foothold = True
            self._clean = 0
        else:
            self._clean += 1
        terminated = False
        if outcome.terminal:  # objective reached
            r -= 5.0
            terminated = True
        elif self._ever_foothold and self._clean >= EVICTION_HOLD_TICKS:
            r += 5.0
            terminated = True
        truncated = w.tick >= self.max_ticks
        return r, terminated, truncated, {"tick": w.tick, "availability": w.availability()}


# --- tabular Q-learning ----------------------------------------------------
@dataclass
class QLearner:
    alpha: float = 0.3
    gamma: float = 0.95
    epsilon: float = 0.2
    seed: int = 0
    version: str = "v1"
    q: dict[tuple, list[float]] = field(default_factory=dict)
    # per-episode (return, warden_won) -- the learning curve
    curve: list[tuple[float, bool]] = field(default_factory=list)

    def _row(self, state: tuple) -> list[float]:
        return self.q.setdefault(state, [0.0] * N_ACTIONS)

    def act(self, state: tuple, rng) -> int:
        if rng.random() < self.epsilon:
            return rng.randrange(N_ACTIONS)
        row = self._row(state)
        best = max(range(N_ACTIONS), key=lambda i: row[i])
        return best

    def greedy(self, state: tuple) -> int:
        row = self._row(state)
        return max(range(N_ACTIONS), key=lambda i: row[i])

    def update(self, s: tuple, a: int, r: float, s2: tuple, done: bool) -> None:
        row = self._row(s)
        target = r if done else r + self.gamma * max(self._row(s2))
        row[a] += self.alpha * (target - row[a])


def train_warden(scenario_id: str, episodes: int, *, seeds: list[int] | None = None,
                 learner: QLearner | None = None, epsilon: float = 0.2,
                 sensor_coverage: float = 1.0, version: str = "v1",
                 epsilon_min: float | None = None) -> QLearner:
    """Train a tabular-Q Warden across episodes. Deterministic for a given seed.

    ``version="v2"`` uses the target-aware decoder and richer features, and
    (unless ``epsilon_min`` is given) linearly decays exploration from
    ``epsilon`` to 0.02 over training. ``learner.curve`` records each episode's
    (return, warden_won) for plotting.
    """
    import random
    learner = learner or QLearner(epsilon=epsilon, version=version)
    learner.version = version
    rng = random.Random(1234)
    env = WardenEnv(scenario_id, sensor_coverage=sensor_coverage, version=version)
    eps_end = epsilon_min if epsilon_min is not None else (0.02 if version == "v2" else epsilon)
    for ep in range(episodes):
        frac = ep / max(1, episodes - 1)
        learner.epsilon = epsilon + (eps_end - epsilon) * frac
        seed = (seeds[ep % len(seeds)] if seeds else ep)
        obs, _ = env.reset(seed)
        state = discretize(obs)
        done = False
        ret = 0.0
        term = False
        while not done:
            a = learner.act(state, rng)
            obs2, r, term, trunc, _ = env.step(a)
            ret += r
            s2 = discretize(obs2)
            done = term or trunc
            learner.update(state, a, r, s2, done)
            state = s2
        won = bool(term and not env.world.objective_reached)
        learner.curve.append((round(ret, 3), won))
    return learner


class QWarden:
    """Match-loop-compatible Warden that plays a (trained) Q-table greedily."""

    side = Side.WARDEN

    def __init__(self, learner: QLearner, side: Side = Side.WARDEN, seed: int = 0):
        self.learner = learner
        self.side = side
        self.seed = seed
        self.tracker = SuspicionTracker()
        self._finding_conf: dict[str, float] = {}
        self._isolated: set[str] = set()
        self._declared: set[str] = set()

    def reset(self) -> None:
        self.tracker = SuspicionTracker()
        self._finding_conf = {}
        self._isolated = set()
        self._declared = set()

    def observe(self, obs: dict, ap_remaining: int) -> Intent:
        self.tracker.ingest(obs.get("feed", []))
        for f in obs.get("findings", []):
            self._finding_conf[f["node"]] = f["confidence"]
        ranked = self.tracker.ranked()
        v2 = getattr(self.learner, "version", "v1") == "v2"
        if v2:
            vec = features_v2(obs["availability"], self.tracker, self._isolated,
                              self._finding_conf, obs["tick"], 200, max(1, len(obs["inventory"])),
                              obs["inventory"], self._declared)
        else:
            vec = features(obs["availability"], self.tracker, self._isolated,
                           self._finding_conf, obs["tick"], 200, max(1, len(obs["inventory"])))
        state = discretize(vec)
        action = self.learner.greedy(state)
        ctx = WardenContext(
            ranked=ranked, finding_conf=dict(self._finding_conf), isolated=set(self._isolated),
            inventory=obs["inventory"], zones=obs["zones"],
            top_suspicion=self.tracker.score.get(ranked[0], 0.0) if ranked else 0.0,
        )
        intent = decode_macro_v2(action, ctx, self._declared) if v2 else decode_macro(action, ctx)
        if AP_COST.get(intent.verb, 0) > ap_remaining:
            intent = Intent(Side.WARDEN, "HOLD", {}, "unaffordable")
        if intent.verb == "ISOLATE":
            self._isolated.add(intent.args["node"])
        elif v2 and intent.verb == "DECLARE":
            self._declared.add(intent.args["node"])
        elif v2 and intent.verb == "RESTORE":
            self._isolated.discard(intent.args["node"])
            self._finding_conf.pop(intent.args["node"], None)
        return intent
