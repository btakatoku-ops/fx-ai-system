// 朝のボード。**予測はしない。**
//
// 出すのは3つだけ。
// 1. 事実: 今日どれだけ動いたか、20日のレンジのどこにいるか、近い水準
// 2. 要因: トレンド・モメンタム・ファンダの向き（▲▼■）。そろっているか、割れているか
// 3. 除外: 機械が「今日はやめておく」と言う理由
//
// **「買い」「売り」「発注」とは書かない。** そろっていても、決めるのは使う人。
// そろった日の成績はまだ測れていない（記録して採点中）。
import Link from "next/link";

import type { Board, BoardBrief, BoardFactor } from "@/lib/api";
import { fmt, nearLevels, rangeBar, usedLevel } from "@/lib/board";

import FundamentalsForm from "./FundamentalsForm";
import PlanPanel from "./PlanPanel";

const VERDICT_CLASS: Record<string, string> = {
  excluded: "vd excluded",
  aligned_up: "vd up",
  aligned_down: "vd down",
  mixed: "vd mixed",
};

const VERDICT_MARK: Record<string, string> = {
  excluded: "✕",
  aligned_up: "▲",
  aligned_down: "▼",
  mixed: "■",
};

function FactorCell({ f }: { f: BoardFactor }) {
  return (
    <div className={`factor v-${f.view}${f.stale ? " stale" : ""}`}>
      <span className="lab">{f.label}</span>
      <span className="mk" aria-label={f.view}>
        {f.mark}
      </span>
      <span className="tx">{f.text}</span>
      {f.key === "fundamentals" && (
        <span className="src">
          {f.source ? `出典: ${f.source}` : "出典なし"}
          {f.as_of ? `（${f.as_of}）` : ""}
          {f.stale ? " ／ 古いので判定に数えていません" : ""}
        </span>
      )}
    </div>
  );
}

function RangeCell({ b }: { b: Board }) {
  const f = b.facts;
  const pos = f.position;
  return (
    <div className="factor v-fact">
      <span className="lab">レンジ位置（20日）</span>
      <span className="mk">{pos === null ? "—" : `${Math.round(pos * 100)}%`}</span>
      <span className="tx">
        {pos === null
          ? "20日の高安が取れていません"
          : pos >= 0.8
            ? "上の端に近い"
            : pos <= 0.2
              ? "下の端に近い"
              : "中ほど"}
      </span>
      <span className="src">
        上に {fmt(f.room_up_adr, 1)} 日分 ／ 下に {fmt(f.room_down_adr, 1)} 日分
      </span>
    </div>
  );
}

function RangeBarView({ b }: { b: Board }) {
  const bar = rangeBar(b.facts);
  if (!bar) return null;
  const d = b.digits;
  const today =
    bar.todayFrom !== null && bar.todayTo !== null
      ? { left: `${bar.todayFrom}%`, width: `${Math.max(0.8, bar.todayTo - bar.todayFrom)}%` }
      : null;
  return (
    <div className="rangebar" aria-label="20日のレンジの中の位置">
      <div className="track">
        {today && <span className="todayband" style={today} title={b.facts.market_open ? "今日の高安" : "直近の取引日の高安"} />}
        {bar.price !== null && (
          <span className="now" style={{ left: `${bar.price}%` }} title="いまの値" />
        )}
      </div>
      <div className="ends">
        <span>{fmt(bar.lo, d)}</span>
        <span className="muted">
          帯={b.facts.market_open ? "今日" : "直近の取引日"}の高安 ／ 縦線=いまの値
        </span>
        <span>{fmt(bar.hi, d)}</span>
      </div>
    </div>
  );
}

function Levels({ b }: { b: Board }) {
  const { above, below } = nearLevels(b.facts);
  if (above.length + below.length === 0) return null;
  const d = b.digits;
  const row = (l: (typeof above)[number]) => (
    <li key={`${l.label}-${l.price}`}>
      <span className="num">{fmt(l.price, d)}</span> {l.label}
      <span className="muted">
        {l.adr !== null ? `（${l.adr > 0 ? "+" : ""}${l.adr.toFixed(2)} 日分）` : ""}
      </span>
    </li>
  );
  return (
    <div className="levels">
      <div>
        <span className="k">上の水準</span>
        <ul className="plain">{above.length ? above.map(row) : <li className="muted">なし</li>}</ul>
      </div>
      <div>
        <span className="k">下の水準</span>
        <ul className="plain">{below.length ? below.map(row) : <li className="muted">なし</li>}</ul>
      </div>
    </div>
  );
}

function Facts({ b }: { b: Board }) {
  const f = b.facts;
  const lv = usedLevel(f.used_ratio);
  return (
    <ul className="facts">
      <li>
        {f.market_open ? "今日の値幅" : "直近の取引日の値幅"}{" "}
        <strong className={`used ${lv}`}>
          {f.used_ratio === null ? "—" : `普段の ${Math.round(f.used_ratio * 100)}%`}
        </strong>
        <span className="muted">
          （{fmt(f.today_low, b.digits)}〜{fmt(f.today_high, b.digits)} ／ 普段は{" "}
          {fmt(f.adr, b.digits)}、{f.adr_days} 日の平均）
        </span>
      </li>
      {f.market_open && f.deadline_jst && (
        <li>
          手仕舞いの刻限 <strong>{f.deadline_jst}</strong>
          {f.minutes_to_deadline !== null &&
            (f.minutes_to_deadline > 0 ? (
              <span className="muted">
                （あと {Math.floor(f.minutes_to_deadline / 60)} 時間{f.minutes_to_deadline % 60} 分）
              </span>
            ) : (
              <strong className="used exclude">
                {" "}過ぎました。日替わりまで建てません
              </strong>
            ))}
          {f.session && <span className="muted"> ／ {f.session}</span>}
        </li>
      )}
      <li>
        指標{" "}
        {f.next_event ? (
          <>
            <strong>{f.next_event}</strong>
            {f.minutes_to_event !== null && (
              <span className="muted">（あと {Math.round(f.minutes_to_event)} 分）</span>
            )}
          </>
        ) : (
          <span className="muted">{f.event_note ?? "—"}</span>
        )}
      </li>
    </ul>
  );
}

const BRIEF_LEAN: Record<string, string> = {
  up: "▲ 上方向",
  down: "▼ 下方向",
  neutral: "■ 中立",
  conflict: "■ 対立",
};

const BRIEF_KEYS = [
  ["trend", "トレンド"],
  ["momentum", "モメンタム"],
  ["fundamentals", "ファンダ"],
  ["range", "レンジ位置"],
] as const;

function BriefRow({ br, b }: { br: BoardBrief; b: Board }) {
  // アプリの向き（トレンド・モメンタム）とブリーフの向きが合っているか
  const mine = Object.fromEntries(b.factors.map((f) => [f.key, f.view]));
  const when =
    br.age_days === 0 ? "今日" : br.age_days !== null ? `${br.age_days}日前` : "日付不明";
  return (
    <div className={`brief${br.age_days === 0 ? "" : " old"}`}>
      <div className="bh">
        <span className="lab">FXモーニングブリーフ</span>
        <span className={`bl ${br.lean ?? "none"}`}>
          {BRIEF_LEAN[br.lean ?? ""] ?? "—"}
          {br.excluded ? "（除外・見送り）" : ""}
        </span>
        <span className="muted">
          {br.brief_at ? br.brief_at.slice(5, 16).replace("T", " ").replace("-", "/") : "—"}（{when}）
        </span>
        {br.url && (
          <a className="muted" href={br.url} target="_blank" rel="noreferrer">
            開く
          </a>
        )}
      </div>
      <div className="bs">
        {BRIEF_KEYS.map(([k, label]) => {
          const s = br.signals[k];
          const differs =
            s && (k === "trend" || k === "momentum") && mine[k] && mine[k] !== "none" && mine[k] !== s.view;
          return (
            <span key={k} className={`v-${s?.view ?? "none"}`} title={s?.text}>
              {label} {s?.mark ?? "—"}
              {differs ? <em>（アプリと違う）</em> : null}
            </span>
          );
        })}
      </div>
      {br.summary && <p className="bq">「{br.summary}」</p>}
      {br.age_days !== 0 && (
        <p className="muted">
          今日のブリーフではないので、ブリーフの判定は「気をつけること」に入れていません
          （ファンダの見立ては7日まで使います）。
        </p>
      )}
    </div>
  );
}

export function BoardCard({ b }: { b: Board }) {
  const v = b.verdict;
  const f = b.facts;
  const fund = b.factors.find((x) => x.key === "fundamentals");
  return (
    <section className={`board ${v.state}`}>
      <header>
        <Link href={`/pair/${b.pair}`} className="sym">
          {b.pair}
        </Link>
        <span className={VERDICT_CLASS[v.state] ?? "vd mixed"}>
          {VERDICT_MARK[v.state] ?? "■"} {v.label}
        </span>
        <span className="px">
          {fmt(f.price, b.digits)}
          {f.today_change_pct !== null && (
            <span className={f.today_change_pct >= 0 ? "chg up" : "chg down"}>
              {" "}
              {f.today_change_pct >= 0 ? "+" : ""}
              {f.today_change_pct.toFixed(2)}%
            </span>
          )}
        </span>
      </header>

      {f.day_note && <p className="muted daynote">{f.day_note}</p>}

      <div className="factors">
        {b.factors.map((x) => (
          <FactorCell key={x.key} f={x} />
        ))}
        <RangeCell b={b} />
      </div>

      {b.brief && <BriefRow br={b.brief} b={b} />}

      {v.exclude.length > 0 && (
        <div className="why excl">
          <strong>除外の理由</strong>
          <ul className="plain">
            {v.exclude.map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        </div>
      )}
      {v.cautions.length > 0 && (
        <div className="why caut">
          <strong>気をつけること</strong>
          <ul className="plain">
            {v.cautions.map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        </div>
      )}

      <PlanPanel pair={b.pair} excluded={v.state === "excluded"} hint={fmt(f.price, b.digits)} />
      <RangeBarView b={b} />
      <Levels b={b} />
      <Facts b={b} />

      <FundamentalsForm pair={b.pair} current={fund} />
    </section>
  );
}

export default function Boards({ boards }: { boards: Board[] }) {
  if (boards.length === 0) {
    return <p className="muted">ボードを作れる銘柄がありません。</p>;
  }
  return (
    <>
      <div className="boards">
        {boards.map((b) => (
          <BoardCard key={b.pair} b={b} />
        ))}
      </div>
      <p className="muted">
        {boards[0].verdict.note}
        建てなかった日を損とは数えません。
      </p>
    </>
  );
}
