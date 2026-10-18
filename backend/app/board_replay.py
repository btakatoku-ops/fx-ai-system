# -*- coding: utf-8 -*-
"""過去の足でボードを作り直して、当たり具合を測る（2026-10-18）。

実際の記録（board_log）は30件たまるまでに何週間もかかる。そこで、手元にある過去の足
（M5 が約60日）で、**記録と同じ時刻**（東京 08:42・ロンドンの始まり・NY の始まり）に
ボードを作り直し、その後どう動いたかを数える。

**未来は見ない。** 判断は ``backtester.HistoryProvider`` を通す（その時刻までに確定した
足しか見えない）。判断側のコード（analyze_pair・build_board）は変えない。

**作り直せないもの**（ボードの3つの材料のうち）

- ファンダ（ブリーフ・手入力）… 過去の見立ては残っていない。**ここでは「無し」**にする。
  なので測っているのは「テクニカル2つ（トレンド・モメンタム）がそろった場面」。
- ブリーフの欄、経済指標の前後の除外 … 過去の予定表が無いので効かない（**甘い側に出る**）。
- スプレッドは一定（平常時）とみなす（**甘い側に出る**）。

**数え方**

- 向き … そろった向きに、区切り（その時間帯の終わり）・刻限までに動いたか（ADR 単位）。
- 利確 … 計画と同じ規則で、建値＝終値＋スプレッド（買い）、損切り＝ATR×倍率、
  TP1＝損切り幅（滑り込み込み）×最小の比。刻限までに TP1 と損切りのどちらに先に
  触れたか。**同じ足で両方に触れたら損切りとみなす**（甘くしない）。
- 比べる相手 … 同じ時刻に**向きを問わず**買い・売りした場合（＝費用だけの成績）。
  そろった場面がこれより良くなければ、そろうことに意味は無い。
"""
from __future__ import annotations

import logging
import math
from bisect import bisect_left
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from . import board as board_mod
from . import board_log, day_trade, sessions
from .analysis import ANALYSIS_TIMEFRAMES, analyze_pair
from .backtester import HistoryProvider, simulate_bracket
from .config import TradingConfig
from .market_data import MarketDataError

log = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))
TOKYO_RECORD = time(8, 42)                  # 朝のボードを残す時刻（朝の処理）


def board_times(cfg: TradingConfig, first: date, last: date) -> List[Tuple[datetime, str]]:
    """記録と同じ時刻の並び（平日のみ）。東京は 08:42 JST、ロンドン・NY は始まりの時刻。"""
    out: List[Tuple[datetime, str]] = []
    d = first
    while d <= last:
        if d.weekday() < 5:
            tokyo = datetime.combine(d, TOKYO_RECORD, JST).astimezone(timezone.utc)
            day = board_mod.fx_day_start(tokyo)
            out.append((tokyo, sessions.TOKYO))
            for name in (sessions.LONDON, sessions.NEWYORK):
                at = sessions.open_at(name, day, getattr(cfg, "board", None))
                if at is not None:
                    out.append((at, name))
        d += timedelta(days=1)
    return out


@contextmanager
def _no_fundamentals() -> Iterator[None]:
    """ファンダとブリーフを「無し」にする（過去の見立ては無いので、いまの物を混ぜない）。"""
    real_f, real_b = board_mod.fundamentals_factor, board_mod._brief_for

    def none_factor(cfg, symbol, now):
        f = board_mod.Factor("fundamentals", "ファンダ")
        f.text = "再生では使わない（過去の見立てが無い）"
        return f

    board_mod.fundamentals_factor = none_factor
    board_mod._brief_for = lambda symbol, now: None
    try:
        yield
    finally:
        board_mod.fundamentals_factor, board_mod._brief_for = real_f, real_b


def _bracket(cfg: TradingConfig, symbol: str, bars, start: int, close: float, atr: float,
             is_long: bool, deadline: Optional[datetime], m: Optional[float] = None) -> Dict:
    """計画と同じ規則で、刻限までに TP1 と損切りのどちらが先か。R は滑り込み込み。

    ``m`` は利確の倍率（損切り幅の何倍）。省略すると計画の規則（r_multiples の最初）。"""
    from . import plan_inputs                     # 滑り込みの仮定は計画と同じところから読む

    spec = cfg.pair(symbol)
    slip = float(plan_inputs.costs(cfg, symbol).slippage_pips_per_side) * spec.pip
    spread = cfg.spread_price(symbol)
    stop_mult = float(cfg.account["stop"]["atr_multiple"])
    if m is None:
        m = float(cfg.account["targets"]["r_multiples"][0])
    entry = close + spread if is_long else close - spread     # 片道でスプレッドを丸ごと払う
    gross = stop_mult * atr
    loss = gross + 2 * slip
    dist = m * loss + 2 * slip
    stop = entry - gross if is_long else entry + gross
    target = entry + dist if is_long else entry - dist
    got = simulate_bracket(bars, start, is_long, entry, stop, target,
                           max_hold=10 ** 6, deadline=deadline)
    # 滑り込みぶんを差し引いた R（損切り幅＝滑り込み込みの距離を1とする）
    move = (got["exit_price"] - entry) if is_long else (entry - got["exit_price"])
    got["r_net"] = round((move - 2 * slip) / loss, 4)
    return got


def replay_pair(cfg: TradingConfig, symbol: str, history: Dict, spread,
                times: Sequence[Tuple[datetime, str]]) -> List[Dict]:
    """1銘柄を、各時刻で作り直す。足が足りない時刻は飛ばす（理由を残す）。"""
    minutes = {tf: cfg.timeframe_minutes(tf) for tf in history}
    hp = HistoryProvider(symbol, history, spread, minutes)
    m15 = list(history["M15"].candles)
    m15_times = [c.timestamp for c in m15]
    rows: List[Dict] = []
    for t, session in times:
        if not m15_times or t <= m15_times[0] or t >= m15_times[-1]:
            continue
        hp.set_cutoff(t)
        try:
            a = analyze_pair(symbol, hp, cfg, now=t)
            b = board_mod.build_board(cfg, hp, symbol, a, t)
        except MarketDataError as exc:
            rows.append({"pair": symbol, "at": t.isoformat(), "session": session,
                         "skipped": str(exc)})
            continue
        f, v = b["facts"], b["verdict"]
        m = (a.indicators or {}).get("M15")
        atr = getattr(m, "atr14", None) if m is not None else None
        if isinstance(atr, (list, tuple)):
            atr = atr[-1] if atr else None
        price, adr = f.get("price"), f.get("adr")
        if price is None or not adr or not atr:
            rows.append({"pair": symbol, "at": t.isoformat(), "session": session,
                         "skipped": "価格・ADR・ATR のどれかが無い"})
            continue
        deadline = day_trade.exit_deadline(cfg.filters, t)
        seg_end = sessions.segment_end(session, t, deadline, getattr(cfg, "board", None))
        lean = v.get("lean")
        base = {"adr": adr, "price": price, "lean": lean}
        seg = board_log._measure(base, m15, t, seg_end) if seg_end else None
        dl = board_log._measure(base, m15, t, deadline) if deadline else None
        i0 = bisect_left(m15_times, t)
        start = i0 if i0 < len(m15_times) else None
        row = {"pair": symbol, "at": t.isoformat(), "session": session,
               "state": v.get("state"), "lean": lean,
               "excluded": v.get("state") == "excluded",
               "exclude": [str(x) for x in v.get("exclude") or []],
               "factors": {x["key"]: x["view"] for x in b["factors"]},
               "price": price, "adr": adr, "atr": float(atr),
               "seg_move": seg[1] if seg else None, "move": dl[1] if dl else None}
        if start is not None and deadline is not None:
            for side, is_long in (("long", True), ("short", False)):
                got = _bracket(cfg, symbol, m15, start, float(price), float(atr), is_long, deadline)
                row[f"{side}_outcome"], row[f"{side}_r"] = got["outcome"], got["r_net"]
        rows.append(row)
    return rows


def load_history(provider, symbol: str) -> Dict:
    """手元の CSV を、使える分だけ全部読む（時間足ごとに本数は違う）。"""
    return {tf: provider.get_candles(symbol, tf, limit=100000) for tf in ANALYSIS_TIMEFRAMES}


def replay(cfg: TradingConfig, provider, symbols: Sequence[str],
           first: date, last: date) -> List[Dict]:
    times = board_times(cfg, first, last)
    out: List[Dict] = []
    with _no_fundamentals():
        for sym in symbols:
            try:
                hist = load_history(provider, sym)
                spread = provider.get_spread(sym)
            except MarketDataError as exc:
                log.warning("履歴を読めません %s: %s", sym, exc)
                continue
            out.extend(replay_pair(cfg, sym, hist, spread, times))
    return out


# ------------------------------------------------------------------ 利確の倍率を変えて測る


def resim(cfg: TradingConfig, rows: List[Dict], m15_by_pair: Dict[str, List],
          multiples: Sequence[float]) -> Dict[str, Dict[float, Dict[str, Tuple[str, float]]]]:
    """同じ時刻・同じ建値・同じ損切りで、利確の倍率だけを変えた結果。

    **測るだけ。** 計画の規則（config/account.json targets）は変えない。"""
    out: Dict[str, Dict] = {}
    times = {p: [c.timestamp for c in bars] for p, bars in m15_by_pair.items()}
    for r in rows:
        if "skipped" in r or r["pair"] not in m15_by_pair:
            continue
        t = datetime.fromisoformat(r["at"])
        ts = times[r["pair"]]
        i = bisect_left(ts, t)
        start = i if i < len(ts) else None
        deadline = day_trade.exit_deadline(cfg.filters, t)
        if start is None or deadline is None:
            continue
        res: Dict[float, Dict] = {}
        for m in multiples:
            res[m] = {}
            for side, is_long in (("long", True), ("short", False)):
                got = _bracket(cfg, r["pair"], m15_by_pair[r["pair"]], start, float(r["price"]),
                               float(r["atr"]), is_long, deadline, m=m)
                res[m][side] = (got["outcome"], got["r_net"])
        out[f"{r['pair']}|{r['at']}"] = res
    return out


# ------------------------------------------------------------------ 週ごとに積み重ねる


def rules_fingerprint(cfg: TradingConfig) -> str:
    """判定と計画の規則の指紋。**規則が変わったら、前の再生と混ぜない。**"""
    import hashlib
    import json as _json

    keep = {"filters": cfg.filters, "board": cfg.board,
            "stop": cfg.account.get("stop"), "targets": cfg.account.get("targets"),
            "spreads": cfg.spreads}
    return hashlib.sha256(_json.dumps(keep, sort_keys=True, ensure_ascii=False,
                                      default=str).encode("utf-8")).hexdigest()[:12]


def merge_archive(path, rows: List[Dict], fingerprint: str) -> List[Dict]:
    """これまでの再生に今回の分を足す（同じ銘柄・同じ時刻は今回の分で置き換える）。

    手元の足は直近約60日しか無いので、毎週測り直して積み重ねる。規則が変わったら
    （指紋が違えば）前の分は別名で残し、新しく始める。"""
    import json as _json
    from pathlib import Path as _P

    p = _P(path)
    old: Dict[str, Dict] = {}
    if p.exists():
        try:
            data = _json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
        if data.get("fingerprint") == fingerprint:
            old = {f"{r['pair']}|{r['at']}": r for r in data.get("rows") or []}
        elif data:
            p.rename(p.with_name(f"{p.stem}-{data.get('fingerprint', 'old')}{p.suffix}"))
    for r in rows:
        if "skipped" not in r:
            old[f"{r['pair']}|{r['at']}"] = r
    merged = sorted(old.values(), key=lambda r: (r["at"], r["pair"]))
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(_json.dumps({"fingerprint": fingerprint, "rows": merged},
                               ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return merged


# ------------------------------------------------------------------ 集計


def wilson(k: int, n: int, z: float = 1.96) -> Tuple[Optional[float], Optional[float]]:
    """割合の95%の幅（少ない件数で割合を言い過ぎないため）。"""
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    w = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - w) / d, (c + w) / d


def _stats(results: List[Tuple[str, float]]) -> Dict:
    n = len(results)
    wins = sum(1 for o, _ in results if o == "WIN")
    losses = sum(1 for o, _ in results if o == "LOSS")
    lo, hi = wilson(wins, n)
    avg = sum(r for _, r in results) / n if n else None
    return {"n": n, "win": wins, "loss": losses, "timeout": n - wins - losses,
            "win_rate": wins / n if n else None, "win_lo": lo, "win_hi": hi,
            "avg_r": avg}


def _dir_stats(moves: List[float]) -> Dict:
    ms = [x for x in moves if x is not None and x != 0]
    k = sum(1 for x in ms if x > 0)
    lo, hi = wilson(k, len(ms))
    return {"n": len(ms), "hit": k, "rate": k / len(ms) if ms else None, "lo": lo, "hi": hi}


def summarize(rows: List[Dict]) -> Dict:
    """そろった場面（向きの側）と、比べる相手（同じ時刻・向きを問わず）。"""
    ok = [r for r in rows if "skipped" not in r and "long_outcome" in r]

    def side(r):
        return "long" if r["lean"] == "up" else "short"

    leaned = [r for r in ok if r["lean"] in ("up", "down")]
    groups = {
        "テクニカルがそろった（除外も含む）": leaned,
        "ボードが「そろった」（除外なし）": [r for r in leaned if not r["excluded"]],
        "そろって除外された": [r for r in leaned if r["excluded"]],
    }
    out: Dict = {"boards": len(ok), "skipped": len(rows) - len(ok), "groups": {}}
    for name, rs in groups.items():
        out["groups"][name] = {
            "tp1": _stats([(r[f"{side(r)}_outcome"], r[f"{side(r)}_r"]) for r in rs]),
            "to_segment": _dir_stats([r["seg_move"] for r in rs]),
            "to_deadline": _dir_stats([r["move"] for r in rs]),
        }
    both = [(r["long_outcome"], r["long_r"]) for r in ok] + [(r["short_outcome"], r["short_r"]) for r in ok]
    out["baseline"] = {"tp1": _stats(both)}
    by_pair: Dict[str, List] = {}
    for r in leaned:
        if not r["excluded"]:
            by_pair.setdefault(r["pair"], []).append((r[f"{side(r)}_outcome"], r[f"{side(r)}_r"]))
    out["by_pair"] = {p: _stats(v) for p, v in sorted(by_pair.items())}
    by_session: Dict[str, List] = {}
    for r in leaned:
        if not r["excluded"]:
            by_session.setdefault(r["session"], []).append((r[f"{side(r)}_outcome"], r[f"{side(r)}_r"]))
    out["by_session"] = {s: _stats(v) for s, v in by_session.items()}
    reasons: Dict[str, int] = {}
    for r in ok:
        for x in r["exclude"]:
            key = "".join("#" if ch.isdigit() else ch for ch in x)[:36]
            reasons[key] = reasons.get(key, 0) + 1
    out["exclude_reasons"] = sorted(reasons.items(), key=lambda kv: -kv[1])[:8]
    out["excluded_share"] = (sum(1 for r in ok if r["excluded"]) / len(ok)) if ok else None
    return out
