// チャートに引く建玉の線を組み立てる。
//
// **描画から切り離してある。** 相場が BUY / SELL のときしか画面に出ない
// ので、画面を開いて確かめられるとは限らない。線を出す・出さないの決まり
// だけは、いつでも試験で確かめられるようにしておく。
//
// 決まりは3つ。
//
// 1. **期限が切れていたら出さない。** 根拠が失われた値を、まだ有効な
//    目安のように見せない。
// 2. **計画が成り立っていない（ok=false）なら出さない。** 損切りを置けない、
//    数量が出ない、といった場合に線だけ引くと、建てられるように見える。
// 3. **値はそのまま引く。** チャート用に丸めたり作り直したりしない。
//    engine の出した値と画面の値が違うと、どちらが本当か分からなくなる。

export interface PlanMarks {
  direction: "LONG" | "SHORT" | "NEUTRAL";
  signalLabel: string;
  entryLow: number | null;
  entryHigh: number | null;
  stop: number | null;
  targets: { label: string; price: number; r: number }[];
  ok: boolean;
  note: string | null;
}

export type MarkKind = "entry" | "stop" | "target";

export interface Mark {
  kind: MarkKind;
  value: number;
  text: string;
}

/** 入る値の中央。上下が揃っていなければ ``null``。 */
export function entryMid(plan: PlanMarks | null | undefined): number | null {
  if (!plan) return null;
  const { entryLow: lo, entryHigh: hi } = plan;
  if (lo === null || hi === null) return null;
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
  return (lo + hi) / 2;
}

/**
 * 引く線の一覧。出さない場合は空。
 *
 * ``fmt`` は値の書き方（銘柄ごとの桁数）。表示のためだけに使う。
 */
export function planMarks(
  plan: PlanMarks | null | undefined,
  fmt: (v: number) => string
): Mark[] {
  if (!plan || !plan.ok) return [];
  const out: Mark[] = [];
  const mid = entryMid(plan);
  if (mid !== null) {
    out.push({
      kind: "entry",
      value: mid,
      text: `Entry ${fmt(plan.entryLow as number)}〜${fmt(
        plan.entryHigh as number
      )}`,
    });
  }
  if (plan.stop !== null && Number.isFinite(plan.stop)) {
    out.push({ kind: "stop", value: plan.stop, text: `SL ${fmt(plan.stop)}` });
  }
  for (const t of plan.targets) {
    if (!Number.isFinite(t.price)) continue;
    out.push({
      kind: "target",
      value: t.price,
      text: `${t.label} ${fmt(t.price)}（${t.r.toFixed(1)}R）`,
    });
  }
  return out;
}
