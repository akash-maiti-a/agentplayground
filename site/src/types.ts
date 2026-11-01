export type Tier = "T0" | "T1" | "T2" | "T3";

export interface TierStats {
  scalar: number;
  scalar_ci95: [number, number];
  winrate: number;
  f1: number;
  availability: number;
  challenger_scalar: number;
}

export interface Row {
  warden: string;
  challenger: string;
  n: number;
  scalar: number;
  scalar_ci95: [number, number];
  winrate: number;
  winrate_ci95: [number, number];
  faithfulness: number;
  axes: Record<string, number>;
  per_tier: Record<Tier, TierStats>;
}

export interface AgentMeta {
  key: string;
  label: string;
  generation?: "v1" | "v2" | "baseline";
  description: string;
}

export interface Step {
  verb: string;
  target: string | null;
  ok: boolean;
}

export interface Tick {
  t: number;
  c: Step | null;
  w: Step | null;
  alerts: number;
  greens: number;
  avail?: number;
  nodes?: Record<string, string>;
}

export interface Run {
  warden: string;
  winner: string;
  reason: string;
  ticks: number;
  scalar: number;
  f1: number;
  ever_compromised: string[];
  declared: string[];
  timeline: Tick[];
}

export interface Results {
  manifest: {
    arena_version: string;
    generated_at: string | null;
    tiers: Tier[];
    eval_seeds: [number, number, number];
    train_seeds: [number, number, number];
    rl_episodes: number;
    league: { tier: string; seeds: number; rounds: number };
    command: string;
  };
  wardens: AgentMeta[];
  challengers: AgentMeta[];
  leaderboard: { rows: Row[] };
  league: {
    rounds: number;
    warden_ratings: Record<string, number>;
    challenger_ratings: Record<string, number>;
    win_matrix: Record<string, Record<string, number>>;
    rank_volatility: number;
    dynamic: string;
    rating_history: { warden: Record<string, number>; challenger: Record<string, number> }[];
  };
  rl: {
    curves: Record<"v1" | "v2", { return: number[]; winrate: number[]; episodes: number; states: number; stride: number }>;
    ablation: { tier: string; version: string; episodes: number; winrate: number; scalar: number; f1: number; availability: number }[];
  };
  action_mix: Record<string, Record<string, number>>;
  replay: {
    tier: string;
    seed: number;
    topology: {
      objective: string;
      zones: string[];
      nodes: { id: string; zone: string; role: string; value: number; business: boolean }[];
      edges: [string, string][];
    };
    runs: Record<"rule" | "adaptive", Run>;
  };
}

/** Fixed entity -> color slot. Color follows the agent, never its rank. */
export const AGENT_COLOR: Record<string, string> = {
  rule: "var(--s1)",
  adaptive: "var(--s2)",
  q_v1: "var(--s3)",
  q_v2: "var(--s4)",
  passive: "var(--muted-series)",
  isolate_all: "var(--muted-series)",
  declare_all: "var(--muted-series)",
};

export const AXIS_LABEL: Record<string, string> = {
  mission_success: "Mission",
  mttd_score: "MTTD",
  mttc_score: "MTTC",
  classification_f1: "F1",
  availability: "Avail.",
  containment_quality: "Contain.",
  false_alarm_score: "No false alarms",
};

export const fmt = (x: number, d = 2) => x.toFixed(d);
export const pct = (x: number) => `${Math.round(x * 100)}%`;
