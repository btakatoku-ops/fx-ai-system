// 総合判断。**材料をひとつずつ並べ、最後に「いまどうするか」を書く。**
//
// 気をつけていること。
//
// 1. **材料ごとに、支持・反対・不明のどれかを必ず出す。** 都合の良い
//    材料だけ並べると、根拠が実際より強く見える。
// 2. **「不明」を「中立」と混ぜない。** 測れていないことは、支えでも
//    反対でもなく、分かっていないという意味。
// 3. **強制の見送りは最上段に置く。** 点数がいくら高くても覆らない。
// 4. **点数は勝率ではない。** 優位性は実測できていない（docs/backtesting.md）。
//    ここで「勝てる」と読めることは書かない。

import {
  DIRECTION_JA,
  MAX_ATTAINABLE_SCORE,
  REGIME_JA,
  SIGNAL_CLASS,
  SIGNAL_MARK,
  type AnalysisResult,
  type PlanResponse,
  type StrategyInfo,
} from "@/lib/api";

type Verdict = "for" | "against" | "unknown" | "block";

const TAG: Record<Verdict, string> = {
  for: "支持",
  against: "反対",
  unknown: "不明",
  block: "停止",
};

interface Item {
  name: string;
  reading: string;
  verdict: Verdict;
}

const CATEGORY_JA: Record<string, string> = {
  trend: "トレンド",
  momentum: "勢い",
  support_resistance: "支持・抵抗",
  volatility: "変動の大きさ",
  price_action: "値動きの形",
  correlation: "相関",
  news: "ニュース・指標",
  session: "時間帯",
  spread_risk: "スプレッド",
};

const CATEGORY_MAX: Record<string, number> = {
  trend: 20,
  momentum: 15,
  support_resistance: 15,
  volatility: 10,
  price_action: 10,
  correlation: 10,
  news: 10,
  session: 5,
  spread_risk: 5,
};

function biasItem(
  label: string,
  b: { direction: string; structure: string },
  dir: string
): Item {
  const same = b.direction === dir && dir !== "NEUTRAL";
  const opposite =
    b.direction !== "NEUTRAL" && dir !== "NEUTRAL" && b.direction !== dir;
  return {
    name: label,
    reading:
      `${DIRECTION_JA[b.direction as "LONG"] ?? b.direction}向き` +
      (b.structure && b.structure !== "UNKNOWN" ? `（${b.structure}）` : ""),
    verdict: same ? "for" : opposite ? "against" : "unknown",
  };
}

export default function VerdictPanel({
  a,
  planRes,
  strategy,
  expired,
  marketOpen = true,
}: {
  a: AnalysisResult;
  planRes: PlanResponse | null;
  strategy: StrategyInfo | null;
  expired: boolean;
  marketOpen?: boolean;
}) {
  const plan = planRes?.plan ?? null;
  const dir = a.direction;

  // --- 材料をひとつずつ ---
  const items: Item[] = [];

  // **強制の見送りが先。** 点数の前に置く。
  //
  // ただし invalidation_reasons には「まだ様子見の水準です」のような、
  // 強制ではない説明も入る。**それを「停止」として出さない。**
  // 止まっているのかどうかを読み違えるため、hard_blocked で分ける。
  if (a.hard_blocked) {
    for (const r of a.invalidation_reasons.slice(0, 4)) {
      items.push({ name: "強制条件", reading: r, verdict: "block" });
    }
  } else {
    items.push({
      name: "強制条件",
      reading: "止める条件には当たっていません",
      verdict: "for",
    });
    for (const r of a.invalidation_reasons.slice(0, 3)) {
      items.push({ name: "足りないもの", reading: r, verdict: "against" });
    }
  }

  items.push({
    name: "相場つきと戦略",
    reading: strategy
      ? `${REGIME_JA[a.regime] ?? a.regime} → ${strategy.label}（${strategy.why}）`
      : `${REGIME_JA[a.regime] ?? a.regime} → 対応する戦略を持っていません`,
    verdict: strategy ? "for" : "block",
  });

  items.push(biasItem("H4 の向き", a.h4_bias, dir));
  items.push(biasItem("H1 の向き", a.h1_bias, dir));
  items.push(biasItem("M15 の場面", a.m15_setup, dir));
  items.push(biasItem("M5 の様子", a.m5_context, dir));

  const st = a.market_structure;
  items.push({
    name: "M15 の構造",
    reading:
      `${st.structure}${st.pattern.length ? `（${st.pattern.join("/")}）` : ""}` +
      `${st.bos ? " ／ 直近の転換点を終値で抜けた" : ""}`,
    verdict:
      st.structure === "BULLISH"
        ? dir === "LONG"
          ? "for"
          : "against"
        : st.structure === "BEARISH"
          ? dir === "SHORT"
            ? "for"
            : "against"
          : "unknown",
  });

  // 点数の内訳。**0 が「中立」なのか「測れていない」のかを分ける。**
  const unmeasured = new Set<string>();
  if (a.correlation && a.correlation.state === "UNAVAILABLE") {
    unmeasured.add("correlation");
  }
  if (!a.news || !a.news.available) unmeasured.add("news");

  for (const [k, max] of Object.entries(CATEGORY_MAX)) {
    const v = a.score_breakdown[k] ?? 0;
    const ratio = max > 0 ? v / max : 0;
    items.push({
      name: CATEGORY_JA[k] ?? k,
      reading: unmeasured.has(k)
        ? `測れていません（満点からも外しています）`
        : `${v.toFixed(1)} / ${max} 点`,
      verdict: unmeasured.has(k)
        ? "unknown"
        : ratio >= 0.7
          ? "for"
          : ratio <= 0.3
            ? "against"
            : "unknown",
    });
  }

  items.push({
    name: "データの状態",
    reading: expired
      ? marketOpen
        ? "有効期限が切れています。作り直しが必要です"
        : "市場が閉まっています。開くまで新しい判断は作れません"
      : `${a.data_quality}${a.warnings.length ? `（注意 ${a.warnings.length} 件）` : ""}`,
    verdict: expired ? "block" : a.data_quality === "OK" ? "for" : "against",
  });

  const counts = {
    for: items.filter((i) => i.verdict === "for").length,
    against: items.filter((i) => i.verdict === "against").length,
    unknown: items.filter((i) => i.verdict === "unknown").length,
    block: items.filter((i) => i.verdict === "block").length,
  };

  // --- 「いまどうするか」を1行で ---
  const actionable = (a.signal === "BUY" || a.signal === "SELL") && !expired;
  let headline: string;
  if (expired && !marketOpen) {
    // **閉まっているときに「作り直せ」と言わない。** 作り直しても
    // 新しい気配値は出てこない。出せない理由が違う。
    headline =
      "市場が閉まっています。新しい判断は、開いてからでないと作れません。";
  } else if (expired) {
    headline = "この判断は期限切れです。作り直してから見てください。";
  } else if (a.signal === "NO_TRADE") {
    headline = a.hard_blocked
      ? "いまは建てません。下の「停止」の行が理由です。"
      : "いまは建てません。点数が取引の水準に届いていません。";
  } else if (a.signal === "WAIT") {
    headline =
      dir === "NEUTRAL"
        ? "様子見。向きが定まっていません。"
        : `様子見。向きは${DIRECTION_JA[dir]}ですが、場面がまだ整っていません。`;
  } else {
    headline = `${
      a.signal === "BUY" ? "買い" : "売り"
    }の条件を満たしています。下の値で建てる想定です。`;
  }

  return (
    <div className="verdict">
      <div className="head">
        <span className={expired ? "sig none" : SIGNAL_CLASS[a.signal]}>
          <span className="big">
            {expired ? "× 期限切れ" : SIGNAL_MARK[a.signal]}
          </span>
        </span>
        <span className="muted">
          向き {DIRECTION_JA[dir]} ／ 点数 {a.score.toFixed(1)} /{" "}
          {MAX_ATTAINABLE_SCORE}（勝率ではありません）
        </span>
      </div>

      <p>{headline}</p>

      {actionable && plan && plan.ok && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>入る値</th>
                <th>損切り</th>
                <th>利確</th>
                <th className="num">損益比</th>
                <th className="num">数量</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  {plan.entry_low !== null && plan.entry_high !== null
                    ? `${plan.entry_low} 〜 ${plan.entry_high}`
                    : "—"}
                </td>
                <td>
                  {plan.stop ?? "—"}
                  {plan.stop_basis ? (
                    <span className="muted"> {plan.stop_basis}</span>
                  ) : null}
                </td>
                <td>
                  {plan.targets.length
                    ? plan.targets
                        .map((t) => `${t.label} ${t.price}（${t.r_multiple}R）`)
                        .join(" ／ ")
                    : "—"}
                </td>
                <td className="num">
                  {plan.risk_reward !== null ? plan.risk_reward.toFixed(2) : "—"}
                </td>
                <td className="num">{plan.sizing?.qty ?? "—"}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
      {actionable && plan && !plan.ok && (
        <p className="muted">
          建玉の計画は出せていません（
          {plan.blocked_reasons[0] ?? "理由は下の計画欄にあります"}）。
        </p>
      )}

      <p className="muted">
        支持 {counts.for} ／ 反対 {counts.against} ／ 不明 {counts.unknown}
        {counts.block > 0 ? ` ／ 停止 ${counts.block}` : ""}
        　—　<strong>数の多さで決めてはいません。</strong>
        停止が1つでもあれば建てません。
      </p>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>材料</th>
              <th>読み取り</th>
              <th>判定</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i, idx) => (
              <tr key={`${i.name}-${idx}`}>
                <td>{i.name}</td>
                <td className="muted">{i.reading}</td>
                <td>
                  <span className={`tag ${i.verdict}`}>{TAG[i.verdict]}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="muted">
        「不明」は中立ではなく、<strong>測れていない</strong>という意味です。
        　実データでの検証では、<strong>この判断がコイン投げに勝つ材料は
        出ていません</strong>（docs/backtesting.md）。サインが出ていることと、
        勝てることは別です。
      </p>
    </div>
  );
}
