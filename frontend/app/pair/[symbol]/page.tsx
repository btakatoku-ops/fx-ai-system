import Link from "next/link";

import AutoRefresh from "../../AutoRefresh";
import Chart from "../../Chart";
import VerdictPanel from "../../Verdict";
import {
  DIRECTION_JA,
  MAX_ATTAINABLE_SCORE,
  REGIME_JA,
  SIGNAL_CLASS,
  SIGNAL_MARK,
  CHART_TIMEFRAMES,
  fetchAnalysis,
  fetchCandles,
  fetchDataStatus,
  fetchPlan,
  fetchStrategies,
  isExpired,
  secondsRemaining,
  yen,
  type ChartResponse,
  type DataStatus,
  type PlanResponse,
  type StrategiesResponse,
  type TimeframeBias,
} from "@/lib/api";

export const dynamic = "force-dynamic";

const BREAKDOWN_JA: Record<string, string> = {
  trend: "トレンド",
  momentum: "勢い",
  support_resistance: "支持・抵抗",
  volatility: "変動の大きさ",
  price_action: "値動きの形（構造を含む）",
  correlation: "相関",
  news: "ニュース・指標",
  session: "時間帯",
  spread_risk: "スプレッド",
};

// 材料を持っていない項目。配点はあるが常に0点になる。
// 材料の有無は分析結果から見る（設定が変われば表示も変わる）
const NO_DATA = new Set<string>();

const MAX: Record<string, number> = {
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

function BiasRow({ b }: { b: TimeframeBias }) {
  return (
    <tr>
      <td>{b.timeframe}</td>
      <td>{DIRECTION_JA[b.direction]}</td>
      <td>{b.structure}</td>
      <td className="muted">{b.note}</td>
    </tr>
  );
}

export default async function PairDetail({
  params,
  searchParams,
}: {
  params: Promise<{ symbol: string }>;
  searchParams: Promise<{ tf?: string }>;
}) {
  const { symbol } = await params;
  const { tf } = await searchParams;
  const chartTf = CHART_TIMEFRAMES.includes(
    (tf ?? "") as (typeof CHART_TIMEFRAMES)[number]
  )
    ? (tf as string)
    : "M15";
  let a;
  let planRes: PlanResponse | null = null;
  let chartRes: ChartResponse | null = null;
  let stratRes: StrategiesResponse | null = null;
  let statusRes: DataStatus | null = null;
  try {
    // **2つを直列に待たない。** 分析と計画は互いに依存しないので、
    // 直列にすると待ち時間がそのまま2倍になる。サーバー側では同じ
    // 分析を使い回すので、並べて投げても計算は1回で済む。
    const [analysis, plan, chart, strat, status] = await Promise.all([
      fetchAnalysis(symbol),
      // 計画やチャート、戦略の説明が取れなくても分析は見せる
      fetchPlan(symbol).catch(() => null),
      // **EMA200 を出すには200本以上いる。** 150本だと凡例に項目だけ
      // 出て値が空になり、指標が壊れているように見える。
      fetchCandles(symbol, chartTf, 400).catch(() => null),
      fetchStrategies().catch(() => null),
      fetchDataStatus().catch(() => null),
    ]);
    a = analysis;
    planRes = plan;
    chartRes = chart;
    stratRes = strat;
    statusRes = status;
  } catch (err) {
    return (
      <>
        <Link className="back" href="/">
          ← 一覧へ
        </Link>
        <div className="notice">
          分析を取得できません。<span className="muted">{String(err)}</span>
        </div>
      </>
    );
  }

  // 画面に出す瞬間にもう一度期限を確かめる。
  const expired = isExpired(a.valid_until);
  const remain = secondsRemaining(a.valid_until);

  const m15 = a.indicators?.M15 ?? {};
  const h1 = a.indicators?.H1 ?? {};
  const fmt = (v: number | null | undefined, d = 4) =>
    v === null || v === undefined ? "—" : v.toFixed(d);
  // 円ペアは3桁、それ以外は5桁で見せる
  const fmtDigits = a.pair.endsWith("JPY") ? 3 : 5;
  // どの戦略で向きを決めたか。**説明も engine から引く。** 画面に書くと
  // engine 側と食い違ったときに気づけない。
  const used = stratRes?.strategies.find((s) => s.name === a.strategy) ?? null;

  // チャートに引く建玉の線。**チャート側で作らない。** 計画が持っている
  // 値をそのまま渡す。期限切れのときは線を出さない（根拠が失われている）。
  const p = planRes?.plan ?? null;
  const marks =
    p === null
      ? null
      : {
          direction: a.direction,
          signalLabel: SIGNAL_MARK[a.signal],
          entryLow: p.entry_low,
          entryHigh: p.entry_high,
          stop: p.stop,
          targets: p.targets.map((t) => ({
            label: t.label,
            price: t.price,
            r: t.r_multiple,
          })),
          ok: p.ok && !expired,
          note: expired
            ? "この判断は期限切れです"
            : (p.blocked_reasons[0] ?? null),
        };

  return (
    <>
      <Link className="back" href="/">
        ← 一覧へ
      </Link>
      <h1>{a.pair}</h1>
      <p className="muted">
        {new Date(a.timestamp).toLocaleString("ja-JP")} 時点
      </p>
      <AutoRefresh />

      <div className="grid">
        <div className="card">
          <span className="k">シグナル</span>
          <span
            className={`v ${expired ? "sig none" : SIGNAL_CLASS[a.signal]}`}
          >
            {expired ? "× 期限切れ" : SIGNAL_MARK[a.signal]}
          </span>
          <span className="k">
            {remain === null
              ? "有効期限なし"
              : expired
                ? "作り直しが必要です"
                : `あと ${remain} 秒 有効`}
          </span>
        </div>
        <div className="card">
          <span className="k">向き</span>
          <span className="v">{DIRECTION_JA[a.direction]}</span>
        </div>
        <div className="card">
          <span className="k">点数（勝率ではない）</span>
          <span className="v">
            {a.score.toFixed(1)}
            <span style={{ fontSize: 13, color: "var(--tx2)" }}>
              {" "}
              / {MAX_ATTAINABLE_SCORE}
            </span>
          </span>
        </div>
        <div className="card">
          <span className="k">相場つき</span>
          <span className="v" style={{ fontSize: 16 }}>
            {REGIME_JA[a.regime] ?? a.regime}
          </span>
          <span className="k">確からしさ {a.regime_score.toFixed(0)} / 100</span>
        </div>
        <div className="card">
          <span className="k">戦略</span>
          <span className="v" style={{ fontSize: 16 }}>
            {used ? used.label : a.strategy ?? "—"}
          </span>
          <span className="k">
            {used
              ? used.why
              : a.strategy
                ? ""
                : "この相場つきに対応する戦略を持っていません"}
          </span>
        </div>
        <div className="card">
          <span className="k">現在値（M15 終値）</span>
          <span className="v">{fmt(m15.close as number)}</span>
        </div>
        <div className="card">
          <span className="k">スプレッド</span>
          <span className="v">
            {a.spread ? `${a.spread.spread_pips.toFixed(1)} pips` : "—"}
          </span>
        </div>
      </div>

      {expired && (
        <div className="notice">
          <strong>この判断は有効期限が切れています。</strong>
          依存している気配値や足が古くなったため、売買の判断には使えません。
          画面を読み込み直してください。
        </div>
      )}

      <div className="notice">
        点数は場面の整い方であって、勝率でも上昇確率でもありません。
        到達しうる最大は {MAX_ATTAINABLE_SCORE} 点ですが、測れない材料が
        あるとその回だけ満点から外れます。
        指標の状態は <strong>{a.news_state}</strong> です。
        {a.news?.available
          ? "発表前後の停止は点数ではなく強制条件で判定しています。"
          : "予定表を取り込んでいないため不明のままです。推測では埋めません。"}
      </div>

      <h2>総合判断</h2>
      <VerdictPanel
        a={a}
        planRes={planRes}
        strategy={used}
        expired={expired}
        marketOpen={statusRes?.market_open ?? true}
      />

      <h2>チャート</h2>
      <p className="muted">
        {CHART_TIMEFRAMES.map((t) => (
          <Link
            key={t}
            href={`/pair/${a.pair}?tf=${t}`}
            className={t === chartTf ? "tf on" : "tf"}
          >
            {t}
          </Link>
        ))}
      </p>
      {chartRes ? (
        <Chart data={chartRes} plan={marks} />
      ) : (
        <p className="muted">チャートを取得できませんでした。</p>
      )}

      <h2>点数の内訳</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>項目</th>
              <th className="num">得点</th>
              <th className="num">配点</th>
              <th style={{ width: "30%" }}>割合</th>
              <th>材料</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(a.score_breakdown).map(([k, v]) => (
              <tr key={k}>
                <td>{BREAKDOWN_JA[k] ?? k}</td>
                <td className="num">{Number(v).toFixed(2)}</td>
                <td className="num">{MAX[k] ?? "—"}</td>
                <td>
                  <span className="bar">
                    <span
                      style={{
                        width: `${Math.min(100, (Number(v) / (MAX[k] || 1)) * 100)}%`,
                      }}
                    />
                  </span>
                </td>
                <td className="muted">
                  {k === "news" && a.news
                    ? a.news.available
                      ? a.news.state === "ACTIVE"
                        ? "指標の前後（建てません）"
                        : a.news.next_event ?? "予定表あり"
                      : "予定表を取り込んでいません"
                    : k === "correlation" && a.correlation
                      ? a.correlation.state === "KNOWN"
                        ? `${a.correlation.drivers.length} 件で測定`
                        : a.correlation.state === "WEAK"
                          ? "測ったが相関が弱い"
                          : "この銘柄では測れません"
                      : "あり"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>時間足ごとの見方</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>時間足</th>
              <th>向き</th>
              <th>構造</th>
              <th>根拠</th>
            </tr>
          </thead>
          <tbody>
            <BiasRow b={a.h4_bias} />
            <BiasRow b={a.h1_bias} />
            <BiasRow b={a.m15_setup} />
            <BiasRow b={a.m5_context} />
          </tbody>
        </table>
      </div>

      <h2>相場の構造（M15）</h2>
      <p className="muted">
        並び {a.market_structure.pattern.join(" → ") || "—"} ／ 判定{" "}
        {a.market_structure.structure} ／ BOS{" "}
        {a.market_structure.bos ? "あり" : "なし"} ／ CHOCH{" "}
        {a.market_structure.choch ? "あり" : "なし"}
      </p>

      <h2>指標の要約</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>指標</th>
              <th className="num">H1</th>
              <th className="num">M15</th>
            </tr>
          </thead>
          <tbody>
            {[
              ["EMA20", "ema20"],
              ["EMA50", "ema50"],
              ["EMA200", "ema200"],
              ["RSI14", "rsi14"],
              ["MACD ヒスト", "macd_hist"],
              ["ATR14", "atr14"],
              ["ADX14", "adx14"],
              ["BB 上限", "bb_upper"],
              ["BB 下限", "bb_lower"],
              ["支持", "support"],
              ["抵抗", "resistance"],
              ["ピボット", "pivot"],
            ].map(([label, key]) => (
              <tr key={key}>
                <td>{label}</td>
                <td className="num">{fmt(h1[key] as number)}</td>
                <td className="num">{fmt(m15[key] as number)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {a.correlation && a.correlation.drivers.length > 0 && (
        <>
          <h2>相関（測った結果）</h2>
          <p className="muted">
            どの材料と相関するかは決め打ちせず、期間ごとに測っています。
            相関が弱い材料は使いません。
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>材料</th>
                  <th className="num">相関</th>
                  <th className="num">直近の動き</th>
                  <th>この銘柄への示唆</th>
                  <th>判断の向きと</th>
                </tr>
              </thead>
              <tbody>
                {a.correlation.drivers.map((d) => (
                  <tr key={d.name}>
                    <td>{d.label}</td>
                    <td className="num">{d.correlation.toFixed(2)}</td>
                    <td className="num">
                      {(d.driver_move * 100).toFixed(2)}%
                    </td>
                    <td>{d.implied > 0 ? "上" : "下"}</td>
                    <td>{d.agrees ? "一致" : "逆"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h2>この判断の根拠</h2>
      {a.reasons.length ? (
        <ul className="plain">
          {a.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      ) : (
        <p className="muted">根拠として挙げられる材料がありません。</p>
      )}

      {a.invalidation_reasons.length > 0 && (
        <>
          <h2>取引しない理由</h2>
          <ul className="plain warn">
            {a.invalidation_reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </>
      )}

      <h2>建玉の計画</h2>
      {planRes === null ? (
        <p className="muted">計画を取得できませんでした。</p>
      ) : !planRes.plan.ok ? (
        <>
          <p className="muted">
            この場面では計画を作りません。
            <strong>見送りに「もし買うなら」の数字を添えると、見送りの意味が
            薄れる</strong>ためです。
          </p>
          <ul className="plain warn">
            {planRes.plan.blocked_reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </>
      ) : (
        <>
          <div className="grid">
            <div className="card">
              <span className="k">建値の帯</span>
              <span className="v" style={{ fontSize: 16 }}>
                {planRes.plan.entry_low?.toFixed(fmtDigits)} 〜{" "}
                {planRes.plan.entry_high?.toFixed(fmtDigits)}
              </span>
              <span className="k">1点では当てられないので帯で示します</span>
            </div>
            <div className="card">
              <span className="k">損切り</span>
              <span className="v" style={{ fontSize: 16 }}>
                {planRes.plan.stop?.toFixed(fmtDigits)}
              </span>
              <span className="k">根拠: {planRes.plan.stop_basis}</span>
            </div>
            <div className="card">
              <span className="k">数量</span>
              <span className="v">{planRes.plan.sizing?.qty}</span>
              <span className="k">
                {planRes.plan.sizing?.units.toLocaleString("ja-JP")} 通貨
                （数量1 ={" "}
                {planRes.plan.sizing?.contract_unit.toLocaleString("ja-JP")}）
              </span>
            </div>
            <div className="card">
              <span className="k">必要証拠金（推定）</span>
              <span className="v" style={{ fontSize: 17 }}>
                {yen(planRes.plan.sizing?.required_margin ?? null)}
              </span>
              <span className="k">
                資金の {planRes.plan.sizing?.margin_use_pct?.toFixed(1)}%
              </span>
            </div>
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>利確</th>
                  <th className="num">値段</th>
                  <th className="num">リスク比</th>
                  <th className="num">届いたときの額</th>
                  <th>備考</th>
                </tr>
              </thead>
              <tbody>
                {planRes.plan.targets.map((t) => (
                  <tr key={t.label}>
                    <td>{t.label}</td>
                    <td className="num">{t.price.toFixed(fmtDigits)}</td>
                    <td className="num">{t.r_multiple.toFixed(2)} R</td>
                    <td className="num">{yen(t.profit)}</td>
                    <td className="muted">
                      {t.capped_by ? `${t.capped_by}の手前で止めました` : "—"}
                    </td>
                  </tr>
                ))}
                <tr>
                  <td>損切り</td>
                  <td className="num">{planRes.plan.stop?.toFixed(fmtDigits)}</td>
                  <td className="num">−1.00 R</td>
                  <td className="num">
                    −
                    {Math.abs(
                      Math.round(planRes.plan.loss_if_stopped?.amount ?? 0)
                    ).toLocaleString("ja-JP")}{" "}
                    円
                  </td>
                  <td className="muted">当たったらこの額を失います</td>
                </tr>
                <tr>
                  <td>スプレッド</td>
                  <td className="num">—</td>
                  <td className="num">—</td>
                  <td className="num">{yen(planRes.plan.spread_cost)}</td>
                  <td className="muted">建てた時点で先に払う費用</td>
                </tr>
                <tr>
                  <td>
                    スワップ
                    {planRes.plan.swap_state === "STALE" && " ⚠"}
                  </td>
                  <td className="num">—</td>
                  <td className="num">—</td>
                  <td className="num">
                    {planRes.plan.swap_state === "UNKNOWN"
                      ? "不明"
                      : yen(planRes.plan.swap_per_day)}
                  </td>
                  <td className="muted">
                    {planRes.plan.swap_state === "UNKNOWN" &&
                      "表を取り込んでいません（0円ではありません）"}
                    {planRes.plan.swap_state === "KNOWN" &&
                      `1日あたり ／ ${planRes.plan.swap?.as_of ?? "日付不明"} の表`}
                    {planRes.plan.swap_state === "STALE" &&
                      `1日あたり ／ ${planRes.plan.swap?.as_of ?? "日付不明"} の表で古くなっています`}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <p className="muted">
            資金 {planRes.account.balance.toLocaleString("ja-JP")} 円 ／ 1回の
            リスク {planRes.account.risk_per_trade_pct}% ／ レバレッジ{" "}
            {planRes.account.leverage} 倍 で計算しています。
          </p>

          <ul className="plain warn">
            {planRes.plan.disclaimers.map((d, i) => (
              <li key={i}>{d.replace(/\*\*/g, "")}</li>
            ))}
            {planRes.account.unverified && (
              <li>{planRes.account.unverified}</li>
            )}
          </ul>
          <p className="muted">
            このアプリは発注しません。注文はご自身で業者の画面から行ってください。
          </p>
        </>
      )}

      {a.warnings.length > 0 && (
        <>
          <h2>注意</h2>
          <ul className="plain warn">
            {a.warnings.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
