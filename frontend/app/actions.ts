"use server";

// ファンダの見立てを書き換える。**機械は作らない。使う人が入れる。**
//
// iPhone からは 127.0.0.1:8000 に届かないので、ブラウザから API を直接
// 叩かず、この PC の上（Next のサーバー）から送る。日付は API 側で今日に
// なる。出典が空なら API が受け付けない。
//
// **鍵は掛けていない。** この画面は家の中の回線だけで使う前提で、API の
// 側も同じ。外に出すなら、ここに確認を入れること。
import { revalidatePath } from "next/cache";

import { API_BASE, type PlanCalcResult } from "@/lib/api";

export interface FundamentalsState {
  ok: boolean;
  message: string;
}

const VIEWS = new Set(["up", "down", "neutral"]);

export async function saveFundamentals(
  _prev: FundamentalsState,
  form: FormData
): Promise<FundamentalsState> {
  const pair = String(form.get("pair") ?? "").toUpperCase();
  const view = String(form.get("view") ?? "");
  const note = String(form.get("note") ?? "").trim();
  const source = String(form.get("source") ?? "").trim();

  if (!/^[A-Z]{6}$/.test(pair)) return { ok: false, message: "銘柄が不正です" };
  if (!VIEWS.has(view)) return { ok: false, message: "向きを選んでください" };
  if (!source) {
    return { ok: false, message: "出典を入れてください（誰の見立てか分からないものは使いません）" };
  }

  try {
    const res = await fetch(`${API_BASE}/api/fundamentals/${pair}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ view, note, source }),
      cache: "no-store",
    });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      return {
        ok: false,
        message: `保存できません（${res.status}）${body?.detail ? `: ${body.detail}` : ""}`,
      };
    }
  } catch (err) {
    return { ok: false, message: `API に繋がりません: ${String(err)}` };
  }
  revalidatePath("/");
  return { ok: true, message: "保存しました。日付は今日になります" };
}

// ------------------------------------------------------------ 自分向けの計画

export interface PlanState {
  error: string;
  result: PlanCalcResult | null;
  // 入れた値を残す（送ると欄が空になるので、入れ直さなくて済むように）
  bid?: string;
  ask?: string;
}

const NUM = /^\d+(\.\d+)?$/;

export async function runPlan(_prev: PlanState, form: FormData): Promise<PlanState> {
  const pair = String(form.get("pair") ?? "").toUpperCase();
  const direction = String(form.get("direction") ?? "");
  const bid = String(form.get("bid") ?? "").trim();
  const ask = String(form.get("ask") ?? "").trim();

  const keep = { bid, ask };
  if (!/^[A-Z]{6}$/.test(pair)) return { error: "銘柄が不正です", result: null, ...keep };
  if (direction !== "LONG" && direction !== "SHORT")
    return { error: "買いか売りを選んでください", result: null, ...keep };
  if (!NUM.test(bid) || !NUM.test(ask))
    return { error: "MT4 の bid と ask を数字で入れてください", result: null, ...keep };

  try {
    const res = await fetch(`${API_BASE}/api/plan-calc/${pair}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ direction, bid, ask }),
      cache: "no-store",
    });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      return { error: `計画を出せません（${res.status}）${body?.detail ? `: ${body.detail}` : ""}`, result: null, ...keep };
    }
    return { error: "", result: (await res.json()) as PlanCalcResult, ...keep };
  } catch (err) {
    return { error: `API に繋がりません: ${String(err)}`, result: null, ...keep };
  }
}
