"use client";

// ファンダの見立てを入れる欄。**機械は作らない。** 出典は必須で、
// 日付は保存した日になる。7日を過ぎると判定に数えなくなる（画面には残る）。
import { useActionState } from "react";

import type { BoardFactor } from "@/lib/api";

import { saveFundamentals, type FundamentalsState } from "./actions";

const initial: FundamentalsState = { ok: false, message: "" };

export default function FundamentalsForm({
  pair,
  current,
}: {
  pair: string;
  current: BoardFactor | undefined;
}) {
  const [state, action, pending] = useActionState(saveFundamentals, initial);
  const view = current && current.view !== "none" ? current.view : "neutral";
  return (
    <details className="fundform">
      <summary>ファンダの見立てを書き換える</summary>
      <form action={action}>
        <input type="hidden" name="pair" value={pair} />
        <fieldset>
          <legend>向き</legend>
          {(
            [
              ["up", "▲ 上"],
              ["neutral", "■ 中立"],
              ["down", "▼ 下"],
            ] as const
          ).map(([v, label]) => (
            <label key={v}>
              <input type="radio" name="view" value={v} defaultChecked={view === v} />
              {label}
            </label>
          ))}
        </fieldset>
        <label className="field">
          中身（金利差、要人発言、介入警戒など）
          <textarea
            name="note"
            rows={2}
            defaultValue={current?.stale ? "" : (current?.text ?? "")}
          />
        </label>
        <label className="field">
          出典（必須。誰の見立てか）
          <input
            name="source"
            required
            placeholder="例: 自分の見立て／○○証券の朝のレポート"
            defaultValue={current?.stale ? "" : (current?.source ?? "")}
          />
        </label>
        <button type="submit" disabled={pending}>
          {pending ? "保存しています…" : "保存する（日付は今日）"}
        </button>
        {state.message && (
          <p className={state.ok ? "ok" : "err"} aria-live="polite">
            {state.message}
          </p>
        )}
      </form>
    </details>
  );
}
