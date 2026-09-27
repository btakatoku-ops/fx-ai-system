// 朝のボードの表示用の計算。**画面の部品から切り離して、試験できるようにする。**
import type { BoardFacts, BoardLevel } from "./api";

export interface RangeBar {
  // 帯の両端（20日の高安と今日の高安の広いほう）
  lo: number;
  hi: number;
  // 0〜100 の位置。値が無ければ null
  price: number | null;
  todayFrom: number | null;
  todayTo: number | null;
}

const pct = (x: number | null, lo: number, hi: number): number | null => {
  if (x === null || !Number.isFinite(x) || hi <= lo) return null;
  return Math.min(100, Math.max(0, ((x - lo) / (hi - lo)) * 100));
};

/** レンジの帯。**20日の高安が取れていなければ描かない（null）。** */
export function rangeBar(f: BoardFacts): RangeBar | null {
  if (f.range_low === null || f.range_high === null) return null;
  const lo = Math.min(f.range_low, f.today_low ?? f.range_low);
  const hi = Math.max(f.range_high, f.today_high ?? f.range_high);
  if (!(hi > lo)) return null;
  return {
    lo,
    hi,
    price: pct(f.price, lo, hi),
    todayFrom: pct(f.today_low, lo, hi),
    todayTo: pct(f.today_high, lo, hi),
  };
}

export interface NearLevel extends BoardLevel {
  // いまの値からの距離（普段の1日の値幅で割ったもの）。上は正、下は負
  adr: number | null;
}

/**
 * いまの値に近い水準を、上と下から n 本ずつ。
 *
 * **遠い水準を並べても判断の材料にならない。** 今日届きそうなものだけ。
 * 距離は ADR（普段の1日の値幅）で測る。銘柄の値の大きさに左右されない。
 */
export function nearLevels(
  f: BoardFacts,
  n = 2
): { above: NearLevel[]; below: NearLevel[] } {
  const p = f.price;
  if (p === null) return { above: [], below: [] };
  const dist = (x: number) => (f.adr && f.adr > 0 ? (x - p) / f.adr : null);
  const withDist = f.levels.map((l) => ({ ...l, adr: dist(l.price) }));
  const above = withDist
    .filter((l) => l.price > p)
    .sort((a, b) => a.price - b.price)
    .slice(0, n);
  const below = withDist
    .filter((l) => l.price <= p)
    .sort((a, b) => b.price - a.price)
    .slice(0, n);
  return { above, below };
}

/** 今日の値幅の消化。**0.9 で注意、1.2 で除外**（board.json と同じ目安）。 */
export function usedLevel(
  used: number | null,
  caution = 0.9,
  exclude = 1.2
): "none" | "ok" | "caution" | "exclude" {
  if (used === null || !Number.isFinite(used)) return "none";
  if (used >= exclude) return "exclude";
  if (used >= caution) return "caution";
  return "ok";
}

export const fmt = (x: number | null, digits: number) =>
  x === null || !Number.isFinite(x) ? "—" : x.toFixed(digits);
