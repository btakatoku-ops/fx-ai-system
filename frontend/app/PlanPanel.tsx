"use client";

// 自分向けの計画（Phase 2）。**向きは使う人が選ぶ。基準価格は MT4 の bid・ask を手で入れる。**
//
// - 除外の日は、向きを選んでも計画は出ない（理由コードが返る）。
// - PLAN_OK でも儲かる根拠ではない。注記を必ず出す。
// - 発注はしない。数字は取引画面で自分で入れる。
import { useActionState, useState } from "react";

import type { PlanCalcResult } from "@/lib/api";

import { runPlan, type PlanState } from "./actions";

const initial: PlanState = { error: "", result: null };

const yen = (n: number) => `${n.toLocaleString("ja-JP")}円`;

function Result({ r }: { r: PlanCalcResult }) {
  const until = r.quote ? new Date(r.quote.valid_until) : null;
  if (r.status !== "PLAN_OK" || !r.plan) {
    return (
      <div className="pr no">
        <span className="st no">計画を出しません</span>
        <ul className="plain">
          {r.stops.map((s, i) => (
            <li key={i}>
              {s.message} <code>{s.code}</code>
            </li>
          ))}
        </ul>
      </div>
    );
  }
  const p = r.plan;
  return (
    <div className="pr ok">
      <span className="st ok">計画</span>
      <table>
        <tbody>
          <tr>
            <th>建値（{p.direction === "LONG" ? "ask" : "bid"}）</th>
            <td className="num">{p.entry}</td>
          </tr>
          <tr>
            <th>損切り</th>
            <td className="num">
              {p.stop} <span className="muted">{p.stop_basis}</span>
            </td>
          </tr>
          {p.targets.map((t) => (
            <tr key={t.label}>
              <th>{t.label}</th>
              <td className="num">
                {t.price} <span className="muted">費用込み {t.net_rr} 倍{t.capped_by ? `・${t.capped_by}の手前` : ""}</span>
              </td>
            </tr>
          ))}
          <tr>
            <th>数量</th>
            <td className="num">
              <b>{p.qty}</b> <span className="muted">（{p.units.toLocaleString("ja-JP")}通貨）</span>
            </td>
          </tr>
          <tr>
            <th>損切りに当たったら</th>
            <td className="num loss">
              −{yen(p.loss_jpy)} <span className="muted">許容 {yen(p.risk_amount_jpy)}</span>
            </td>
          </tr>
          {p.targets.map((t) => (
            <tr key={`p-${t.label}`}>
              <th>{t.label} に届いたら</th>
              <td className="num gain">＋{yen(t.profit_jpy)}</td>
            </tr>
          ))}
          <tr>
            <th>必要証拠金（推定）</th>
            <td className="num">
              {yen(p.margin_jpy)} <span className="muted">資金の {p.margin_pct}%</span>
            </td>
          </tr>
        </tbody>
      </table>
      {p.notes.map((n, i) => (
        <p key={i} className="muted">{n}</p>
      ))}
      {until && (
        <p className="muted">
          この計画は {until.toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" })} まで。
          過ぎたら MT4 の値を入れ直してください。
        </p>
      )}
    </div>
  );
}

export default function PlanPanel({
  pair,
  excluded,
  hint,
}: {
  pair: string;
  excluded: boolean;
  hint: string;          // 入力例（取り込んだ足の終値。MT4 の値ではない）
}) {
  const [state, action, pending] = useActionState(runPlan, initial);
  const [dir, setDir] = useState<"LONG" | "SHORT">("LONG");
  return (
    <details className="planpanel">
      <summary className="planbtn">
        計画を見る
        <span className="sub">買い／売りを選び、MT4 の bid・ask を入れる</span>
        {excluded ? <span className="sub">今日は除外の日なので、計画は出ません</span> : null}
      </summary>
      <form action={action}>
        <input type="hidden" name="pair" value={pair} />
        <input type="hidden" name="direction" value={dir} />
        <div className="dirs" role="group" aria-label="向き">
          <button type="button" className={dir === "LONG" ? "on up" : ""} onClick={() => setDir("LONG")}>
            ▲ 買いで見る
          </button>
          <button type="button" className={dir === "SHORT" ? "on down" : ""} onClick={() => setDir("SHORT")}>
            ▼ 売りで見る
          </button>
        </div>
        <p className="howto">
          MT4 の「気配値表示」にある<b>売値（Bid）と買値（Ask）をそのまま</b>入れてください。
          買い・売りどちらでも同じ2つです。買値の方が少し高く、差がスプレッドです。
          {dir === "SHORT" ? "売りは売値（Bid）で建てる計算になります。" : "買いは買値（Ask）で建てる計算になります。"}
          損切り・利確はこちらで計算します。
        </p>
        <div className="quotes">
          <label>
            売値（Bid）
            <input key={`b-${state.bid ?? ""}`} name="bid" inputMode="decimal" autoComplete="off"
              placeholder={hint} defaultValue={state.bid ?? ""} required />
          </label>
          <label>
            買値（Ask）
            <input key={`a-${state.ask ?? ""}`} name="ask" inputMode="decimal" autoComplete="off"
              placeholder={hint} defaultValue={state.ask ?? ""} required />
          </label>
        </div>
        <button type="submit" className="go" disabled={pending}>
          {pending ? "計算しています…" : "計画を出す"}
        </button>
        {state.error && <p className="err" aria-live="polite">{state.error}</p>}
      </form>
      {state.result && <Result r={state.result} />}
      {state.result && (
        <div className="muted small">
          {state.result.disclaimers.map((d, i) => (
            <p key={i}>{d}</p>
          ))}
          {state.result.assumptions && (
            <p>
              仮定：滑り 片道 {state.result.assumptions.slippage_pips_per_side}pips・円換算の余裕{" "}
              {Number(state.result.assumptions.conversion_stress) * 100}%。発注はしません。
            </p>
          )}
        </div>
      )}
    </details>
  );
}
