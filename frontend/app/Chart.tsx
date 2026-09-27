"use client";

// ローソク足チャート。**表示のためだけの部品で、判断には関わらない。**
//
// MT4 で見慣れた指標（EMA・ボリンジャー・RSI・MACD）を、TradingView の
// 操作感（十字線・拡大縮小・掴んで移動・凡例のその場読み）で見る。
//
// 3つ、意識して守っていること。
//
// 1. **指標の値はサーバーから受け取る。** ここで計算し直すと、同じ指標の
//    実装が2つになって静かに食い違う。計算は engine 側に1つだけ置く。
// 2. **建玉の線（Entry / SL / TP）は、計画がそのまま持っている値を引く。**
//    チャート用に丸めたり作り直したりしない。
// 3. **チャートは発注しない。** 押せるのは表示の切り替えだけ。
//
// 外部の描画ライブラリは使っていない。依存を増やさずに済むのと、
// 動きのない部分はそのまま SVG なので、出るまでが速い。

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { entryMid, planMarks, type PlanMarks } from "@/lib/chart";

export type { PlanMarks };

export interface Candle {
  t: string;
  o: number;
  h: number;
  l: number;
  c: number;
}

export interface ChartData {
  pair: string;
  timeframe: string;
  digits: number;
  candles: Candle[];
  overlays: Record<string, (number | null)[]>;
  sub: Record<string, (number | null)[]>;
  readout?: Record<string, (number | null)[]>;
  forecast?: ForecastBand | null;
}

/** この先どれだけ動きうるかの幅。**向きの予想ではない。** */
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

const W = 980;
const H_PRICE = 330;
const H_SUB = 78;
const PAD_L = 8;
const PAD_R = 74;
const PAD_T = 12;
const GAP = 12;

const UP = "#2fb37a";
const DOWN = "#e0575f";
const GRID = "#222936";
const AXIS = "#8d97aa";
const CROSS = "#7f8ca3";

const MIN_BARS = 25;

type Line = {
  key: string;
  color: string;
  label: string;
  dash?: string;
  stepped?: boolean;
  group: string;
};

// 支持帯・抵抗帯は階段状に動くので、線でつなぐと実体のない斜めの線が
// 引かれる。**持ち合いの戦略が見ている「帯」そのもの**なので、形を
// 変えずに出す（stepped）。
const OVERLAY_LINES: Line[] = [
  { key: "ema20", color: "#e8c46a", label: "EMA20", group: "ema" },
  { key: "ema50", color: "#6aa9e8", label: "EMA50", group: "ema" },
  { key: "ema200", color: "#b78ae8", label: "EMA200", group: "ema" },
  { key: "bb_upper", color: "#4a5568", label: "BB", dash: "3 3", group: "bb" },
  { key: "bb_lower", color: "#4a5568", label: "", dash: "3 3", group: "bb" },
  {
    key: "resistance",
    color: "#49a39b",
    label: "支持/抵抗",
    dash: "6 4",
    stepped: true,
    group: "sr",
  },
  {
    key: "support",
    color: "#49a39b",
    label: "",
    dash: "6 4",
    stepped: true,
    group: "sr",
  },
];

const TOGGLES: { group: string; label: string }[] = [
  { group: "ema", label: "EMA" },
  { group: "bb", label: "ボリンジャー" },
  { group: "sr", label: "支持/抵抗" },
  { group: "plan", label: "建玉の目安" },
  { group: "forecast", label: "この先の幅" },
  { group: "rsi", label: "RSI" },
  { group: "macd", label: "MACD" },
];

function niceTicks(lo: number, hi: number, count = 5): number[] {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 5 ? 5 : norm >= 2 ? 2 : 1) * mag;
  const first = Math.ceil(lo / step) * step;
  const out: number[] = [];
  for (let v = first; v <= hi + step * 0.01; v += step) out.push(v);
  return out;
}

function clock(t: string, timeframe: string): string {
  const d = new Date(t);
  const day = `${d.getMonth() + 1}/${d.getDate()}`;
  const hh = String(d.getHours()).padStart(2, "0");
  if (timeframe === "H4" || timeframe === "H1") return `${day} ${hh}時`;
  return `${day} ${hh}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export default function Chart({
  data,
  plan,
}: {
  data: ChartData;
  plan?: PlanMarks | null;
}) {
  const all = data.candles;
  const n = all.length;

  // 見える範囲。**末尾を既定にする。** 途中を既定にすると、いま何を
  // 見ているのか分からないまま読むことになる。
  const [view, setView] = useState({ from: Math.max(0, n - 120), to: n });
  const [hover, setHover] = useState<number | null>(null);
  const [off, setOff] = useState<Record<string, boolean>>({});
  const [grabbing, setGrabbing] = useState(false);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const drag = useRef<{ x: number; from: number; to: number } | null>(null);

  // 時間足やペアを切り替えると本数が変わる。**古い範囲を持ち越さない。**
  useEffect(() => {
    setView({ from: Math.max(0, n - 120), to: n });
    setHover(null);
  }, [n, data.timeframe, data.pair]);

  const on = (g: string) => !off[g];
  const toggle = (g: string) => setOff((p) => ({ ...p, [g]: !p[g] }));

  const from = Math.max(0, Math.min(view.from, Math.max(0, n - MIN_BARS)));
  const to = Math.min(n, Math.max(view.to, from + MIN_BARS));

  const candles = useMemo(() => all.slice(from, to), [all, from, to]);
  const count = candles.length;
  const slice = useCallback(
    (arr: (number | null)[] | undefined) => (arr ?? []).slice(from, to),
    [from, to]
  );

  if (count < 2) {
    return <p className="muted">チャートに出せる足がありません。</p>;
  }

  const visibleLines = OVERLAY_LINES.filter((l) => on(l.group));

  // 価格の範囲は、足・重ねる線・建玉の線がすべて入るように取る
  let lo = Math.min(...candles.map((c) => c.l));
  let hi = Math.max(...candles.map((c) => c.h));
  for (const line of visibleLines) {
    for (const v of slice(data.overlays[line.key])) {
      if (v === null || !Number.isFinite(v)) continue;
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
    }
  }
  const planOn = Boolean(plan && plan.ok && on("plan"));
  if (planOn && plan) {
    const levels = [plan.entryLow, plan.entryHigh, plan.stop]
      .concat(plan.targets.map((t) => t.price))
      .filter((v): v is number => v !== null && Number.isFinite(v));
    for (const v of levels) {
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
    }
  }
  // この先の幅。**向きは予想しない。** 右側に場所を取って、最後の終値から
  // 広がる帯として描く。倍率も覆い率もサーバーが測った値で、画面では
  // 何も計算しない。
  const band = data.forecast ?? null;
  const fcOn = Boolean(band && band.levels.length && on("forecast"));
  const future = fcOn && band ? band.levels[band.levels.length - 1].bars : 0;
  if (fcOn && band) {
    for (const l of band.levels) {
      lo = Math.min(lo, l.low);
      hi = Math.max(hi, l.high);
    }
  }

  const span = hi - lo || 1;
  lo -= span * 0.05;
  hi += span * 0.05;

  const plotW = W - PAD_L - PAD_R;
  const slot = plotW / (count + future);
  const body = Math.max(1.2, Math.min(9, slot * 0.64));
  const x = (i: number) => PAD_L + slot * (i + 0.5);
  const y = (v: number) => PAD_T + ((hi - v) * (H_PRICE - PAD_T * 2)) / (hi - lo);

  const path = (key: string, stepped = false) => {
    const vals = slice(data.overlays[key]);
    let d = "";
    let prev: number | null = null;
    for (let i = 0; i < count; i++) {
      const v = vals[i];
      if (v === null || v === undefined || !Number.isFinite(v)) {
        prev = null;
        continue;
      }
      if (prev === null) {
        d += `M${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      } else if (stepped && v !== prev) {
        d += `L${x(i).toFixed(1)},${y(prev).toFixed(1)}`;
        d += `L${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      } else {
        d += `L${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      }
      prev = v;
    }
    return d;
  };

  // 下段。RSI と MACD は出し入れできる
  const panes: string[] = [];
  if (on("rsi")) panes.push("rsi");
  if (on("macd")) panes.push("macd");
  const paneTop = (k: string) =>
    H_PRICE + GAP + panes.indexOf(k) * (H_SUB + GAP);
  const totalH = H_PRICE + (panes.length ? panes.length * (H_SUB + GAP) + 6 : 8);

  const rsi = slice(data.sub.rsi14);
  const ry = (v: number) => paneTop("rsi") + ((100 - v) * (H_SUB - 14)) / 100 + 7;
  let rsiPath = "";
  let pen = false;
  for (let i = 0; i < count; i++) {
    const v = rsi[i];
    if (v === null || v === undefined || !Number.isFinite(v)) {
      pen = false;
      continue;
    }
    rsiPath += `${pen ? "L" : "M"}${x(i).toFixed(1)},${ry(v).toFixed(1)}`;
    pen = true;
  }

  const hist = slice(data.sub.macd_hist);
  const macd = slice(data.sub.macd);
  const macdSig = slice(data.sub.macd_signal);
  const macdVals = [...hist, ...macd, ...macdSig].filter(
    (v): v is number => v !== null && v !== undefined && Number.isFinite(v)
  );
  const mMax = Math.max(1e-9, ...macdVals.map((v) => Math.abs(v)));
  const my = (v: number) =>
    paneTop("macd") + H_SUB / 2 - (v / mMax) * (H_SUB / 2 - 8);
  const macdLine = (arr: (number | null)[]) => {
    let d = "";
    let p = false;
    for (let i = 0; i < count; i++) {
      const v = arr[i];
      if (v === null || v === undefined || !Number.isFinite(v)) {
        p = false;
        continue;
      }
      d += `${p ? "L" : "M"}${x(i).toFixed(1)},${my(v).toFixed(1)}`;
      p = true;
    }
    return d;
  };

  const ticks = niceTicks(lo, hi);
  const fmt = (v: number) => v.toFixed(data.digits);
  const last = candles[count - 1];

  // ---- 十字線・拡大縮小・移動 -----------------------------------------
  const point = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return null;
    const r = svg.getBoundingClientRect();
    return {
      sx: ((e.clientX - r.left) / r.width) * W,
      sy: ((e.clientY - r.top) / r.height) * totalH,
    };
  };

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const p = point(e);
    if (!p) return;
    if (drag.current) {
      const moved = Math.round(((drag.current.x - p.sx) / plotW) * count);
      const width = drag.current.to - drag.current.from;
      const f = Math.max(0, Math.min(drag.current.from + moved, n - width));
      setView({ from: f, to: f + width });
      return;
    }
    const i = Math.floor((p.sx - PAD_L) / slot);
    setHover(i >= 0 && i < count ? i : null);
  };

  const onWheel = (e: React.WheelEvent<SVGSVGElement>) => {
    const p = point(e);
    if (!p) return;
    const next = Math.round((to - from) * (e.deltaY > 0 ? 1.18 : 0.85));
    const w = Math.max(MIN_BARS, Math.min(n, next));
    // 触っている足を軸に伸び縮みさせる（見ていた場所が画面の外へ飛ばない）
    const at = Math.max(0, Math.min(count - 1, Math.floor((p.sx - PAD_L) / slot)));
    const anchor = from + at;
    const f = Math.max(
      0,
      Math.min(Math.round(anchor - (at / Math.max(1, count - 1)) * (w - 1)), n - w)
    );
    setView({ from: f, to: f + w });
  };

  const onDown = (e: React.MouseEvent<SVGSVGElement>) => {
    const p = point(e);
    if (!p) return;
    drag.current = { x: p.sx, from, to };
    setGrabbing(true);
  };
  const endDrag = () => {
    drag.current = null;
    setGrabbing(false);
  };

  const hovered = hover !== null && hover < count ? hover : null;
  const shown = hovered === null ? count - 1 : hovered;
  const cur = candles[shown];
  const readAt = (arr: (number | null)[] | undefined) => {
    const v = slice(arr)[shown];
    return v === null || v === undefined || !Number.isFinite(v) ? null : v;
  };
  const num = (v: number | null, d = 2) => (v === null ? "—" : v.toFixed(d));

  const isLong = plan?.direction === "LONG";
  const mid = entryMid(plan);

  // **線の中身は lib/chart.ts で組み立てる。** BUY/SELL のときしか画面に
  // 出ないので、出す・出さないの決まりを試験で確かめられるようにしてある。
  const COLOR: Record<string, string> = {
    entry: "#e6eaf2",
    stop: DOWN,
    target: UP,
  };
  const marks = planOn ? planMarks(plan, fmt) : [];

  return (
    <div className="chart">
      <div className="chart-head">
        <strong>{data.pair}</strong>
        <span className="muted">
          {" "}
          {data.timeframe} ／ {from + 1}〜{to} 本目（全 {n} 本）
        </span>
        <span className="legend">
          {TOGGLES.map((t) => (
            <button
              key={t.group}
              type="button"
              className={`chip${on(t.group) ? " on" : ""}`}
              onClick={() => toggle(t.group)}
              aria-pressed={on(t.group)}
            >
              {t.label}
            </button>
          ))}
        </span>
      </div>

      {/* 凡例。**十字線を合わせた足の値を、その場で数字で出す。**
          目盛りから読み取らせない。 */}
      <div className="readout">
        <strong>{clock(cur.t, data.timeframe)}</strong>
        <span>
          始 <b>{fmt(cur.o)}</b> 高 <b>{fmt(cur.h)}</b> 安 <b>{fmt(cur.l)}</b> 終{" "}
          <b style={{ color: cur.c >= cur.o ? UP : DOWN }}>{fmt(cur.c)}</b>
        </span>
        {on("ema") && (
          <>
            <span style={{ color: "#e8c46a" }}>
              EMA20 {num(readAt(data.overlays.ema20), data.digits)}
            </span>
            <span style={{ color: "#6aa9e8" }}>
              EMA50 {num(readAt(data.overlays.ema50), data.digits)}
            </span>
            <span style={{ color: "#b78ae8" }}>
              EMA200 {num(readAt(data.overlays.ema200), data.digits)}
            </span>
          </>
        )}
        {on("rsi") && <span>RSI {num(readAt(data.sub.rsi14), 1)}</span>}
        {on("macd") && (
          <span>MACD {num(readAt(data.sub.macd_hist), data.digits + 1)}</span>
        )}
        <span>ATR {num(readAt(data.readout?.atr14), data.digits)}</span>
        <span>ADX {num(readAt(data.readout?.adx14), 1)}</span>
        {hovered === null && <span className="muted">（最新の足）</span>}
      </div>

      <div className="table-wrap">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${totalH}`}
          width="100%"
          role="img"
          aria-label={`${data.pair} ${data.timeframe} のローソク足チャート`}
          onMouseMove={onMove}
          onMouseLeave={() => {
            setHover(null);
            endDrag();
          }}
          onWheel={onWheel}
          onMouseDown={onDown}
          onMouseUp={endDrag}
          style={{ cursor: grabbing ? "grabbing" : "crosshair" }}
        >
          {/* 価格の目盛り */}
          {ticks.map((v) => (
            <g key={v}>
              <line
                x1={PAD_L}
                x2={W - PAD_R}
                y1={y(v)}
                y2={y(v)}
                stroke={GRID}
                strokeWidth="1"
              />
              <text
                x={W - PAD_R + 6}
                y={y(v) + 3.5}
                fill={AXIS}
                fontSize="11"
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {fmt(v)}
              </text>
            </g>
          ))}

          {/* 建玉の目安。**損切りまでを赤、利確までを緑で塗る。**
              帯の厚みが、賭けている幅と狙っている幅そのものになる。 */}
          {planOn && plan && mid !== null && plan.stop !== null && (
            <rect
              x={PAD_L}
              y={Math.min(y(mid), y(plan.stop))}
              width={plotW}
              height={Math.abs(y(plan.stop) - y(mid))}
              fill={DOWN}
              opacity="0.09"
            />
          )}
          {planOn && plan && mid !== null && plan.targets[0] && (
            <rect
              x={PAD_L}
              y={Math.min(y(mid), y(plan.targets[0].price))}
              width={plotW}
              height={Math.abs(y(plan.targets[0].price) - y(mid))}
              fill={UP}
              opacity="0.08"
            />
          )}

          {/* この先の幅。**上下どちらに動くかは何も言っていない。** */}
          {fcOn && band && (
            <g>
              <path
                d={
                  `M${x(count - 1).toFixed(1)},${y(last.c).toFixed(1)}` +
                  band.levels
                    .map(
                      (l) =>
                        `L${x(count - 1 + l.bars).toFixed(1)},${y(l.high).toFixed(1)}`
                    )
                    .join("") +
                  band.levels
                    .slice()
                    .reverse()
                    .map(
                      (l) =>
                        `L${x(count - 1 + l.bars).toFixed(1)},${y(l.low).toFixed(1)}`
                    )
                    .join("") +
                  "Z"
                }
                fill="#6aa9e8"
                opacity="0.10"
              />
              {[0, 1].map((side) => (
                <path
                  key={side}
                  d={
                    `M${x(count - 1).toFixed(1)},${y(last.c).toFixed(1)}` +
                    band.levels
                      .map(
                        (l) =>
                          `L${x(count - 1 + l.bars).toFixed(1)},${y(
                            side ? l.low : l.high
                          ).toFixed(1)}`
                      )
                      .join("")
                  }
                  fill="none"
                  stroke="#6aa9e8"
                  strokeWidth="1"
                  strokeDasharray="4 4"
                  opacity="0.8"
                />
              ))}
              <line
                x1={x(count - 1)}
                x2={x(count - 1)}
                y1={PAD_T}
                y2={H_PRICE - PAD_T}
                stroke={AXIS}
                strokeWidth="1"
                strokeDasharray="1 4"
                opacity="0.6"
              />
            </g>
          )}

          {/* 重ねる線 */}
          {visibleLines.map((l) => (
            <path
              key={l.key}
              d={path(l.key, l.stepped)}
              fill="none"
              stroke={l.color}
              strokeWidth="1.3"
              strokeDasharray={l.dash}
              opacity="0.9"
            />
          ))}

          {/* ローソク足 */}
          {candles.map((c, i) => {
            const up = c.c >= c.o;
            const col = up ? UP : DOWN;
            const top = Math.min(y(c.o), y(c.c));
            const h = Math.max(1, Math.abs(y(c.c) - y(c.o)));
            return (
              <g key={c.t}>
                <line
                  x1={x(i)}
                  x2={x(i)}
                  y1={y(c.h)}
                  y2={y(c.l)}
                  stroke={col}
                  strokeWidth="1"
                />
                <rect
                  x={x(i) - body / 2}
                  y={top}
                  width={body}
                  height={h}
                  fill={col}
                />
              </g>
            );
          })}

          {/* 建玉の線と札 */}
          {marks.map((m) => (
            <g key={m.text}>
              <line
                x1={PAD_L}
                x2={W - PAD_R}
                y1={y(m.value)}
                y2={y(m.value)}
                stroke={COLOR[m.kind]}
                strokeWidth="1.2"
                strokeDasharray="7 5"
                opacity="0.95"
              />
              <text
                x={PAD_L + 6}
                y={y(m.value) - 4}
                fill={COLOR[m.kind]}
                fontSize="10.5"
                fontWeight="600"
              >
                {m.text}
              </text>
            </g>
          ))}

          {/* 現在値 */}
          <line
            x1={PAD_L}
            x2={W - PAD_R}
            y1={y(last.c)}
            y2={y(last.c)}
            stroke="#8d97aa"
            strokeWidth="1"
            strokeDasharray="4 4"
            opacity="0.7"
          />
          <rect
            x={W - PAD_R + 2}
            y={y(last.c) - 8}
            width={PAD_R - 4}
            height={16}
            fill="#2a3242"
            rx="2"
          />
          <text
            x={W - PAD_R + 6}
            y={y(last.c) + 3.5}
            fill="#e6eaf2"
            fontSize="11"
            fontWeight="600"
            style={{ fontVariantNumeric: "tabular-nums" }}
          >
            {fmt(last.c)}
          </text>

          {/* 売買の印。**最新の足の位置に、向きごと出す。** */}
          {plan && plan.direction !== "NEUTRAL" && (
            <g>
              <path
                d={
                  isLong
                    ? `M${x(count - 1)},${y(last.l) + 20} l-6,11 l12,0 z`
                    : `M${x(count - 1)},${y(last.h) - 20} l-6,-11 l12,0 z`
                }
                fill={isLong ? UP : DOWN}
              />
              <text
                x={Math.min(W - PAD_R - 30, x(count - 1))}
                y={isLong ? y(last.l) + 46 : y(last.h) - 28}
                fill={isLong ? UP : DOWN}
                fontSize="11"
                fontWeight="700"
                textAnchor="middle"
              >
                {plan.signalLabel}
              </text>
            </g>
          )}

          {/* 十字線 */}
          {hovered !== null && (
            <g pointerEvents="none">
              <line
                x1={x(hovered)}
                x2={x(hovered)}
                y1={0}
                y2={totalH}
                stroke={CROSS}
                strokeWidth="1"
                strokeDasharray="2 3"
                opacity="0.8"
              />
              <line
                x1={PAD_L}
                x2={W - PAD_R}
                y1={y(cur.c)}
                y2={y(cur.c)}
                stroke={CROSS}
                strokeWidth="1"
                strokeDasharray="2 3"
                opacity="0.5"
              />
              <rect
                x={Math.max(2, Math.min(W - 86, x(hovered) - 40))}
                y={H_PRICE - 16}
                width={80}
                height={15}
                fill="#2a3242"
                rx="2"
              />
              <text
                x={Math.max(42, Math.min(W - 46, x(hovered)))}
                y={H_PRICE - 5}
                fill="#e6eaf2"
                fontSize="10"
                textAnchor="middle"
              >
                {clock(cur.t, data.timeframe)}
              </text>
            </g>
          )}

          {/* 下段 RSI */}
          {on("rsi") && (
            <g>
              <rect
                x={PAD_L}
                y={paneTop("rsi")}
                width={plotW}
                height={H_SUB}
                fill="none"
                stroke={GRID}
              />
              {[30, 50, 70].map((lv) => (
                <g key={lv}>
                  <line
                    x1={PAD_L}
                    x2={W - PAD_R}
                    y1={ry(lv)}
                    y2={ry(lv)}
                    stroke={GRID}
                    strokeWidth="1"
                    strokeDasharray={lv === 50 ? "2 4" : undefined}
                  />
                  <text
                    x={W - PAD_R + 6}
                    y={ry(lv) + 3.5}
                    fill={AXIS}
                    fontSize="10"
                  >
                    {lv}
                  </text>
                </g>
              ))}
              <path d={rsiPath} fill="none" stroke="#d9a13b" strokeWidth="1.3" />
              <text
                x={PAD_L + 4}
                y={paneTop("rsi") + 12}
                fill={AXIS}
                fontSize="10"
              >
                RSI14
              </text>
            </g>
          )}

          {/* 下段 MACD */}
          {on("macd") && (
            <g>
              <rect
                x={PAD_L}
                y={paneTop("macd")}
                width={plotW}
                height={H_SUB}
                fill="none"
                stroke={GRID}
              />
              <line
                x1={PAD_L}
                x2={W - PAD_R}
                y1={my(0)}
                y2={my(0)}
                stroke={GRID}
                strokeWidth="1"
              />
              {hist.map((v, i) =>
                v === null || v === undefined || !Number.isFinite(v) ? null : (
                  <rect
                    key={i}
                    x={x(i) - body / 2}
                    y={Math.min(my(v), my(0))}
                    width={body}
                    height={Math.max(1, Math.abs(my(v) - my(0)))}
                    fill={v >= 0 ? UP : DOWN}
                    opacity="0.65"
                  />
                )
              )}
              <path
                d={macdLine(macd)}
                fill="none"
                stroke="#6aa9e8"
                strokeWidth="1.2"
              />
              <path
                d={macdLine(macdSig)}
                fill="none"
                stroke="#e8c46a"
                strokeWidth="1.2"
              />
              <text
                x={PAD_L + 4}
                y={paneTop("macd") + 12}
                fill={AXIS}
                fontSize="10"
              >
                MACD 12/26/9
              </text>
            </g>
          )}
        </svg>
      </div>

      {fcOn && band && (
        <p className="muted">
          <strong>この先の幅</strong>：{band.basis} から{" "}
          {band.levels[band.levels.length - 1].bars} 本先までの帯を出しています。
          {band.coverage_low !== null && band.coverage_high !== null ? (
            <>
              {" "}
              <strong>
                決めた期間とは別の期間で確かめて、実際に{" "}
                {(band.coverage_low * 100).toFixed(0)}〜
                {(band.coverage_high * 100).toFixed(0)}% を覆いました
              </strong>
              （{band.pairs} 銘柄ぶん、{band.measured_at?.slice(0, 10)} 測定）。
            </>
          ) : null}{" "}
          {band.note}
        </p>
      )}
      <p className="muted">
        {new Date(candles[0].t).toLocaleString("ja-JP")} 〜{" "}
        {new Date(last.t).toLocaleString("ja-JP")}
        　／　最後の足は<strong>確定済み</strong>のものだけを表示しています。
        　／　ホイールで拡大縮小、掴んで左右に移動できます。
      </p>
      {plan && !plan.ok && plan.note && (
        <p className="muted">
          建玉の線は出していません（{plan.note}）。
        </p>
      )}
    </div>
  );
}
