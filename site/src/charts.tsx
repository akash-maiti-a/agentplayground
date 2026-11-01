import {
  Bar, BarChart, CartesianGrid, LabelList, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { AGENT_COLOR, AXIS_LABEL, AgentMeta, Results, Row, Tier, fmt, pct } from "./types";

const MAIN = ["rule", "adaptive", "q_v1", "q_v2"];
const tick = { fill: "var(--muted)", fontSize: 12 };
const axisLine = { stroke: "var(--axis)" };

/** Single-hue sequential fill: accent mixed into the panel by magnitude. */
export function seqFill(v: number) {
  const pctMix = Math.round(4 + Math.pow(Math.max(0, Math.min(1, v)), 1.15) * 84);
  return `color-mix(in srgb, var(--accent) ${pctMix}%, var(--panel))`;
}

export function Swatch({ k }: { k: string }) {
  return <span className="swatch" style={{ background: AGENT_COLOR[k] ?? "var(--muted-series)" }} />;
}

export function AgentName({ k, meta }: { k: string; meta: Record<string, AgentMeta> }) {
  const m = meta[k];
  return (
    <span className="agent">
      <Swatch k={k} />
      {m?.label ?? k}
      {m?.generation && <span className={`gen ${m.generation}`}>{m.generation}</span>}
    </span>
  );
}

/** Mean with a 95% CI whisker on a fixed 0..1 scale. */
export function CIBar({ mean, ci, color }: { mean: number; ci: [number, number]; color: string }) {
  const W = 120, H = 16, x = (v: number) => 2 + v * (W - 4);
  return (
    <svg width={W} height={H} role="img" aria-label={`${fmt(mean, 3)} (95% CI ${fmt(ci[0], 3)} to ${fmt(ci[1], 3)})`}>
      <line x1={x(0)} x2={x(1)} y1={H / 2} y2={H / 2} stroke="var(--grid)" strokeWidth={2} strokeLinecap="round" />
      <line x1={x(ci[0])} x2={x(ci[1])} y1={H / 2} y2={H / 2} stroke={color} strokeWidth={2} strokeLinecap="round" opacity={0.55} />
      <line x1={x(ci[0])} x2={x(ci[0])} y1={4} y2={H - 4} stroke={color} strokeWidth={2} strokeLinecap="round" opacity={0.55} />
      <line x1={x(ci[1])} x2={x(ci[1])} y1={4} y2={H - 4} stroke={color} strokeWidth={2} strokeLinecap="round" opacity={0.55} />
      <circle cx={x(mean)} cy={H / 2} r={4.5} fill={color} stroke="var(--panel)" strokeWidth={2} />
    </svg>
  );
}

function TT({ active, payload, label, title, digits = 3, names }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tt">
      <div className="h">{title ? title(label) : label}</div>
      {payload.map((p: any) => (
        <div className="r" key={p.dataKey}>
          <span className="swatch" style={{ background: p.color }} />
          {names?.[p.dataKey] ?? p.name}
          <b>{typeof p.value === "number" ? p.value.toFixed(digits) : p.value}</b>
        </div>
      ))}
    </div>
  );
}

function Legend({ keys, meta }: { keys: string[]; meta: Record<string, AgentMeta> }) {
  return (
    <div className="legend">
      {keys.map((k) => (
        <span key={k}><Swatch k={k} />{meta[k]?.label ?? k}</span>
      ))}
    </div>
  );
}

export function TierChart({ rows, challenger, meta, tiers }: {
  rows: Row[]; challenger: string; meta: Record<string, AgentMeta>; tiers: Tier[];
}) {
  const sel = rows.filter((r) => r.challenger === challenger && MAIN.includes(r.warden));
  const data = tiers.map((t) => {
    const o: Record<string, number | string> = { tier: t };
    sel.forEach((r) => (o[r.warden] = r.per_tier[t].scalar));
    return o;
  });
  const names = Object.fromEntries(MAIN.map((k) => [k, meta[k]?.label ?? k]));
  return (
    <>
      <Legend keys={MAIN} meta={meta} />
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="tier" tick={tick} axisLine={axisLine} tickLine={false} />
          <YAxis domain={[0.3, 1]} tick={tick} axisLine={false} tickLine={false} />
          <Tooltip content={<TT names={names} title={(l: string) => `Tier ${l} · warden scalar`} />}
                   cursor={{ stroke: "var(--axis)" }} />
          {MAIN.map((k) => (
            <Line key={k} dataKey={k} name={names[k]} stroke={AGENT_COLOR[k]} strokeWidth={2}
                  dot={{ r: 4, strokeWidth: 2, stroke: "var(--panel)", fill: AGENT_COLOR[k] }}
                  activeDot={{ r: 6, strokeWidth: 2, stroke: "var(--panel)" }} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </>
  );
}

export function AxisHeatmap({ rows, challenger, meta, order }: {
  rows: Row[]; challenger: string; meta: Record<string, AgentMeta>; order: string[];
}) {
  const axes = Object.keys(AXIS_LABEL);
  const byW = Object.fromEntries(rows.filter((r) => r.challenger === challenger).map((r) => [r.warden, r]));
  return (
    <div className="table-wrap">
      <table className="heat">
        <thead>
          <tr><th>Warden</th>{axes.map((a) => <th key={a} className="num">{AXIS_LABEL[a]}</th>)}</tr>
        </thead>
        <tbody>
          {order.filter((w) => byW[w]).map((w) => (
            <tr key={w}>
              <td><AgentName k={w} meta={meta} /></td>
              {axes.map((a) => {
                const v = byW[w].axes[a];
                return (
                  <td key={a} className="cell" style={{ background: seqFill(v) }}
                      title={`${meta[w]?.label}: ${AXIS_LABEL[a]} = ${fmt(v, 3)}`}>{fmt(v)}</td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function WinMatrix({ rows, meta, challengers, order }: {
  rows: Row[]; meta: Record<string, AgentMeta>; challengers: AgentMeta[]; order: string[];
}) {
  const get = (w: string, c: string) => rows.find((r) => r.warden === w && r.challenger === c);
  return (
    <div className="table-wrap">
      <table className="heat">
        <thead>
          <tr><th>Warden</th>{challengers.map((c) => <th key={c.key} className="num">vs {c.label.replace(" Challenger", "")}</th>)}</tr>
        </thead>
        <tbody>
          {order.map((w) => (
            <tr key={w}>
              <td><AgentName k={w} meta={meta} /></td>
              {challengers.map((c) => {
                const r = get(w, c.key);
                if (!r) return <td key={c.key} />;
                return (
                  <td key={c.key} className="cell" style={{ background: seqFill(r.winrate) }}
                      title={`${meta[w]?.label} vs ${c.label}: Warden wins ${pct(r.winrate)} (95% CI ${pct(r.winrate_ci95[0])}–${pct(r.winrate_ci95[1])}, n=${r.n})`}>
                    {pct(r.winrate)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EloChart({ league, meta }: { league: Results["league"]; meta: Record<string, AgentMeta> }) {
  const keys = Object.keys(league.warden_ratings);
  const data = [{ round: 0, ...Object.fromEntries(keys.map((k) => [k, 1200])) },
    ...league.rating_history.map((h, i) => ({ round: i + 1, ...h.warden }))];
  const names = Object.fromEntries(keys.map((k) => [k, meta[k]?.label ?? k]));
  const colored = keys.filter((k) => MAIN.includes(k));
  const last = data[data.length - 1] as Record<string, number>;
  return (
    <>
      <div className="legend">
        {colored.map((k) => <span key={k}><Swatch k={k} />{names[k]}</span>)}
        <span><span className="swatch" style={{ background: "var(--muted-series)" }} />Degenerate baselines</span>
      </div>
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: -6 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="round" tick={tick} axisLine={axisLine} tickLine={false}
                 label={{ value: "round", position: "insideBottomRight", offset: -2, fill: "var(--faint)", fontSize: 11 }} />
          <YAxis tick={tick} axisLine={false} tickLine={false} domain={["auto", "auto"]} />
          <Tooltip content={<TT names={names} digits={0} title={(l: number) => `After round ${l} · Elo`} />}
                   cursor={{ stroke: "var(--axis)" }} itemSorter={(p: any) => -p.value} />
          {keys.map((k) => (
            <Line key={k} dataKey={k} name={names[k]} stroke={AGENT_COLOR[k]}
                  strokeWidth={MAIN.includes(k) ? 2 : 1.5} dot={false} isAnimationActive={false}
                  activeDot={{ r: 5, strokeWidth: 2, stroke: "var(--panel)" }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <p className="note">Final: {keys.slice().sort((a, b) => last[b] - last[a]).map((k) => `${names[k]} ${Math.round(last[k])}`).join(" · ")}</p>
    </>
  );
}

export function RLCurve({ rl, meta }: { rl: Results["rl"]; meta: Record<string, AgentMeta> }) {
  const n = Math.max(rl.curves.v1.winrate.length, rl.curves.v2.winrate.length);
  const data = Array.from({ length: n }, (_, i) => ({
    ep: i * rl.curves.v2.stride, q_v1: rl.curves.v1.winrate[i], q_v2: rl.curves.v2.winrate[i],
  }));
  const names = { q_v1: meta.q_v1?.label ?? "v1", q_v2: meta.q_v2?.label ?? "v2" };
  return (
    <>
      <Legend keys={["q_v1", "q_v2"]} meta={meta} />
      <ResponsiveContainer width="100%" height={250}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="ep" tick={tick} axisLine={axisLine} tickLine={false} type="number" domain={[0, "dataMax"]}
                 label={{ value: "training episode", position: "insideBottomRight", offset: -2, fill: "var(--faint)", fontSize: 11 }} />
          <YAxis domain={[0, 1]} tick={tick} axisLine={false} tickLine={false} tickFormatter={(v) => pct(v)} />
          <Tooltip content={<TT names={names} digits={2} title={(l: number) => `Episode ${l} · rolling win rate (40 ep)`} />}
                   cursor={{ stroke: "var(--axis)" }} />
          {(["q_v1", "q_v2"] as const).map((k) => (
            <Line key={k} dataKey={k} name={names[k]} stroke={AGENT_COLOR[k]} strokeWidth={2} dot={false}
                  isAnimationActive={false} activeDot={{ r: 5, strokeWidth: 2, stroke: "var(--panel)" }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </>
  );
}

export function AblationChart({ rl, meta }: { rl: Results["rl"]; meta: Record<string, AgentMeta> }) {
  const groups: Record<string, any> = {};
  rl.ablation.forEach((a) => {
    const g = (groups[`${a.tier} · ${a.episodes} ep`] ??= { g: `${a.tier} · ${a.episodes} ep` });
    g[a.version === "v1" ? "q_v1" : "q_v2"] = a.scalar;
  });
  const data = Object.values(groups);
  const names = { q_v1: meta.q_v1?.label ?? "v1", q_v2: meta.q_v2?.label ?? "v2" };
  if (!data.length) return <p className="note">Ablation skipped in this export (--quick).</p>;
  return (
    <>
      <Legend keys={["q_v1", "q_v2"]} meta={meta} />
      <ResponsiveContainer width="100%" height={250}>
        <BarChart data={data} margin={{ top: 18, right: 8, bottom: 0, left: -12 }} barGap={2} barCategoryGap="28%">
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="g" tick={{ ...tick, fontSize: 11 }} axisLine={axisLine} tickLine={false} interval={0} />
          <YAxis domain={[0, 1]} tick={tick} axisLine={false} tickLine={false} />
          <Tooltip content={<TT names={names} title={(l: string) => `${l} · warden scalar`} />} cursor={{ fill: "var(--accent-dim)" }} />
          {(["q_v1", "q_v2"] as const).map((k) => (
            <Bar key={k} dataKey={k} name={names[k]} fill={AGENT_COLOR[k]} radius={[4, 4, 0, 0]} isAnimationActive={false}>
              <LabelList dataKey={k} position="top" fontSize={10} fill="var(--muted)" formatter={(v: number) => v.toFixed(2)} />
            </Bar>
          ))}
        </BarChart>
      </ResponsiveContainer>
    </>
  );
}

export function ActionMix({ mix, meta }: { mix: Results["action_mix"]; meta: Record<string, AgentMeta> }) {
  const verbs = Array.from(new Set(Object.values(mix).flatMap((m) => Object.keys(m))))
    .sort((a, b) => (mix.adaptive?.[b] ?? 0) - (mix.adaptive?.[a] ?? 0));
  const keys = Object.keys(mix);
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Intent</th>{keys.map((k) => <th key={k} className="num">{meta[k]?.label ?? k}</th>)}</tr></thead>
        <tbody>
          {verbs.map((v) => (
            <tr key={v}>
              <td className="mono">{v}</td>
              {keys.map((k) => {
                const x = mix[k][v] ?? 0;
                return (
                  <td key={k} className="num">
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                      <svg width={70} height={8} aria-hidden>
                        <rect x={0} y={0} width={70} height={8} rx={4} fill="var(--grid)" />
                        <rect x={0} y={0} width={Math.max(2, x * 70)} height={8} rx={4} fill={AGENT_COLOR[k]} />
                      </svg>
                      {pct(x)}
                    </span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
