// API との受け渡し。Phase 1 では読み取りのみ。発注の口は持たない。
//
// 期限と金額の表示は ./freshness に置いてある。**同じ実装を2か所に持たない。**
export { isExpired, secondsRemaining, yen } from "./freshness";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

export type Signal = "BUY" | "SELL" | "WAIT" | "NO_TRADE";
export type Direction = "LONG" | "SHORT" | "NEUTRAL";

export interface RankingEntry {
  rank: number;
  pair: string;
  direction: Direction;
  signal: Signal;
  score: number;
  quality: string;
  regime: string;
  strategy: string | null;
  tier: string;
  spread_pips: number | null;
  warning_count: number;
  data_quality: string;
  valid_until: string | null;
  reason: string | null;
}

export interface RankingResponse {
  version: string;
  generated_at: string;
  provider: { name: string; state: string; detail: string };
  entries: RankingEntry[];
  analyzed: number;
  failed: number;
}

export interface TimeframeBias {
  timeframe: string;
  direction: Direction;
  structure: string;
  note: string;
}

export interface AnalysisResult {
  pair: string;
  timestamp: string;
  direction: Direction;
  signal: Signal;
  score: number;
  quality: string;
  score_breakdown: Record<string, number>;
  regime: string;
  regime_score: number;
  // どの戦略で向きを決めたか。**戦略ごとに前提が違うので、混ぜて
  // 眺めると読み違える。** 名前と説明は /api/strategies から引く。
  strategy: string | null;
  news_state: string;
  news: {
    state: string;
    available: boolean;
    blocked: boolean;
    ratio: number;
    minutes_to_next: number | null;
    next_event: string | null;
    age_hours: number | null;
    reasons: string[];
  } | null;
  correlation: {
    state: string;
    ratio: number;
    drivers: {
      name: string;
      label: string;
      correlation: number;
      driver_move: number;
      implied: number;
      agrees: boolean;
    }[];
    skipped: string[];
    reasons: string[];
  } | null;
  h1_bias: TimeframeBias;
  h4_bias: TimeframeBias;
  m15_setup: TimeframeBias;
  m5_context: TimeframeBias;
  market_structure: {
    structure: string;
    last_swing_high: number | null;
    last_swing_low: number | null;
    bos: boolean;
    choch: boolean;
    pattern: string[];
  };
  spread: { spread_pips: number; spread_price: number } | null;
  valid_until: string | null;
  data_quality: string;
  hard_blocked: boolean;
  indicators: Record<string, Record<string, number | null>>;
  warnings: string[];
  reasons: string[];
  invalidation_reasons: string[];
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`API エラー ${res.status}: ${path}`);
  }
  return (await res.json()) as T;
}

// 戦略の一覧。**日本語名を画面側に持たない。** 画面に書くと engine 側と
// 食い違ったときに気づけない。名前・説明・割り当ては API から引く。
export interface StrategyInfo {
  name: string;
  label: string;
  why: string;
  scoring: string;
  regimes: string[];
  enabled: boolean;
}

export interface StrategiesResponse {
  version: string;
  strategies: StrategyInfo[];
  measured: string;
}

export const fetchStrategies = () =>
  getJson<StrategiesResponse>("/api/strategies");

export const strategyLabels = (res: StrategiesResponse | null) =>
  Object.fromEntries((res?.strategies ?? []).map((s) => [s.name, s.label]));

// 取り込んだ材料の鮮度。**取り込みは手動なので、古くなったことが
// 画面から分かるようにしておく。** 予定表が古くなると全銘柄が見送りになる。
export interface DataSource {
  key: string;
  label: string;
  state: "OK" | "SOON" | "STALE" | "MISSING";
  detail: string;
  as_of: string | null;
  age_hours: number | null;
  limit_hours: number | null;
  refresh: string;
  blocks_trading: boolean;
  notes: string[];
}

export interface DataStatus {
  checked_at: string;
  provider: string;
  // 市場が開いているか。**閉まっているときに「作り直せ」と言わない。**
  market_open: boolean;
  state: DataSource["state"];
  blocking: string[];
  sources: DataSource[];
}

export const fetchDataStatus = () => getJson<DataStatus>("/api/data-status");

export const DATA_STATE_JA: Record<DataSource["state"], string> = {
  OK: "◯ 使えます",
  SOON: "△ もうすぐ古くなります",
  STALE: "× 古くなっています",
  MISSING: "— 取り込んでいません",
};

// 画面に出た判断の記録。**「こう判断した」だけで、建てたかどうかは含まない。**
export interface JournalEntry {
  id: number;
  pair: string;
  decided_at: string;
  last_seen: string;
  seen_count: number;
  provider: string;
  signal: Signal;
  direction: Direction;
  quality: string;
  score: number;
  regime: string;
  strategy: string | null;
  hard_blocked: number;
  reason: string | null;
  outcome: string | null;
  r_multiple: number | null;
}

export interface JournalResponse {
  version: string;
  enabled: boolean;
  summary: {
    total: number;
    actionable: number;
    scored: number;
    first_at: string | null;
    last_at: string | null;
    by_signal: Record<string, number>;
    note: string;
  };
  entries: JournalEntry[];
}

export const fetchJournal = (limit = 20) =>
  getJson<JournalResponse>(`/api/journal?limit=${limit}`);

// どの銘柄を支援するか。**費用だけで分けている。** 勝てるかどうかでは
// 分けていない（それは測れていない）。
export interface FocusResponse {
  version: string;
  measured: boolean;
  measured_at: string | null;
  timeframe: string | null;
  spread_limit_ratio: number | null;
  spread_basis: string | null;
  counts: Record<string, number>;
  labels: Record<string, string>;
  pairs: Record<string, {
    spread_pips: number;
    spread_pips_wide: number;
    atr_pips_median: number;
    ratio_median: number;
    ratio_median_wide: number;
    pass_rate: number;
    tier: string;
  }>;
  note: string;
}

export const fetchFocus = () => getJson<FocusResponse>("/api/focus");

export const TIER_JA: Record<string, string> = {
  focus: "◎ 支援する",
  watch: "○ 様子を見る",
  off: "× 対象外",
  unknown: "— 未測定",
};

// 区分を渡すと、その銘柄だけ分析する。**26銘柄ぶん計算しない。**
export const fetchRanking = (tier?: string) =>
  getJson<RankingResponse>(
    `/api/ranking${tier ? `?tier=${tier}` : ""}`
  );
export const fetchAnalysis = (pair: string) =>
  getJson<AnalysisResult>(`/api/analysis/${pair}`);
export const fetchHealth = () =>
  getJson<Record<string, unknown>>("/health");

// 色だけに頼らないための印。読み上げや印刷でも区別できるようにする。
export const SIGNAL_MARK: Record<Signal, string> = {
  BUY: "▲ 買い",
  SELL: "▼ 売り",
  WAIT: "… 様子見",
  NO_TRADE: "× 見送り",
};

export const SIGNAL_CLASS: Record<Signal, string> = {
  BUY: "sig buy",
  SELL: "sig sell",
  WAIT: "sig wait",
  NO_TRADE: "sig none",
};

export const REGIME_JA: Record<string, string> = {
  TREND_UP: "上昇トレンド",
  TREND_DOWN: "下降トレンド",
  RANGE: "持ち合い",
  BREAKOUT: "放れ",
  HIGH_VOLATILITY: "変動大",
  LOW_VOLATILITY: "変動小",
  NEWS: "指標",
  TRANSITION: "移行中",
  NO_TRADE: "判定不可",
};

export const DIRECTION_JA: Record<Direction, string> = {
  LONG: "上",
  SHORT: "下",
  NEUTRAL: "—",
};

// 到達しうる満点。相関とニュースの材料を繋いだので100点。ただし銘柄や
// 取り込み状況によっては測れず、その回だけ満点から外れる。
// 画面で「95点中」と出すのは、点数の意味を取り違えさせないため。
export const MAX_ATTAINABLE_SCORE = 100;



// ---------------------------------------------------------------- Phase 2

export interface Money {
  amount: number;
  currency: string;
}

export interface PlanTarget {
  label: string;
  price: number;
  r_multiple: number;
  profit: Money | null;
  capped_by: string | null;
}

export interface Sizing {
  ok: boolean;
  qty: number;
  contract_unit: number;
  lot: number;
  units: number;
  risk_amount: Money | null;
  pip_value_per_lot: Money | null;
  stop_distance_pips: number | null;
  required_margin: Money | null;
  margin_use_pct: number | null;
  conversion_pair: string | null;
  reasons: string[];
  notes: string[];
}

export interface SwapInfo {
  state: string;
  per_lot_per_day: Money | null;
  per_position_per_day: Money | null;
  source: string | null;
  as_of: string | null;
  age_days: number | null;
  notes: string[];
}

export interface TradePlan {
  pair: string;
  ok: boolean;
  direction: Direction;
  signal: Signal;
  reference_price: number | null;
  entry_low: number | null;
  entry_high: number | null;
  stop: number | null;
  stop_basis: string | null;
  targets: PlanTarget[];
  risk_reward: number | null;
  sizing: Sizing | null;
  loss_if_stopped: Money | null;
  spread_cost: Money | null;
  swap_per_day: Money | null;
  swap_state: string;
  swap: SwapInfo | null;
  valid_until: string | null;
  reasons: string[];
  blocked_reasons: string[];
  disclaimers: string[];
}

export interface PlanResponse {
  version: string;
  analysis: {
    signal: Signal;
    direction: Direction;
    score: number;
    regime: string;
    valid_until: string | null;
  };
  account: {
    balance: number;
    risk_per_trade_pct: number;
    leverage: number;
    margin_rate: number;
    broker: string;
    unverified: string | null;
  };
  plan: TradePlan;
}

export const fetchPlan = (pair: string, balance?: number, riskPct?: number) => {
  const q = new URLSearchParams();
  if (balance) q.set("balance", String(balance));
  if (riskPct) q.set("risk_pct", String(riskPct));
  const qs = q.toString();
  return getJson<PlanResponse>(`/api/plan/${pair}${qs ? `?${qs}` : ""}`);
};



// ---------------------------------------------------------------- チャート

export interface ChartResponse {
  version: string;
  pair: string;
  timeframe: string;
  digits: number;
  candles: { t: string; o: number; h: number; l: number; c: number }[];
  overlays: Record<string, (number | null)[]>;
  sub: Record<string, (number | null)[]>;
  // 十字線に出す値。**画面側で計算し直さない。**
  readout?: Record<string, (number | null)[]>;
  // この先どれだけ動きうるかの幅。**向きの予想ではない。**
  // 測っていない時間足では null が返る（推測で描かないため）。
  forecast?: ForecastBand | null;
}

/** この先の値動きの幅。実データから測った倍率と、確かめた覆い率。 */
export interface ForecastBand {
  basis: string;
  quantile: number | null;
  measured_at: string | null;
  pairs: number | null;
  coverage_low: number | null;
  coverage_high: number | null;
  levels: {
    bars: number;
    low: number;
    high: number;
    atr_multiple: number;
    measured_coverage: number | null;
    checked_on: number | null;
  }[];
  note: string;
}

export const CHART_TIMEFRAMES = ["H4", "H1", "M15", "M5"] as const;

export const fetchCandles = (pair: string, timeframe: string, limit = 150) =>
  getJson<ChartResponse>(
    `/api/candles/${pair}?timeframe=${timeframe}&limit=${limit}`
  );

// 朝のボード。**予測はしない。** 事実（値幅・位置・水準）と、3つの要因の
// 向き（▲▼■）と、機械が止める理由（除外）だけを出す。決めるのは使う人。
export type View = "up" | "down" | "neutral" | "none";

export interface BoardLevel {
  price: number;
  label: string;
  kind: "support" | "resistance" | "round" | "open" | string;
}

export interface BoardFacts {
  price: number | null;
  price_at: string | null;
  today_open: number | null;
  today_high: number | null;
  today_low: number | null;
  today_change_pct: number | null;
  prev_high: number | null;
  prev_low: number | null;
  prev_note: string | null;
  adr: number | null;
  adr_days: number;
  used_ratio: number | null;
  range_high: number | null;
  range_low: number | null;
  position: number | null;
  room_up_adr: number | null;
  room_down_adr: number | null;
  round_above: number | null;
  round_below: number | null;
  session: string;
  minutes_to_deadline: number | null;
  deadline_jst: string | null;
  next_event: string | null;
  minutes_to_event: number | null;
  event_note: string | null;
  market_open: boolean;
  day_note: string | null;
  levels: BoardLevel[];
}

export interface BoardFactor {
  key: "trend" | "momentum" | "fundamentals" | string;
  label: string;
  view: View;
  mark: string;
  text: string;
  source: string | null;
  as_of: string | null;
  stale: boolean;
}

export interface BoardVerdict {
  state: "excluded" | "aligned_up" | "aligned_down" | "mixed" | string;
  label: string;
  lean: "up" | "down" | null;
  counts: Record<View, number>;
  exclude: string[];
  cautions: string[];
  note: string;
}

// 取り込んだ FXモーニングブリーフの、この銘柄の判定。**アプリの判定とは別物。**
// 除外の理由には使わず、並べて見せ、後で採点する。
export interface BriefSignal {
  view: "up" | "down" | "neutral";
  mark: string;
  text: string;
}

export interface BoardBrief {
  brief_at: string | null;
  brief_date: string | null;
  age_days: number | null;
  url: string | null;
  lean: "up" | "down" | "neutral" | "conflict" | null;
  excluded: boolean;
  summary: string;
  signals: Partial<Record<"trend" | "momentum" | "fundamentals" | "range", BriefSignal>>;
}

export interface Board {
  pair: string;
  digits: number;
  generated_at: string;
  facts: BoardFacts;
  factors: BoardFactor[];
  verdict: BoardVerdict;
  brief: BoardBrief | null;
}

export interface BoardResponse {
  version: string;
  boards: Board[];
}

export const fetchBoard = (pair?: string) =>
  getJson<BoardResponse>(pair ? `/api/board?pair=${pair}` : "/api/board");
