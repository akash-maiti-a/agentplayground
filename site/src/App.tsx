import { useEffect, useMemo, useState } from "react";
import {
  AblationChart, ActionMix, AgentName, AxisHeatmap, CIBar, EloChart, RLCurve, TierChart, WinMatrix,
} from "./charts";
import Replay from "./Replay";
import { AGENT_COLOR, AgentMeta, Results, Row, fmt, pct } from "./types";

/** Measured on the pre-fix commit (7ecdf6c) — these runs cannot be regenerated
 *  from the fixed code, so they are recorded here with their provenance. */
const PRE_FIX = {
  commit: "7ecdf6c",
  rlWinrateByHashSeed: [
    { seed: "unset (first run)", winrate: 0.15 },
    { seed: "1", winrate: 0.5 },
    { seed: "2", winrate: 0.9 },
    { seed: "3", winrate: 0.75 },
  ],
  /** MTTD / MTTC score from the first v0.2 export, before the engine/scorer fix. */
  timing: { rule: ["0.10", "0.01"], adaptive: ["0.04", "0.00"] },
};

const ORDER = ["q_v2", "adaptive", "q_v1", "rule", "declare_all", "passive", "isolate_all"];

function useTheme() {
  const [theme, setTheme] = useState<string | null>(() => {
    try { return localStorage.getItem("arena-theme"); } catch { return null; }
  });
  useEffect(() => {
    const el = document.documentElement;
    if (theme) el.setAttribute("data-theme", theme); else el.removeAttribute("data-theme");
    try { theme ? localStorage.setItem("arena-theme", theme) : localStorage.removeItem("arena-theme"); } catch { /* ignore */ }
  }, [theme]);
  const isLight = theme ? theme === "light" : window.matchMedia?.("(prefers-color-scheme: light)").matches;
  return { isLight, toggle: () => setTheme(isLight ? "dark" : "light") };
}

function Tile({ label, from, to, fmtv = (v: number) => fmt(v), note }: {
  label: string; from: number; to: number; fmtv?: (v: number) => string; note?: string;
}) {
  const d = to - from;
  return (
    <div className="tile">
      <div className="lbl">{label}</div>
      <div className="val">{fmtv(to)} <span className="from">from {fmtv(from)}</span></div>
      <div className={`delta ${Math.abs(d) < 0.005 ? "flat" : ""}`}>{d >= 0 ? "▲" : "▼"} {fmtv(Math.abs(d))} {note ?? "vs v1"}</div>
    </div>
  );
}

export default function App() {
  const [data, setData] = useState<Results | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [chal, setChal] = useState("rule");
  const { isLight, toggle } = useTheme();

  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}data/results.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setData)
      .catch((e) => setErr(String(e)));
  }, []);

  const meta = useMemo<Record<string, AgentMeta>>(
    () => Object.fromEntries([...(data?.wardens ?? []), ...(data?.challengers ?? []).map((c) => ({ ...c, key: `c:${c.key}` }))]
      .map((m) => [m.key, m])), [data]);

  const nav = (
    <div className="topbar">
      <nav className="wrap">
        <a className="brand" href="#top" style={{ textDecoration: "none" }}><span className="dot" />ARENA results</a>
        <div className="navlinks">
          <a href="#findings">Findings</a>
          <a href="#leaderboard" className="hide-sm">Leaderboard</a>
          <a href="#rl" className="hide-sm">RL</a>
          <a href="#replay" className="hide-sm">Replay</a>
          <a href="#method" className="hide-sm">Method</a>
          <button className="theme-btn" onClick={toggle} aria-label="Toggle color theme">{isLight ? "◐ Dark" : "◑ Light"}</button>
        </div>
      </nav>
    </div>
  );

  if (err) return <>{nav}<div className="wrap loading">Could not load results.json ({err}).</div></>;
  if (!data) return <>{nav}<div className="wrap loading">Loading results…</div></>;

  const rows = data.leaderboard.rows;
  const R = (w: string, c = "rule") => rows.find((r) => r.warden === w && r.challenger === c)!;
  const rule = R("rule"), adaptive = R("adaptive"), q1 = R("q_v1"), q2 = R("q_v2");
  const ruleS = R("rule", "stealth"), adaptiveS = R("adaptive", "stealth");
  const degenerate = ["passive", "isolate_all", "declare_all"];
  const bestDegen = rows.filter((r) => degenerate.includes(r.warden)).sort((a, b) => b.scalar - a.scalar)[0];
  const worstReal = rows.filter((r) => !degenerate.includes(r.warden)).sort((a, b) => a.scalar - b.scalar)[0];
  const holdRule = data.action_mix.rule?.HOLD ?? 0, holdAdaptive = data.action_mix.adaptive?.HOLD ?? 0;
  const m = data.manifest;
  const nPerCell = rule.n;
  const tableRows = ORDER.map((w) => rows.find((r) => r.warden === w && r.challenger === chal)).filter(Boolean) as Row[];
  const ablT3 = data.rl.ablation.filter((a) => a.tier === "T2" && a.episodes === 400);

  const ChalToggle = (
    <div className="seg" role="group" aria-label="Opponent">
      {data.challengers.map((c) => (
        <button key={c.key} aria-pressed={chal === c.key} onClick={() => setChal(c.key)}>vs {c.label}</button>
      ))}
    </div>
  );

  return (
    <>
      {nav}
      <header className="hero" id="top">
        <div className="wrap">
          <span className="eyebrow">experiment · v1 baselines vs v2 agents · {nPerCell * data.wardens.length * data.challengers.length} scored matches</span>
          <h1>What changed when we fixed the range,<br />and taught the defender to <span className="accent">look before it holds.</span></h1>
          <p className="sub">
            ARENA pits a constrained <b>Challenger</b> against a detect-and-respond <b>Warden</b> inside a deterministic,
            schema-validated simulation with no real shells, sockets or CVEs. We found a hidden determinism bug,
            diagnosed why the defenders under-performed, shipped v2 agents, and re-ran every measurement on held-out seeds.
          </p>
          <div className="tiles">
            <Tile label="Adaptive Warden · classification F1" from={rule.axes.classification_f1} to={adaptive.axes.classification_f1} />
            <Tile label="Adaptive Warden · scalar score" from={rule.scalar} to={adaptive.scalar} />
            <Tile label="Q-Warden v2 · scalar score" from={q1.scalar} to={q2.scalar} />
            <Tile label="Win rate vs Stealth Challenger" from={ruleS.winrate} to={adaptiveS.winrate} fmtv={pct} note="rule → adaptive" />
          </div>
          <div className="meta-line">
            arena {m.arena_version} · tiers {m.tiers.join("/")} · held-out seeds {m.eval_seeds[0]}–{m.eval_seeds[1]} ({m.eval_seeds[2]} per tier) ·
            generated {m.generated_at ?? "—"}
          </div>
        </div>
      </header>

      <section id="findings">
        <div className="wrap">
          <div className="kicker">Findings</div>
          <h2>Seven things the experiment showed.</h2>
          <p className="lead">Every number below is read from <code className="inline">results.json</code>, produced by one command. Bootstrap 95% intervals are on the leaderboard.</p>
          <div className="grid2">
            <div className="card finding">
              <span className="n">01</span>
              <span className="tag bug">bug · fixed</span>
              <h3>“Deterministic” results changed between processes</h3>
              <p>
                The match loop built the Challenger's observation by iterating a Python <code className="inline">set</code> of node ids, so tie-breaks
                followed per-process string hashing. Same seeds and code, but the original RL experiment gave a Q-Warden win rate of{" "}
                <b className="num">{PRE_FIX.rlWinrateByHashSeed.map((r) => pct(r.winrate)).join(", ")}</b> depending only on{" "}
                <code className="inline">PYTHONHASHSEED</code>. The existing gate compared two runs <em>inside one process</em>, so it never saw this.
                Fix: sort before iterating. A new test re-runs a batch under three hash seeds and requires identical output.
              </p>
            </div>
            <div className="card finding">
              <span className="n">02</span>
              <span className="tag bug">bug · fixed</span>
              <h3>The scorer penalised defenders for containing first</h3>
              <p>
                Both Wardens isolate a confirmed host <em>before</em> declaring it. The engine judged a declaration with “is the foothold active?”, so an
                isolated foothold read as benign: the declaration counted as wrong and no detection time was recorded. Separately, time-to-contain was
                undefined whenever isolation preceded the verdict. In the first export this pinned MTTD/MTTC near zero (rule{" "}
                <b className="num">{PRE_FIX.timing.rule.join(" / ")}</b>, adaptive <b className="num">{PRE_FIX.timing.adaptive.join(" / ")}</b>). Now the engine
                tracks contained footholds and containment-before-verdict is zero latency: rule{" "}
                <b className="num">{fmt(rule.axes.mttd_score)} / {fmt(rule.axes.mttc_score)}</b>, adaptive{" "}
                <b className="num">{fmt(adaptive.axes.mttd_score)} / {fmt(adaptive.axes.mttc_score)}</b>. The degenerate-baseline tripwire still passes.
              </p>
            </div>
            <div className="card finding">
              <span className="n">03</span>
              <span className="tag win">improvement</span>
              <h3>The v1 Warden was idle {pct(holdRule)} of the time</h3>
              <p>
                It rarely raised a false alarm, but recall was poor: a single real alert on a low-fidelity host never crossed the summed-suspicion bar, so
                compromised hosts went uninvestigated. The Adaptive Warden triages every malicious-category alert, hunts with spare action points,
                and thresholds per host at the midpoint of the engine's two confidence distributions. HOLD drops to <b className="num">{pct(holdAdaptive)}</b>;
                F1 goes <b className="num">{fmt(rule.axes.classification_f1)} → {fmt(adaptive.axes.classification_f1)}</b>; calibration (faithfulness){" "}
                <b className="num">{fmt(rule.faithfulness)} → {fmt(adaptive.faithfulness)}</b>.
              </p>
            </div>
            <div className="card finding">
              <span className="n">04</span>
              <span className="tag">new opponent</span>
              <h3>A low-and-slow Challenger exposes v1</h3>
              <p>
                The new Stealth Challenger lies low after every loud move so the Warden's decaying suspicion bleeds off. Against it the rule Warden's
                win rate falls <b className="num">{pct(rule.winrate)} → {pct(ruleS.winrate)}</b>; the Adaptive Warden holds{" "}
                <b className="num">{pct(adaptiveS.winrate)}</b>, because it acts on individual alerts instead of accumulated suspicion.
              </p>
            </div>
            <div className="card finding">
              <span className="n">05</span>
              <span className="tag win">improvement</span>
              <h3>The RL Warden was acting on the wrong host</h3>
              <p>
                v1's macro-actions always targeted the top-suspicion node, so “isolate” rarely hit a confirmed compromise. v2 decodes each macro to the
                node it makes sense for, adds two calibrated features, decays exploration and fixes a train/eval opponent mismatch. Same training budget:
                scalar <b className="num">{fmt(q1.scalar)} → {fmt(q2.scalar)}</b>. It still trails the hand-written rule Warden against the Rule Challenger ({fmt(rule.scalar)}), but beats it against the Stealth Challenger ({fmt(R("q_v2", "stealth").scalar)} vs {fmt(ruleS.scalar)}).{" "}
                {ablT3.length === 2 && <>The ablation isolates the cause: on T2 at 400 episodes v1 scores <b className="num">{fmt(ablT3.find((a) => a.version === "v1")!.scalar)}</b> vs v2's <b className="num">{fmt(ablT3.find((a) => a.version === "v2")!.scalar)}</b>, so more training alone does not close the gap.</>}
              </p>
            </div>
            <div className="card finding">
              <span className="n">06</span>
              <span className="tag null">null result</span>
              <h3>Decoys didn't move the needle</h3>
              <p>
                The Adaptive Warden can plant decoys in poorly-observed deep zones. With hunting on, spare action points are almost always spent on
                investigation first, so decoys fired only 7 times in 40 T2/T3 matches and changed no metric at three decimal places. They ship
                disabled by default; the switch stays for future work.
              </p>
            </div>
            <div className="card finding">
              <span className="n">07</span>
              <span className="tag bug">guard holds · caveat</span>
              <h3>The score resists gaming, but win-only Elo doesn't</h3>
              <p>
                The best degenerate baseline ({meta[bestDegen.warden]?.label}, {fmt(bestDegen.scalar, 3)}) still scores below the weakest real Warden
                ({meta[worstReal.warden]?.label} vs {data.challengers.find((c) => c.key === worstReal.challenger)?.label}, {fmt(worstReal.scalar, 3)}).
                Isolate-all wins {pct(R("isolate_all").winrate)} of matches, but the SLA-gated mission axis and the availability axis punish it. <b>Caveat:</b> the league's Elo is computed from win/loss only, and there Isolate-all <em>does</em> rate highest ({Math.round(data.league.warden_ratings.isolate_all)}). Win-only ratings are gameable; rank agents by the multi-objective score.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section id="leaderboard">
        <div className="wrap">
          <div className="kicker">Leaderboard</div>
          <h2>Every Warden against both Challengers.</h2>
          <p className="lead">
            Mean warden scalar across tiers T0–T3, {nPerCell} matches per row, with a deterministic bootstrap 95% interval. The scalar is a transparent
            weighting of the axes in the next table; it is computed post-hoc and never shown to agents.
          </p>
          {ChalToggle}
          <div className="card">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>#</th><th>Warden</th><th className="num">Scalar</th><th>95% CI (0–1)</th>
                    <th className="num">Win rate</th><th className="num">F1</th><th className="num">Avail.</th><th className="num">Faithful.</th>
                  </tr>
                </thead>
                <tbody>
                  {tableRows.slice().sort((a, b) => b.scalar - a.scalar).map((r, i) => (
                    <tr key={r.warden} className={degenerate.includes(r.warden) ? "dim" : ""}>
                      <td className="num">{i + 1}</td>
                      <td><AgentName k={r.warden} meta={meta} /></td>
                      <td className="num"><b>{fmt(r.scalar, 3)}</b></td>
                      <td><CIBar mean={r.scalar} ci={r.scalar_ci95} color={AGENT_COLOR[r.warden]} /></td>
                      <td className="num">{pct(r.winrate)}</td>
                      <td className="num">{fmt(r.axes.classification_f1)}</td>
                      <td className="num">{fmt(r.axes.availability)}</td>
                      <td className="num">{fmt(r.faithfulness)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="grid2" style={{ marginTop: "1rem" }}>
            <div className="card">
              <p className="chart-title">Warden scalar by scenario tier</p>
              <p className="chart-sub">T0 tutorial → T3 stealth &amp; deception (lower telemetry, partial sensor outages). Opponent: {data.challengers.find((c) => c.key === chal)?.label}.</p>
              <TierChart rows={rows} challenger={chal} meta={meta} tiers={m.tiers} />
            </div>
            <div className="card">
              <p className="chart-title">Warden win rate matrix</p>
              <p className="chart-sub">Share of matches the Warden survives (objective protected). A high win rate alone is not a good score: see Isolate-all.</p>
              <WinMatrix rows={rows} meta={meta} challengers={data.challengers} order={ORDER} />
            </div>
          </div>

          <div className="card" style={{ marginTop: "1rem" }}>
            <p className="chart-title">Score axes: where each Warden earns and loses points</p>
            <p className="chart-sub">Mean per axis, 0–1 (darker = better). Degenerate strategies max one axis and crater another. Opponent: {data.challengers.find((c) => c.key === chal)?.label}.</p>
            <AxisHeatmap rows={rows} challenger={chal} meta={meta} order={ORDER} />
          </div>
        </div>
      </section>

      <section id="behaviour">
        <div className="wrap">
          <div className="kicker">Behaviour &amp; league</div>
          <h2>How the Wardens spend their turns, and who out-rates whom.</h2>
          <div className="grid2">
            <div className="card">
              <p className="chart-title">Intent mix, T2 (share of Warden turns)</p>
              <p className="chart-sub">Held-out seeds {m.eval_seeds[0]}–{m.eval_seeds[0] + 19}, vs Rule Challenger.</p>
              <ActionMix mix={data.action_mix} meta={meta} />
            </div>
            <div className="card">
              <p className="chart-title">Co-evolution league: Warden Elo by round</p>
              <p className="chart-sub">
                Tier {m.league.tier}, {m.league.seeds} seeds × {m.league.rounds} rounds, both Challengers. Verdict:{" "}
                <b>{data.league.dynamic}</b> (rank volatility {fmt(data.league.rank_volatility)}).
              </p>
              <EloChart league={data.league} meta={meta} />
            </div>
          </div>
        </div>
      </section>

      <section id="rl">
        <div className="wrap">
          <div className="kicker">Reinforcement learning</div>
          <h2>Same budget, different decoder.</h2>
          <p className="lead">
            Both tabular-Q Wardens train for {m.rl_episodes} episodes on a mixed T0–T3 curriculum (training seeds {m.train_seeds[0]}–{m.train_seeds[1]})
            and are evaluated only on held-out seeds. v1 learned {data.rl.curves.v1.states} states, v2 learned {data.rl.curves.v2.states}.
          </p>
          <div className="grid2">
            <div className="card">
              <p className="chart-title">Training win rate (rolling 40 episodes)</p>
              <p className="chart-sub">Training-time signal only (evaluation uses the hidden post-hoc score). The sawtooth is the curriculum: 4 passes of T0→T1→T2→T3 blocks, so win rate dips on the harder tiers.</p>
              <RLCurve rl={data.rl} meta={meta} />
            </div>
            <div className="card">
              <p className="chart-title">Ablation: training budget vs decoder version</p>
              <p className="chart-sub">Single-tier training, warden scalar on 40 held-out seeds.</p>
              <AblationChart rl={data.rl} meta={meta} />
            </div>
          </div>
        </div>
      </section>

      <section id="replay">
        <div className="wrap">
          <div className="kicker">Replay</div>
          <h2>One match, two defenders.</h2>
          <p className="lead">
            Tier {data.replay.tier}, seed {data.replay.seed}: the first held-out seed where the rule Warden loses and the Adaptive Warden wins, against
            the identical Challenger. Scrub the ticks, or click an event.
          </p>
          <Replay replay={data.replay} />
        </div>
      </section>

      <section id="method">
        <div className="wrap">
          <div className="kicker">Method &amp; safety</div>
          <h2>How to trust (and reproduce) these numbers.</h2>
          <div className="grid2">
            <div className="card">
              <h3>Reproduce</h3>
              <p>Pure Python standard library; the whole experiment is one command and is byte-for-byte deterministic apart from the timestamp.</p>
              <pre>{`pip install -e ".[dev]"
python -m pytest -q    # all gates
${m.command} \\
    --seeds ${m.eval_seeds[0]}-${m.eval_seeds[1]} --episodes ${m.rl_episodes}`}</pre>
              <p className="note">
                Evaluation seeds never overlap training seeds. Intervals are a percentile bootstrap (1,000 resamples, fixed RNG) over per-match scores.
              </p>
            </div>
            <div className="card">
              <h3>The abstraction is the isolation</h3>
              <div className="safety-row"><span className="check">✓</span><div><b>No code execution by agents.</b> Only schema-validated abstract intents; the engine owns every outcome.</div></div>
              <div className="safety-row"><span className="check">✓</span><div><b>Zero egress, verified.</b> A static scan forbids networking/process imports in the package, including the new modules.</div></div>
              <div className="safety-row"><span className="check">✓</span><div><b>Deterministic, now across processes.</b> State-hash transcripts are identical across runs and hash seeds.</div></div>
              <div className="safety-row"><span className="check">✓</span><div><b>Tamper-evident logs.</b> Hash-chained, re-scorable from the log alone.</div></div>
              <div className="safety-row"><span className="check">✓</span><div><b>Reward-hacking tripwire.</b> Tests require both rule and adaptive Wardens to beat every degenerate baseline.</div></div>
            </div>
          </div>
          <div className="card" style={{ marginTop: "1rem" }}>
            <h3>Not benchmarked here: the docker-desktop-agents lab</h3>
            <p>
              The repo also contains a deliberately open-scope lab: four Dockerized Linux desktops, each driven by local vision + text models with a real
              shell, on an isolated network with seeded weak SSH credentials. It has a different safety boundary (Docker network isolation, not
              abstraction), needs ~24 GB RAM, and is not deterministic, so it is out of scope for this experiment. See{" "}
              <code className="inline">docker-desktop-agents/README.md</code> to run it locally.
            </p>
          </div>
        </div>
      </section>

      <footer>
        <div className="wrap">
          ARENA is a research/education abstraction. Nothing here maps to a real-world exploit. Pre-fix numbers are historical: finding 01 was measured on commit {PRE_FIX.commit}, finding 02 on the first v0.2 export before the engine/scorer fix.
        </div>
      </footer>
    </>
  );
}
