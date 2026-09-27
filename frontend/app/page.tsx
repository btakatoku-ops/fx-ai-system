import Link from "next/link";

import AutoRefresh from "./AutoRefresh";
import Boards from "./Board";
import {
  DIRECTION_JA,
  MAX_ATTAINABLE_SCORE,
  REGIME_JA,
  SIGNAL_CLASS,
  SIGNAL_MARK,
  TIER_JA,
  DATA_STATE_JA,
  fetchBoard,
  fetchDataStatus,
  fetchFocus,
  fetchJournal,
  fetchRanking,
  fetchStrategies,
  isExpired,
  strategyLabels,
  type BoardResponse,
  type DataStatus,
  type FocusResponse,
  type JournalResponse,
  type RankingEntry,
  type StrategiesResponse,
} from "@/lib/api";

export const dynamic = "force-dynamic";

function Row({ e, labels }: { e: RankingEntry; labels: Record<string, string> }) {
  // 期限は表示する瞬間にもう一度確かめる。
  // API が返した時点で有効でも、画面に出るまでに切れていることがある。
  const expired = isExpired(e.valid_until);
  return (
    <tr>
      <td className="num">{e.rank}</td>
      <td>
        <Link href={`/pair/${e.pair}`}>{e.pair}</Link>
      </td>
      <td>{DIRECTION_JA[e.direction]}</td>
      <td>
        {expired ? (
          <span className="sig none">× 期限切れ</span>
        ) : (
          <span className={SIGNAL_CLASS[e.signal]}>{SIGNAL_MARK[e.signal]}</span>
        )}
      </td>
      <td className="num">{e.score.toFixed(1)}</td>
      <td>{REGIME_JA[e.regime] ?? e.regime}</td>
      <td>{e.strategy ? (labels[e.strategy] ?? e.strategy) : "—"}</td>
      <td className="num">
        {e.spread_pips === null ? "—" : e.spread_pips.toFixed(1)}
      </td>
      <td>
        {e.data_quality === "OK" ? "正常" : e.data_quality}
        {e.warning_count > 0 ? `（注意 ${e.warning_count}）` : ""}
      </td>
      <td className="muted">{expired ? "有効期限が切れています" : e.reason ?? "—"}</td>
    </tr>
  );
}

function PairCard({
  e,
  labels,
}: {
  e: RankingEntry;
  labels: Record<string, string>;
}) {
  const expired = isExpired(e.valid_until);
  return (
    <Link className="paircard" href={`/pair/${e.pair}`}>
      <span className="top">
        <span className="sym">{e.pair}</span>
        <span className={expired ? "sig none" : SIGNAL_CLASS[e.signal]}>
          {expired ? "× 期限切れ" : SIGNAL_MARK[e.signal]}
        </span>
      </span>
      <span className="meta">
        点数 {e.score.toFixed(1)} ／ 向き {DIRECTION_JA[e.direction]} ／{" "}
        {REGIME_JA[e.regime] ?? e.regime}
        {e.strategy ? ` ／ ${labels[e.strategy] ?? e.strategy}` : ""}
        {e.spread_pips !== null ? ` ／ ${e.spread_pips.toFixed(1)} pips` : ""}
      </span>
      <span className="why">
        {expired ? "有効期限が切れています" : e.reason ?? "—"}
      </span>
    </Link>
  );
}

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ all?: string }>;
}) {
  // 既定は支援する銘柄だけ。**26銘柄ぶん計算しない。**
  // 実勢データでは読み込みが重く、全銘柄だと表示に数秒かかる。
  const showAll = (await searchParams)?.all === "1";
  let data;
  let strategies: StrategiesResponse | null = null;
  let status: DataStatus | null = null;
  let journal: JournalResponse | null = null;
  let focus: FocusResponse | null = null;
  let board: BoardResponse | null = null;
  try {
    // 戦略の一覧や鮮度、記録が取れなくても並びは出す。
    const [ranking, strat, st, jr, fc, bd] = await Promise.all([
      fetchRanking(showAll ? undefined : "focus"),
      fetchStrategies().catch(() => null),
      fetchDataStatus().catch(() => null),
      fetchJournal(20).catch(() => null),
      fetchFocus().catch(() => null),
      fetchBoard().catch(() => null),
    ]);
    data = ranking;
    strategies = strat;
    status = st;
    journal = jr;
    focus = fc;
    board = bd;
  } catch (err) {
    return (
      <>
        <h1>FX 判断支援エンジン</h1>
        <div className="notice">
          API に繋がりません。バックエンドを起動してください。
          <br />
          <code>uvicorn app.main:app --reload</code>（backend ディレクトリで実行）
          <br />
          <span className="muted">{String(err)}</span>
        </div>
      </>
    );
  }

  // **支援する銘柄を前に出す。** 実測では、向きが出た場面の 77〜100% が
  // スプレッドの強制条件で消えていた。費用が見合わない銘柄を同じ顔で
  // 並べると、画面は賑やかだが出るのは見送りばかりになる。
  const supported = data.entries.filter((e) => e.tier === "focus");
  const rest = data.entries.filter((e) => e.tier !== "focus");
  const actionable = supported.filter(
    (e) => e.signal === "BUY" || e.signal === "SELL"
  );
  const watching = supported.filter((e) => e.signal === "WAIT");
  const blocked = supported.filter((e) => e.signal === "NO_TRADE");
  const labels = strategyLabels(strategies);
  // いま実際に使われている戦略の内訳。**どれが一度も出ていないかが
  // 見えるようにする。** 一覧に載っていても、出ていなければ機能して
  // いないのと同じ。
  const usage = new Map<string, number>();
  for (const e of data.entries) {
    if (e.strategy) usage.set(e.strategy, (usage.get(e.strategy) ?? 0) + 1);
  }

  return (
    <>
      <h1>FX 判断支援エンジン</h1>
      <p className="muted">
        {new Date(data.generated_at).toLocaleString("ja-JP")} 時点 ／ 供給元{" "}
        {data.provider.name}（{data.provider.state}）
      </p>
      <AutoRefresh />

      {/* **いちばん上は朝のボード。** 予測ではなく、材料の向きと、
          機械が止める理由を並べる。決めるのは使う人。 */}
      <h2>朝のボード</h2>
      {board ? (
        <Boards boards={board.boards} />
      ) : (
        <p className="muted">ボードを取得できませんでした。</p>
      )}

      <h2>点数の仕組み（参考）</h2>
      {/* **いちばん上に答えを置く。** 携帯では、下までたどり着く前に
          知りたいのは「いま入る場面があるか」だけ。 */}
      <div className="today">
        <span className="big">
          {actionable.length > 0 ? (
            <span className="sig buy">
              売買の候補 {actionable.length} 件
            </span>
          ) : (
            <span className="sig none">いま入る場面はありません</span>
          )}
        </span>
        <span className="sub">
          {supported.length > 0
            ? `支援する ${supported.length} 銘柄を見ています（${supported
                .map((e) => e.pair)
                .join("・")}）`
            : "支援する銘柄がまだ決まっていません"}
        </span>
        {actionable.length > 0 && (
          <ul className="plain">
            {actionable.map((e) => (
              <li key={e.pair}>
                <Link href={`/pair/${e.pair}`}>
                  <strong>{e.pair}</strong>
                </Link>{" "}
                <span className={SIGNAL_CLASS[e.signal]}>
                  {SIGNAL_MARK[e.signal]}
                </span>{" "}
                <span className="muted">点数 {e.score.toFixed(1)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>


      <div className="notice">
        点数は<strong>場面の整い方</strong>を表したもので、
        <strong>勝率ではありません</strong>。配点は{" "}
        <strong>{MAX_ATTAINABLE_SCORE} 点</strong>満点ですが、
        銘柄によっては相関や指標を測れず、その分だけ満点が下がります。
        このアプリは発注を行いません。売買の判断と結果はご自身の責任になります。
        {data.provider.name === "mock" && (
          <>
            <br />
            いま表示しているのは<strong>合成データ</strong>で、実勢の値ではありません。
          </>
        )}
      </div>

      {status && status.state !== "OK" && (
        <div className="notice warn">
          <strong>取り込んだ材料が古くなっています。</strong>
          <ul className="plain">
            {status.sources
              .filter((s) => s.state !== "OK")
              .map((s) => (
                <li key={s.key}>
                  {s.label}：{DATA_STATE_JA[s.state]}
                  {s.age_hours !== null && s.limit_hours !== null
                    ? `（${s.age_hours.toFixed(0)} 時間経過 / 上限 ${s.limit_hours.toFixed(0)} 時間）`
                    : ""}
                  {s.blocks_trading && s.state === "STALE" ? (
                    <strong>　全銘柄が見送りになります。</strong>
                  ) : null}
                  <br />
                  <span className="muted">
                    取り込み直す: <code>{s.refresh}</code>
                  </span>
                  {s.notes.map((n, i) => (
                    <span className="muted" key={i}>
                      <br />
                      {n}
                    </span>
                  ))}
                </li>
              ))}
          </ul>
        </div>
      )}

      <h2>相場の概況</h2>
      <div className="grid">
        <div className="card">
          <span className="k">支援する銘柄</span>
          <span className="v">{supported.length}</span>
          <span className="k">分析は {data.analyzed} 銘柄（比較のため）</span>
        </div>
        <div className="card">
          <span className="k">売買の候補</span>
          <span className="v">{actionable.length}</span>
        </div>
        <div className="card">
          <span className="k">様子見</span>
          <span className="v">{watching.length}</span>
        </div>
        <div className="card">
          <span className="k">見送り</span>
          <span className="v">{blocked.length}</span>
        </div>
      </div>

      {focus && (
        <div className="notice">
          {focus.measured ? (
            <>
              <strong>
                支援するのは {focus.counts.focus ?? 0} 銘柄だけです。
              </strong>
              　費用（スプレッド ÷ M15 の ATR）で分けています。上限{" "}
              {focus.spread_limit_ratio} を普段から超えている銘柄は、
              そもそも出番が来ません。
              <strong>勝てるかどうかでは分けていません。</strong>
              それは測れていません。
              <br />
              <span className="muted">
                {focus.measured_at?.slice(0, 10)} 測定 ／ 内訳:{" "}
                {Object.entries(focus.counts)
                  .map(([k, v]) => `${TIER_JA[k] ?? k} ${v}`)
                  .join(" ／ ")}
              </span>
            </>
          ) : (
            <>
              <strong>まだ銘柄を測っていません。</strong>
              　<code>python scripts/measure_pair_value.py</code> を走らせると、
              費用の面で支援する値打ちのある銘柄だけを前に出します。
            </>
          )}
        </div>
      )}

      <h2>上位の機会</h2>
      {actionable.length === 0 ? (
        <p className="muted">
          いま売買の条件を満たしている銘柄はありません。
          条件が整わないときに動かないのは、この仕組みの想定どおりの動きです。
        </p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="num">順位</th>
                <th>ペア</th>
                <th>向き</th>
                <th>シグナル</th>
                <th className="num">点数</th>
                <th>相場つき</th>
                <th>戦略</th>
                <th className="num">スプレッド</th>
                <th>状態</th>
                <th>理由</th>
              </tr>
            </thead>
            <tbody>
              {actionable.map((e) => (
                <Row key={e.pair} e={e} labels={labels} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {strategies && (
        <>
          <h2>使っている戦略</h2>
          <div className="notice">{strategies.measured}</div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>戦略</th>
                  <th>どういう場面で入るか</th>
                  <th>使う相場つき（数字は試す順）</th>
                  <th className="num">いま出ている銘柄</th>
                </tr>
              </thead>
              <tbody>
                {strategies.strategies.map((s) => (
                  <tr key={s.name}>
                    <td>
                      {s.label}
                      <span className="muted"> {s.name}</span>
                    </td>
                    <td className="muted">{s.why}</td>
                    <td className="muted">
                      {s.regimes.length === 0
                        ? "割り当てなし"
                        : s.regimes
                            .map((r) => {
                              const [name, order] = r.split("(");
                              return `${REGIME_JA[name] ?? name}(${order}`;
                            })
                            .join(" ／ ")}
                    </td>
                    <td className="num">{usage.get(s.name) ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted">
            相場つきごとに、試す順番が決まっています。
            <strong>順に試して、最初に向きが出たものを採ります。</strong>
            戦略を増やすほど「どれかが偶然当てはまる」回数が増えるので、
            <strong>増やせば強くなるわけではありません。</strong>
          </p>
        </>
      )}

      {status && (
        <>
          <h2>取り込んだ材料</h2>
          <p className="muted">
            取り込みは<strong>手動</strong>です。古くなると点数に入らなく
            なり、経済指標の予定表は<strong>古くなった時点で全銘柄が
            見送り</strong>になります（古い予定表で「指標なし」と判断する
            のがいちばん危ないためです）。
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>材料</th>
                  <th>状態</th>
                  <th className="num">経過 / 上限</th>
                  <th>中身</th>
                  <th>取り込み直す</th>
                </tr>
              </thead>
              <tbody>
                {status.sources.map((s) => (
                  <tr key={s.key}>
                    <td>{s.label}</td>
                    <td>{DATA_STATE_JA[s.state]}</td>
                    <td className="num">
                      {s.age_hours === null
                        ? "—"
                        : `${s.age_hours.toFixed(0)}h / ${
                            s.limit_hours === null
                              ? "—"
                              : `${s.limit_hours.toFixed(0)}h`
                          }`}
                    </td>
                    <td className="muted">{s.detail}</td>
                    <td className="muted">
                      <code>{s.refresh}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {journal && (
        <>
          <h2>判断の記録</h2>
          <p className="muted">
            画面に出した判断をそのまま残しています。
            <strong>「こう判断した」という記録だけで、実際に建てたかどうかは
            含みません。</strong>
            同じ判断が続いているあいだは1行のままで、見た回数だけ増えます。
            結果の欄は <code>scripts/score_journal.py</code> が後から埋めます
            （<strong>判断した時刻より後の足だけ</strong>を使います）。
          </p>
          <div className="grid">
            <div className="card">
              <span className="k">残っている判断</span>
              <span className="v">{journal.summary.total}</span>
            </div>
            <div className="card">
              <span className="k">うち売買の判断</span>
              <span className="v">{journal.summary.actionable}</span>
            </div>
            <div className="card">
              <span className="k">結果を突き合わせた件数</span>
              <span className="v">{journal.summary.scored}</span>
            </div>
            <div className="card">
              <span className="k">記録の開始</span>
              <span className="v" style={{ fontSize: 14 }}>
                {journal.summary.first_at
                  ? new Date(journal.summary.first_at).toLocaleString("ja-JP")
                  : "—"}
              </span>
            </div>
          </div>
          {journal.entries.length === 0 ? (
            <p className="muted">まだ記録がありません。</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>判断した時刻</th>
                    <th>ペア</th>
                    <th>シグナル</th>
                    <th>向き</th>
                    <th className="num">点数</th>
                    <th>戦略</th>
                    <th className="num">続いた回数</th>
                    <th>結果</th>
                    <th>理由</th>
                  </tr>
                </thead>
                <tbody>
                  {journal.entries.map((e) => (
                    <tr key={e.id}>
                      <td>
                        {new Date(e.decided_at).toLocaleString("ja-JP")}
                        {e.provider === "mock" ? (
                          <span className="muted"> 合成</span>
                        ) : null}
                      </td>
                      <td>
                        <Link href={`/pair/${e.pair}`}>{e.pair}</Link>
                      </td>
                      <td>
                        <span className={SIGNAL_CLASS[e.signal]}>
                          {SIGNAL_MARK[e.signal]}
                        </span>
                      </td>
                      <td>{DIRECTION_JA[e.direction]}</td>
                      <td className="num">{e.score.toFixed(1)}</td>
                      <td>
                        {e.strategy
                          ? (labels[e.strategy] ?? e.strategy)
                          : "—"}
                      </td>
                      <td className="num">{e.seen_count}</td>
                      <td>
                        {e.outcome
                          ? `${e.outcome}${
                              e.r_multiple !== null
                                ? `（${e.r_multiple.toFixed(2)}R）`
                                : ""
                            }`
                          : "—"}
                      </td>
                      <td className="muted">{e.reason ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      <h2>支援する銘柄</h2>
      <p className="muted">
        費用の面で土俵に乗る銘柄です。見送りでも隠さず並べます。
        なぜ弾かれたかは、不具合を追うときに要る情報だからです。
      </p>
      <div className="pairlist only-narrow">
        {(supported.length ? supported : data.entries).map((e) => (
          <PairCard key={e.pair} e={e} labels={labels} />
        ))}
      </div>
      <div className="table-wrap only-wide">
        <table>
          <thead>
            <tr>
              <th className="num">順位</th>
              <th>ペア</th>
              <th>向き</th>
              <th>シグナル</th>
              <th className="num">点数</th>
              <th>相場つき</th>
              <th>戦略</th>
              <th className="num">スプレッド</th>
              <th>状態</th>
              <th>理由</th>
            </tr>
          </thead>
          <tbody>
            {(supported.length ? supported : data.entries).map((e) => (
              <Row key={e.pair} e={e} labels={labels} />
            ))}
          </tbody>
        </table>
      </div>

      {!showAll && (
        <p className="muted">
          <Link className="tf" href="/?all=1">
            対象外の銘柄もまとめて見る
          </Link>
          　支援する銘柄だけを計算しています（そのぶん速く出ます）。
        </p>
      )}
      {showAll && (
        <p className="muted">
          <Link className="tf" href="/">
            支援する銘柄だけに戻す
          </Link>
        </p>
      )}

      {supported.length > 0 && rest.length > 0 && (
        <>
          <h2>それ以外の銘柄</h2>
          <p className="muted">
            費用が重く、出番が来にくい銘柄です。
            <strong>消さずに残しています。</strong>
            比較の対象として要るのと、業者や時間帯が変われば条件も
            変わるからです。
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th className="num">順位</th>
                  <th>ペア</th>
                  <th>区分</th>
                  <th>向き</th>
                  <th>シグナル</th>
                  <th className="num">点数</th>
                  <th>相場つき</th>
                  <th className="num">スプレッド</th>
                  <th>理由</th>
                </tr>
              </thead>
              <tbody>
                {rest.map((e) => (
                  <tr key={e.pair}>
                    <td className="num">{e.rank}</td>
                    <td>
                      <Link href={`/pair/${e.pair}`}>{e.pair}</Link>
                    </td>
                    <td className="muted">{TIER_JA[e.tier] ?? e.tier}</td>
                    <td>{DIRECTION_JA[e.direction]}</td>
                    <td>
                      <span className={SIGNAL_CLASS[e.signal]}>
                        {SIGNAL_MARK[e.signal]}
                      </span>
                    </td>
                    <td className="num">{e.score.toFixed(1)}</td>
                    <td>{REGIME_JA[e.regime] ?? e.regime}</td>
                    <td className="num">
                      {e.spread_pips === null ? "—" : e.spread_pips.toFixed(1)}
                    </td>
                    <td className="muted">{e.reason ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
