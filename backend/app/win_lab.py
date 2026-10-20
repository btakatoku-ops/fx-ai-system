# -*- coding: utf-8 -*-
"""勝率だけを測る検証（2026-10-20、利用者の指定「勝率を重点に、費用はそのあと」）。

**測り方を固定する（見かけの勝率に騙されないため）**

- 利確＝損切り（どちらも ATR(H1)×1）。費用は入れない。**根拠が無ければちょうど 50%** になる。
  （利確を近く・損切りを遠くにすれば勝率はいくらでも上がるので、その形では測らない）
- 1本の足の中で両方に触れたら 0.5（どちらが先か分からない。勝ちにも負けにもしない）。
- 刻限（day_trade.exit_deadline）までにどちらにも触れなければ、刻限の終値の向きで勝ち負け。
- 買いの勝ちと売りの勝ちは裏返し（買いの点 + 売りの点 = 1）。だから「同じ時刻にでたらめな向き」は
  いつもちょうど 50%。
- 相場全体が上げていた時期は「いつも買い」でも 50% を超える。**買いだけ・売りだけの勝率も並べ**、
  片側だけで稼いだ決まりを見分ける。

**本物とみなす条件**：前半（〜2025-07-01）で 55% 以上、**後半でも 55% 以上**、かつ後半の
95% の幅（日ごとにまとめて数えたもの）の下側が 50% より上。同じ日の銘柄どうしは一緒に動く
（ドル全面高など）ので、1件ずつ数えると幅が狭く出すぎる。日ごとの平均で数える。

**予測ではない。過去の数え上げ。** 1時間足での近似。
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import day_trade, sessions
from . import signal_lab as S
from .board import fx_day_start
from .board_replay import wilson
from .config import TradingConfig
from .indicator_engine import adx, atr as atr_series, bollinger, ema, macd, rsi, sma

SPLIT = datetime(2025, 7, 1, tzinfo=timezone.utc)
JST = timezone(timedelta(hours=9))
PASS = 0.55


# ------------------------------------------------------------ 結果（1対1・費用なし）

def outcome(s: "S.Series", start: int, entry: float, dist: float, deadline: datetime) -> Optional[float]:
    """買いの点（1＝勝ち・0＝負け・0.5＝分からない）。売りの点は 1 − これ。"""
    up, dn = entry + dist, entry - dist
    last = None
    for j in range(start, len(s.ts)):
        if s.ts[j] >= deadline:
            break
        hit_up, hit_dn = s.h[j] >= up, s.l[j] <= dn
        if hit_up and hit_dn:
            return 0.5
        if hit_up:
            return 1.0
        if hit_dn:
            return 0.0
        last = s.c[j]
    if last is None:
        return None
    return 1.0 if last > entry else 0.0 if last < entry else 0.5


# ------------------------------------------------------------ 日足（1時間足から作る）

def daily(s: "S.Series") -> Tuple[List[datetime], List[float], List[float], List[float], List[float]]:
    """為替の1日（NY17時替わり）ごとの始値・高値・安値・終値。"""
    days: List[datetime] = []
    o, h, l, c = [], [], [], []
    for i, t in enumerate(s.ts):
        d = fx_day_start(t)
        if not days or d != days[-1]:
            days.append(d)
            o.append(s.o[i]); h.append(s.h[i]); l.append(s.l[i]); c.append(s.c[i])
        else:
            h[-1] = max(h[-1], s.h[i]); l[-1] = min(l[-1], s.l[i]); c[-1] = s.c[i]
    return days, o, h, l, c


def _sign(x: Optional[float]) -> Optional[str]:
    if x is None or x == 0:
        return None
    return "up" if x > 0 else "down"


def _flip(d: Optional[str]) -> Optional[str]:
    return {"up": "down", "down": "up"}.get(d)


# ------------------------------------------------------------ 判断の時刻ごとの材料

def rows_for(cfg: TradingConfig, pair: str, s: "S.Series",
             times: Sequence[Tuple[datetime, str]], exec_s: Optional["S.Series"] = None,
             exec_mult: float = 2.0) -> List[Dict]:
    """判断の材料は 1時間足 ``s`` から。``exec_s``（M15）を渡すと、**結果だけ** M15 の足で、
    計画と同じ幅（ATR(M15)×exec_mult）で数える（1時間足での近似を確かめるため）。"""
    c, h, l = s.c, s.h, s.l
    x_atr = atr_series(exec_s.h, exec_s.l, exec_s.c, 14) if exec_s is not None else None
    a1 = atr_series(h, l, c, 14)
    e20, e50, e80, e200 = ema(c, 20), ema(c, 50), ema(c, 80), ema(c, 200)
    r14 = rsi(c, 14)
    _, _, hist = macd(c)
    _, bb_up, bb_lo, _ = bollinger(c, 20, 2.0)
    ax = adx(h, l, c, 14)
    days, do, dh, dl, dc = daily(s)
    d_sma20 = sma(dc, 20)
    d_atr = atr_series(dh, dl, dc, 14)
    atr_ok = [(s.close_ts[i], v) for i, v in enumerate(a1) if v]
    atr_t, atr_v = [t for t, _ in atr_ok], [v for _, v in atr_ok]

    out: List[Dict] = []
    for t, session in times:
        n = s.upto(t)
        if n < 250 or n >= len(s.ts):
            continue
        i = n - 1
        if t - s.close_ts[i] > timedelta(hours=3):
            continue
        a = a1[i]
        if not a or None in (e20[i], e50[i], e80[i], e200[i], r14[i], bb_up[i]):
            continue
        deadline = day_trade.exit_deadline(cfg.filters, t)
        if deadline is None:
            continue
        if exec_s is None:
            score = outcome(s, bisect_left(s.ts, t), c[i], a, deadline)
        else:
            xn = exec_s.upto(t)
            if xn < 15 or t - exec_s.close_ts[xn - 1] > timedelta(minutes=45) or not x_atr[xn - 1]:
                continue
            score = outcome(exec_s, bisect_left(exec_s.ts, t), exec_s.c[xn - 1],
                            x_atr[xn - 1] * exec_mult, deadline)
        if score is None:
            continue
        today = fx_day_start(t)
        k = bisect_left(days, today)               # 今日の位置（今日の足は t までの分だけ使う）
        if k < 22 or k >= len(days) or days[k] != today:
            continue
        # 今日の t までの高安（未来を見ない）
        j0 = bisect_left(s.ts, today)
        if j0 > i:                                  # 今日の足がまだ無い（欠け）
            continue
        hi_today, lo_today = max(h[j0:i + 1]), min(l[j0:i + 1])
        asia_end = today + timedelta(hours=9)      # 日替わり＋9時間＝東京の終わりごろ（15〜16時 JST）
        ja = bisect_left(s.ts, asia_end)
        asia = (max(h[j0:ja]), min(l[j0:ja])) if ja > j0 and ja <= i + 1 else None
        k1 = k - 1                                   # 前日（確定済み）
        win = atr_v[max(0, bisect_right(atr_t, t) - 24 * 20):bisect_right(atr_t, t)]
        wk = today - timedelta(days=today.weekday() if today.weekday() < 6 else 0)
        out.append({
            "pair": pair, "at": t, "session": session, "score": score,
            "close": c[i], "atr": a,
            "vol": a / median(win) if len(win) >= 24 * 5 else None,
            "trend": ("up" if e20[i] > e50[i] and e80[i] > e200[i] else
                      "down" if e20[i] < e50[i] and e80[i] < e200[i] else None),
            "mom": ("up" if r14[i] >= 55 and (hist[i] or 0) > 0 else
                    "down" if r14[i] <= 45 and (hist[i] or 0) < 0 else None),
            "rsi": r14[i], "adx": ax[i], "bb_up": bb_up[i], "bb_lo": bb_lo[i],
            "spread_ema": (e20[i] - e50[i]) / a,
            "day_open": do[k], "hi_today": hi_today, "lo_today": lo_today, "asia": asia,
            "pd_hi": dh[k1], "pd_lo": dl[k1], "pd_close": dc[k1],
            "d_sma20": d_sma20[k1], "d_sma20_prev": d_sma20[k1 - 5] if k1 >= 5 else None,
            "d_atr": d_atr[k1], "d_c5": dc[k1 - 5] if k1 >= 5 else None,
            "d_c20": dc[k1 - 20] if k1 >= 20 else None, "d_c1": dc[k1],
            "week_open": do[_week_first(days, k)],
        })
    return out


def _week_key(d: datetime):
    return (d + timedelta(hours=3)).isocalendar()[:2]     # 日曜夜（UTC）に始まる為替日は月曜の週


def _week_first(days: List[datetime], k: int) -> int:
    kk = k
    while kk > 0 and _week_key(days[kk - 1]) == _week_key(days[k]):
        kk -= 1
    return kk


# ------------------------------------------------------------ 候補（向きを言うもの）

def daily_trend(close_yday, sma20, sma20_5ago) -> Optional[str]:
    """日足の流れ：前日の終値が20日線の上で、20日線が5日前より上なら上（下も同じ）。ボードと共通。"""
    if None in (close_yday, sma20, sma20_5ago):
        return None
    if close_yday > sma20 and sma20 > sma20_5ago:
        return "up"
    if close_yday < sma20 and sma20 < sma20_5ago:
        return "down"
    return None


PULLBACK_RSI = 40                                    # 上の流れで RSI 40以下＝押し目、下の流れで 60以上＝戻り


def pullback_lean(daily: Optional[str], rsi_h1: Optional[float]) -> Optional[str]:
    """押し目・戻り：日足の流れの向きに、1時間足の RSI が逆へ振れた時（2026-10-20 の検証で唯一 50% を上回った）。"""
    if rsi_h1 is None:
        return None
    if daily == "up" and rsi_h1 <= PULLBACK_RSI:
        return "up"
    if daily == "down" and rsi_h1 >= 100 - PULLBACK_RSI:
        return "down"
    return None


def _daily_trend(r):
    return daily_trend(r["d_c1"], r["d_sma20"], r["d_sma20_prev"])


def _lean(r):
    return r["trend"] if r["trend"] and r["trend"] == r["mom"] else None


def _rsi_ext(r, lo=30, hi=70):
    return "up" if r["rsi"] <= lo else "down" if r["rsi"] >= hi else None


def _bb_out(r):
    return "up" if r["close"] < r["bb_lo"] else "down" if r["close"] > r["bb_up"] else None


def _asia_break(r):
    if r["session"] == sessions.TOKYO or not r["asia"]:
        return None
    hi, lo = r["asia"]
    return "up" if r["close"] > hi else "down" if r["close"] < lo else None


def _pd_break(r):
    return "up" if r["close"] > r["pd_hi"] else "down" if r["close"] < r["pd_lo"] else None


def _day_move(r):
    return (r["close"] - r["day_open"]) / r["d_atr"] if r["d_atr"] else None


def _big_day_fade(r):
    m = _day_move(r)
    if m is None or abs(m) < 0.8:
        return None
    return "down" if m > 0 else "up"


def _range_mode(r):
    return r["adx"] is not None and r["adx"] < 20


RULES: Dict[str, Tuple[str, str, Callable]] = {
    # 種類, 見出し, 向き
    "trend": ("基準", "トレンド（H1 の EMA20/50・80/200 がそろう）", lambda r: r["trend"]),
    "lean": ("基準", "ボード相当（トレンド＋モメンタム）", _lean),
    "rsi_rev": ("逆張り", "RSI 30以下で買い・70以上で売り", _rsi_ext),
    "rsi_rev_range": ("逆張り", "RSI 極端 ＋ ADX 20未満（もみ合い）", lambda r: _rsi_ext(r) if _range_mode(r) else None),
    "bb_rev": ("逆張り", "BB(20,2) の外で戻りを狙う", _bb_out),
    "bb_rev_range": ("逆張り", "BB の外 ＋ ADX 20未満", lambda r: _bb_out(r) if _range_mode(r) else None),
    "rsi_rev_lowvol": ("逆張り", "RSI 極端 ＋ 値動き0.8倍未満の日", lambda r: _rsi_ext(r) if (r["vol"] or 9) < 0.8 else None),
    "big_day_fade": ("逆張り", "今日すでに日足 ATR の0.8倍以上動いた → 逆", _big_day_fade),
    "day_fade": ("逆張り", "今日の始値より上なら売り（戻り）", lambda r: _flip(_sign(r["close"] - r["day_open"]))),
    "asia_break": ("抜け", "東京の高安をロンドン・NY で抜けた向き", _asia_break),
    "asia_fade": ("抜け", "東京の高安を抜けた → 逆（だまし狙い）", lambda r: _flip(_asia_break(r))),
    "pd_break": ("節目", "前日の高安を抜けた向き", _pd_break),
    "pd_fade": ("節目", "前日の高安を抜けた → 逆", lambda r: _flip(_pd_break(r))),
    "week_open": ("節目", "週の始値より上なら買い", lambda r: _sign(r["close"] - r["week_open"]) if r["week_open"] else None),
    "pd_close": ("節目", "前日の終値より上なら買い", lambda r: _sign(r["close"] - r["pd_close"])),
    "day_follow": ("流れ", "今日の始値より上なら買い", lambda r: _sign(r["close"] - r["day_open"])),
    "d_trend": ("大きい流れ", "日足：20日線の上で20日線が上向き", _daily_trend),
    "d_mom5": ("大きい流れ", "日足：5日前より上なら買い", lambda r: _sign(r["d_c1"] - r["d_c5"]) if r["d_c5"] else None),
    "d_mom20": ("大きい流れ", "日足：20日前より上なら買い", lambda r: _sign(r["d_c1"] - r["d_c20"]) if r["d_c20"] else None),
    "d_trend_lean": ("大きい流れ", "日足の流れ ＋ ボード相当が同じ向き",
                     lambda r: _lean(r) if _lean(r) and _lean(r) == _daily_trend(r) else None),
    "d_trend_pullback": ("大きい流れ", "日足の流れの向きに、H1 の RSI が逆へ振れた時（押し目・戻り）",
                         lambda r: pullback_lean(_daily_trend(r), r["rsi"])),
    "d_trend_adx": ("大きい流れ", "日足の流れ ＋ H1 の ADX 25以上",
                    lambda r: _daily_trend(r) if (r["adx"] or 0) >= 25 else None),
    "d_trend_asia": ("大きい流れ", "日足の流れの向きに東京の高安を抜けた",
                     lambda r: _asia_break(r) if _asia_break(r) and _asia_break(r) == _daily_trend(r) else None),
}


# ------------------------------------------------------------ 数える

def _half(rows: List[Dict], first: bool) -> List[Dict]:
    return [r for r in rows if (r["at"] < SPLIT) == first]


def evaluate(rows: List[Dict], pick: Callable) -> Dict:
    out = {}
    for name, part in (("前半", _half(rows, True)), ("後半", _half(rows, False))):
        pts, longs, shorts, by_day = [], [], [], defaultdict(list)
        for r in part:
            d = pick(r)
            if d not in ("up", "down"):
                continue
            p = r["score"] if d == "up" else 1.0 - r["score"]
            pts.append(p)
            (longs if d == "up" else shorts).append(p)
            by_day[fx_day_start(r["at"])].append(p)
        out[name] = summarize(pts, longs, shorts, by_day)
    return out


def summarize(pts, longs, shorts, by_day) -> Dict:
    n = len(pts)
    if not n:
        return {"n": 0, "win": None, "lo": None, "hi": None, "long": None, "short": None,
                "n_long": 0, "n_short": 0, "days": 0}
    win = sum(pts) / n
    lo, hi = wilson(round(sum(pts)), n)
    dlo = dhi = None
    if len(by_day) > 1:                          # 日ごとにまとめた幅（同じ日の銘柄は一緒に動くので）
        d = len(by_day)
        dev = sum((sum(v) - win * len(v)) ** 2 for v in by_day.values())
        se = math.sqrt(dev * d / (d - 1)) / n
        dlo, dhi = win - 1.96 * se, win + 1.96 * se
    return {"n": n, "win": win, "lo": dlo if dlo is not None else lo, "hi": dhi if dhi is not None else hi,
            "long": sum(longs) / len(longs) if longs else None,
            "short": sum(shorts) / len(shorts) if shorts else None,
            "n_long": len(longs), "n_short": len(shorts), "days": len(by_day)}


def verdict(e: Dict, min_n: int = 100) -> str:
    a, b = e["前半"], e["後半"]
    if a["n"] < min_n or b["n"] < min_n:
        return "件数不足"
    if (a["win"] or 0) >= PASS and (b["win"] or 0) >= PASS and (b["lo"] or 0) > 0.5:
        return "◎ 本物の候補"
    if (a["win"] or 0) > 0.5 and (b["win"] or 0) > 0.5 and (b["lo"] or 0) > 0.5:
        return "○ 50%は超える（55%に届かない）"
    if (a["win"] or 0) > 0.5 and (b["win"] or 0) > 0.5:
        return "△ 両方で50%超（偶然と区別できない）"
    return ""


def subgroups(rows: List[Dict], pick: Callable, key: Callable, min_n: int = 60) -> List[Tuple[str, Dict]]:
    """前半で良かった部分（銘柄・時間帯）を選び、**後半でどうだったか**を並べる（選び方の甘さを見せる）。"""
    groups = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    out = []
    for g, rs in groups.items():
        e = evaluate(rs, pick)
        if e["前半"]["n"] >= min_n and (e["前半"]["win"] or 0) >= PASS:
            out.append((g, e))
    return sorted(out, key=lambda x: -(x[1]["前半"]["win"] or 0))
