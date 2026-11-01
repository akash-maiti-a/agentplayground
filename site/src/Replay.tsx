import { useEffect, useMemo, useRef, useState } from "react";
import { Results, Tick } from "./types";

const STATE_STYLE: Record<string, { fill: string; label: string; icon: string }> = {
  healthy: { fill: "var(--panel-2)", label: "Healthy", icon: "" },
  foothold: { fill: "var(--critical)", label: "Challenger foothold", icon: "!" },
  "foothold+admin": { fill: "var(--critical)", label: "Foothold with admin", icon: "!!" },
  isolated: { fill: "var(--muted-series)", label: "Isolated by Warden", icon: "⊘" },
  restoring: { fill: "var(--warning)", label: "Restoring", icon: "↻" },
  decoy: { fill: "var(--accent)", label: "Decoy", icon: "◇" },
};

function stateAt(timeline: Tick[], t: number): Record<string, string> {
  let snap: Record<string, string> = {};
  for (const row of timeline) {
    if (row.t > t) break;
    if (row.nodes) snap = row.nodes;
  }
  return snap;
}

export default function Replay({ replay }: { replay: Results["replay"] }) {
  const [which, setWhich] = useState<"rule" | "adaptive">("rule");
  const run = replay.runs[which];
  const maxT = run.timeline.length ? run.timeline[run.timeline.length - 1].t : 0;
  const [t, setT] = useState(maxT);
  const [playing, setPlaying] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => { setT(maxT); setPlaying(false); }, [which, maxT]);
  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => setT((x) => (x >= maxT ? (setPlaying(false), x) : x + 1)), 280);
    return () => clearInterval(id);
  }, [playing, maxT]);
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(".ev.cur");
    if (el && listRef.current) listRef.current.scrollTop = el.offsetTop - listRef.current.offsetTop - 120;
  }, [t, which]);

  const topo = replay.topology;
  const zones = topo.zones;
  const layout = useMemo(() => {
    const W = 640, colW = W / zones.length, rowH = 30, top = 34;
    const pos: Record<string, { x: number; y: number; w: number }> = {};
    let maxRows = 0;
    zones.forEach((z, zi) => {
      const ns = topo.nodes.filter((n) => n.zone === z);
      maxRows = Math.max(maxRows, ns.length);
      ns.forEach((n, i) => (pos[n.id] = { x: zi * colW + 8, y: top + i * rowH, w: colW - 16 }));
    });
    return { W, H: top + maxRows * rowH + 8, colW, pos };
  }, [topo, zones]);

  const states = stateAt(run.timeline, t);
  const cur = run.timeline.find((r) => r.t === t);
  const avail = [...run.timeline].reverse().find((r) => r.t <= t && r.avail !== undefined)?.avail ?? 1;
  const won = run.winner === "warden";

  return (
    <div className="card">
      <div className="controls">
        <div className="seg" role="group" aria-label="Warden">
          {(["rule", "adaptive"] as const).map((k) => (
            <button key={k} aria-pressed={which === k} onClick={() => setWhich(k)}>
              {k === "rule" ? "Rule Warden (v1)" : "Adaptive Warden (v2)"}
            </button>
          ))}
        </div>
        <span className={`result-pill ${won ? "win" : "loss"}`}>
          {won ? "Warden wins" : "Challenger wins"} · {run.reason.replace("_", " ")} · tick {run.ticks}
        </span>
        <span className="note" style={{ margin: 0 }}>
          scalar {run.scalar.toFixed(3)} · F1 {run.f1.toFixed(2)} · compromised {run.ever_compromised.length} · declared {run.declared.length}
        </span>
      </div>

      <div style={{ display: "flex", gap: "0.75rem", alignItems: "center", margin: "0.9rem 0 0.4rem" }}>
        <button className="theme-btn" onClick={() => { if (t >= maxT) setT(0); setPlaying((p) => !p); }}
                aria-label={playing ? "Pause" : "Play"}>{playing ? "❚❚ Pause" : "▶ Play"}</button>
        <input type="range" min={0} max={maxT} value={t} onChange={(e) => { setPlaying(false); setT(+e.target.value); }}
               aria-label="Tick" />
        <span className="mono" style={{ fontSize: "0.8rem", whiteSpace: "nowrap" }}>tick {t}/{maxT}</span>
      </div>

      <div className="replay-grid">
        <div>
          <div className="table-wrap">
            <svg viewBox={`0 0 ${layout.W} ${layout.H}`} style={{ width: "100%", minWidth: 520, display: "block" }}
                 role="img" aria-label={`Network topology at tick ${t}`}>
              {zones.map((z, zi) => (
                <text key={z} x={zi * layout.colW + 8} y={18} fontSize={11} fill="var(--muted)"
                      fontFamily="var(--mono)" style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>{z}</text>
              ))}
              {topo.edges.map(([a, b]) => {
                const pa = layout.pos[a], pb = layout.pos[b];
                if (!pa || !pb) return null;
                return <line key={a + b} x1={pa.x + pa.w / 2} y1={pa.y + 11} x2={pb.x + pb.w / 2} y2={pb.y + 11}
                             stroke="var(--axis)" strokeWidth={1.25} />;
              })}
              {topo.nodes.map((n) => {
                const p = layout.pos[n.id];
                if (!p) return null;
                const s = states[n.id] ?? "healthy";
                const st = STATE_STYLE[s] ?? STATE_STYLE.healthy;
                const hot = cur && (cur.c?.target === n.id || cur.w?.target === n.id);
                const dark = s !== "healthy";
                return (
                  <g key={n.id}>
                    <title>{`${n.id} · ${n.role} · value ${n.value}${n.business ? " · business-critical" : ""} — ${st.label}`}</title>
                    <rect x={p.x} y={p.y} width={p.w} height={22} rx={5} fill={st.fill}
                          stroke={hot ? "var(--text)" : n.id === topo.objective ? "var(--accent)" : "var(--border)"}
                          strokeWidth={hot || n.id === topo.objective ? 2 : 1} />
                    <text x={p.x + 8} y={p.y + 15} fontSize={10.5} fontFamily="var(--mono)"
                          fill={dark && s !== "restoring" ? "#ffffff" : s === "restoring" ? "#0b0b0b" : "var(--text)"}>
                      {n.id === topo.objective ? "★ " : ""}{n.id}{st.icon ? ` ${st.icon}` : ""}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
          <div className="legend" style={{ marginTop: "0.6rem" }}>
            {Object.entries(STATE_STYLE).map(([k, v]) => (
              <span key={k}><span className="swatch" style={{ background: v.fill, border: "1px solid var(--border)" }} />
                {v.icon && <b className="mono">{v.icon}</b>}{v.label}</span>
            ))}
            <span><b>★</b> objective ({topo.objective})</span>
          </div>
          <div className="note">Availability at tick {t}: <b>{Math.round(avail * 100)}%</b> · alerts this tick: {cur?.alerts ?? 0} · benign noise events: {cur?.greens ?? 0}</div>
        </div>
        <div className="events" ref={listRef} aria-label="Event log">
          {run.timeline.filter((r) => r.c || r.w).map((r) => (
            <div key={r.t} className={`ev ${r.t === t ? "cur" : ""}`} onClick={() => { setPlaying(false); setT(r.t); }}
                 style={{ cursor: "pointer" }}>
              <span>t{String(r.t).padStart(3, "0")}</span>
              <span>
                {r.c && r.c.verb !== "DWELL" && (
                  <div className={r.c.ok ? "" : "fail"}><span className="c">C</span> {r.c.verb} {r.c.target ?? ""}</div>
                )}
                {r.w && r.w.verb !== "HOLD" && (
                  <div className={r.w.ok ? "" : "fail"}><span className="w">W</span> {r.w.verb} {r.w.target ?? ""}</div>
                )}
                {(!r.c || r.c.verb === "DWELL") && (!r.w || r.w.verb === "HOLD") && <div>· both idle</div>}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
