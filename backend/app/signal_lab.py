# -*- coding: utf-8 -*-
"""指標の候補を、過去の再生（board_replay）の上で一つずつ試す（2026-10-18）。

**足す前に測る。** 画面に▲▼を増やしても、当たらなければ判断は良くならない。

やり方：board_replay が作った各時刻（東京 08:42・ロンドン・NY の始まり）には、
「その時刻に買っていたら／売っていたら」TP1 と損切りのどちらに先に届いたかが入っている。
候補の指標が「その時刻に上／下」と言っていれば、その向きの結果を数える。

**比べる相手は、同じ時刻に向きを問わず建てた場合**（買い・売り両方の平均）。時間帯や
値動きの大きさで成績が変わるのは向きと関係ないので、同じ時刻どうしで比べないと、
「良い時刻を選んだだけ」を「当たる指標」と取り違える。

**偶然に注意。** 候補をいくつも試せば、どれかは偶然よく見える。だから
(1) 95%の幅、(2) 期間を前半・後半に分けて**両方で**良いか、を必ず並べる。

**未来は見ない。** 指標はすべて、その時刻までに確定した足だけで計算する
（相関の材料は1時間足なので、確定は時刻＋60分）。
"""
from __future__ import annotations

import csv
from bisect import bisect_right
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import board_replay as R
from .board import fx_day_start
from .config import PROJECT_ROOT, TradingConfig
from .indicator_engine import atr as atr_series

DRIVERS_DIR = PROJECT_ROOT / "data" / "drivers"


# ------------------------------------------------------------ 足（銘柄・相関の材料）


class Series:
    """時刻順の足。**その時刻までに確定した足**だけを返す。"""

    def __init__(self, ts: List[datetime], o, h, l, c, minutes: int) -> None:
        self.ts, self.o, self.h, self.l, self.c = ts, o, h, l, c
        self.close_ts = [t + timedelta(minutes=minutes) for t in ts]
        self.minutes = minutes

    def upto(self, t: datetime) -> int:
        """t までに確定した足の本数。"""
        return bisect_right(self.close_ts, t)


def from_candles(candles, minutes: int) -> Series:
    return Series([c.timestamp for c in candles], [float(c.open) for c in candles],
                  [float(c.high) for c in candles], [float(c.low) for c in candles],
                  [float(c.close) for c in candles], minutes)


def load_driver(name: str, path: Path = DRIVERS_DIR) -> Optional[Series]:
    p = path / f"{name}_H1.csv"
    if not p.exists():
        return None
    ts, o, h, l, c = [], [], [], [], []
    with p.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:                                  # 1行まるごと読めた時だけ足す（列がずれないように）
                close = float(r["close"])         # 相関の材料は終値だけの CSV（timestamp,close）
                vals = (datetime.fromisoformat(r["timestamp"]),
                        float(r.get("open") or close), float(r.get("high") or close),
                        float(r.get("low") or close), close)
            except (KeyError, ValueError, TypeError):
                continue
            for lst, v in zip((ts, o, h, l, c), vals):
                lst.append(v)
    return Series(ts, o, h, l, c, 60)


# ------------------------------------------------------------ 候補（向きを言うもの）
# どれも (row, ctx) -> "up" / "down" / None。ctx には銘柄の M15 と相関の材料が入る。


def _sign(x: Optional[float]) -> Optional[str]:
    if x is None or x == 0:
        return None
    return "up" if x > 0 else "down"


def trend_only(row, ctx):
    return {"up": "up", "down": "down"}.get(row["factors"].get("trend"))


def momentum_only(row, ctx):
    return {"up": "up", "down": "down"}.get(row["factors"].get("momentum"))


def _day_open(m15: Series, t: datetime) -> Optional[float]:
    start = fx_day_start(t)
    i = bisect_right(m15.ts, start - timedelta(seconds=1))
    return m15.o[i] if i < len(m15.ts) and m15.ts[i] < t else None


def day_follow(row, ctx):
    """その日の始値より上なら上（日中の勢いに乗る）。"""
    op = _day_open(ctx["m15"], ctx["t"])
    return _sign(row["price"] - op) if op is not None else None


def day_fade(row, ctx):
    """その日の始値より上なら下（戻りを狙う）。day_follow の裏返し。"""
    s = day_follow(row, ctx)
    return {"up": "down", "down": "up"}.get(s)


def prior_day_break(row, ctx):
    """前日の高値より上なら上、安値より下なら下（抜けた向きに乗る）。"""
    m15, t = ctx["m15"], ctx["t"]
    today = fx_day_start(t)
    yday = fx_day_start(today - timedelta(minutes=1))
    a, b = bisect_right(m15.ts, yday - timedelta(seconds=1)), bisect_right(m15.ts, today - timedelta(seconds=1))
    if b - a < 40:
        return None
    hi, lo = max(m15.h[a:b]), min(m15.l[a:b])
    if row["price"] > hi:
        return "up"
    if row["price"] < lo:
        return "down"
    return None


def _change(s: Optional[Series], t: datetime, bars: int) -> Optional[float]:
    if s is None:
        return None
    n = s.upto(t)
    if n <= bars:
        return None
    # 確定した最後の足が古すぎる（12時間より前）なら使わない
    if t - s.close_ts[n - 1] > timedelta(hours=12):
        return None
    return s.c[n - 1] - s.c[n - 1 - bars]


def dxy_4h(row, ctx):
    """ドル指数の直近4時間の向きを、その銘柄のドルの側に当てる。"""
    ch = _change(ctx["drivers"].get("DXY"), ctx["t"], 4)
    s = _sign(ch)
    if s is None:
        return None
    pair = row["pair"]
    if pair.startswith("USD"):
        return s
    if pair.endswith("USD"):
        return {"up": "down", "down": "up"}[s]
    return None


def vix_day(row, ctx):
    """VIX が直近（取れた範囲で6時間）上がっていれば、円高（円クロスは下）。"""
    pair = row["pair"]
    if not pair.endswith("JPY"):
        return None
    s = _sign(_change(ctx["drivers"].get("VIX"), ctx["t"], 6))
    return {"up": "down", "down": "up"}.get(s) if s else None


def us10y_session(row, ctx):
    """米10年金利が直近の米国時間（6時間）で上がっていれば、USDJPY は上。"""
    if row["pair"] != "USDJPY":
        return None
    return _sign(_change(ctx["drivers"].get("US10Y"), ctx["t"], 6))


ADX_STRONG = 25.0


def h1_adx(row, ctx) -> Optional[float]:
    """その時刻までに確定した H1 の ADX14（トレンドの強さ。向きは持たない）。"""
    h1, a = ctx.get("h1"), ctx.get("adx")
    if h1 is None or not a:
        return None
    n = h1.upto(ctx["t"])
    return a[n - 1] if n and a[n - 1] is not None else None


def adx_trend(row, ctx):
    """トレンド（H4・H1 の EMA）がそろい、かつ H1 の ADX が25以上の時だけ、その向き。"""
    v = h1_adx(row, ctx)
    return trend_only(row, ctx) if v is not None and v >= ADX_STRONG else None


def adx_lean(row, ctx):
    """テクニカルがそろった向き（除外も含む）を、H1 の ADX が25以上の時だけ数える。"""
    v = h1_adx(row, ctx)
    lean = row.get("lean")
    return lean if v is not None and v >= ADX_STRONG and lean in ("up", "down") else None


def _bb_at(ctx) -> Optional[Tuple[int, List, List, List]]:
    bb, m15 = ctx.get("bb"), ctx.get("m15")
    if not bb or m15 is None:
        return None
    n = m15.upto(ctx["t"])
    return (n,) + bb if n else None


def bb_width_pct(row, ctx) -> Optional[float]:
    """M15 のボリンジャーバンドの幅が、直近10日の中で下から何割か（0＝いちばん狭い）。"""
    got = _bb_at(ctx)
    if not got:
        return None
    n, _up, _lo, w = got
    win = [x for x in w[max(0, n - 960):n] if x is not None]
    if len(win) < 300 or w[n - 1] is None:
        return None
    return sum(1 for x in win if x < w[n - 1]) / len(win)


def bb_squeeze_break(row, ctx):
    """直近2時間のうちに幅が10日の下位20%まで縮み、いま終値が上の帯を超えたら上、下の帯を割ったら下。"""
    got = _bb_at(ctx)
    if not got:
        return None
    n, up, lo, w = got
    win = sorted(x for x in w[max(0, n - 960):n] if x is not None)
    recent = [x for x in w[max(0, n - 8):n] if x is not None]
    if len(win) < 300 or not recent or up[n - 1] is None or lo[n - 1] is None:
        return None
    if min(recent) >= win[int(len(win) * 0.2)]:     # 下位20%の線より**狭く**なっていなければ収縮ではない
        return None
    c = ctx["m15"].c[n - 1]
    return "up" if c > up[n - 1] else "down" if c < lo[n - 1] else None


CANDIDATES: Dict[str, Tuple[str, Callable]] = {
    "trend_only": ("トレンドだけ（H4・H1 の EMA がそろう）", trend_only),
    "momentum_only": ("モメンタムだけ（M15 の RSI・MACD）", momentum_only),
    "day_follow": ("その日の動きに乗る（始値より上なら買い）", day_follow),
    "day_fade": ("その日の動きの逆（始値より上なら売り）", day_fade),
    "prior_day_break": ("前日の高値・安値を抜けた向き", prior_day_break),
    "dxy_4h": ("ドル指数の直近4時間の向き（ドルの側）", dxy_4h),
    "vix_day": ("VIX の上昇で円高（円クロスのみ）", vix_day),
    "us10y_session": ("米10年金利の向き（USDJPY のみ）", us10y_session),
    "adx_trend": ("トレンド＋H1 の ADX が25以上", adx_trend),
    "adx_lean": ("テクニカルがそろった＋H1 の ADX が25以上", adx_lean),
    "bb_squeeze_break": ("ボリンジャーバンドの収縮のあとに帯を抜けた向き", bb_squeeze_break),
}


# ------------------------------------------------------------ 向きを言わないもの（場面の選び方）


def vol_ratio(row, ctx) -> Optional[float]:
    """いまの M15 の ATR ÷ 直近20日の ATR の中央値（1より大きい＝普段より動いている）。"""
    hist = ctx.get("atr_hist")
    if not hist:
        return None
    ts, vals = hist
    n = bisect_right(ts, ctx["t"])
    window = vals[max(0, n - 96 * 20):n]
    if len(window) < 96 * 5:
        return None
    m = median(window)
    return row["atr"] / m if m else None


# ------------------------------------------------------------ 数える


def _raw_up_move(row) -> Optional[float]:
    m = row.get("move")
    if m is None:
        return None
    return -m if row.get("lean") == "down" else m


def evaluate(rows: List[Dict], pick: Callable, ctx_for: Callable) -> Dict:
    """候補の向きで数え、同じ時刻の「向きを問わず」と比べる。前半・後半にも分ける。"""
    used, base, hits = [], [], []
    for r in rows:
        if "long_outcome" not in r:
            continue
        d = pick(r, ctx_for(r))
        if d not in ("up", "down"):
            continue
        side = "long" if d == "up" else "short"
        used.append((r["at"], r[f"{side}_outcome"], r[f"{side}_r"]))
        base += [(r["long_outcome"], r["long_r"]), (r["short_outcome"], r["short_r"])]
        mv = _raw_up_move(r)
        if mv:
            hits.append(1 if (mv > 0) == (d == "up") else 0)
    used.sort()
    half = len(used) // 2
    out = {"signal": R._stats([(o, x) for _, o, x in used]),
           "same_times": R._stats(base),
           "first_half": R._stats([(o, x) for _, o, x in used[:half]]),
           "second_half": R._stats([(o, x) for _, o, x in used[half:]])}
    k, n = sum(hits), len(hits)
    lo, hi = R.wilson(k, n)
    out["direction"] = {"n": n, "rate": k / n if n else None, "lo": lo, "hi": hi}
    s, b = out["signal"], out["same_times"]
    out["edge_r"] = (s["avg_r"] - b["avg_r"]) if s["n"] and b["n"] else None
    out["both_halves_better"] = bool(
        out["first_half"]["n"] and out["second_half"]["n"] and b["avg_r"] is not None
        and out["first_half"]["avg_r"] > b["avg_r"] and out["second_half"]["avg_r"] > b["avg_r"])
    return out


def by_bucket(rows: List[Dict], key: Callable, ctx_for: Callable,
              edges: Sequence[float], labels: Sequence[str],
              only_lean: bool = False) -> Dict[str, Dict]:
    """向きを言わない指標で場面を分ける。向きは「向きを問わず」か、ボードのそろった向き。"""
    out: Dict[str, List] = {lab: [] for lab in labels}
    for r in rows:
        if "long_outcome" not in r:
            continue
        v = key(r, ctx_for(r))
        if v is None:
            continue
        lab = labels[sum(1 for e in edges if v >= e)]
        if only_lean:
            if r["lean"] not in ("up", "down") or r["excluded"]:
                continue
            side = "long" if r["lean"] == "up" else "short"
            out[lab].append((r[f"{side}_outcome"], r[f"{side}_r"]))
        else:
            out[lab] += [(r["long_outcome"], r["long_r"]), (r["short_outcome"], r["short_r"])]
    return {lab: R._stats(v) for lab, v in out.items()}


# ------------------------------------------------------------ 東京の値幅をロンドンで抜ける


def tokyo_breakout(cfg: TradingConfig, symbol: str, m15c, days: Sequence, hours: int = 3) -> List[Tuple]:
    """東京（09:00 JST〜ロンドンの始まり）の高値・安値を、ロンドンの最初の数時間で
    終値が抜けたら、その向きに建てる（抜けた足の終値で）。1日1回まで。"""
    from . import day_trade, sessions

    m15 = from_candles(m15c, 15)
    a = atr_series(m15.h, m15.l, m15.c, 14)
    out: List[Tuple] = []
    for day_start in days:
        tokyo = sessions.open_at(sessions.TOKYO, day_start, getattr(cfg, "board", None))
        london = sessions.open_at(sessions.LONDON, day_start, getattr(cfg, "board", None))
        if tokyo is None or london is None:
            continue
        i0, i1 = bisect_right(m15.ts, tokyo - timedelta(seconds=1)), bisect_right(m15.ts, london - timedelta(seconds=1))
        if i1 - i0 < 16:
            continue
        hi, lo = max(m15.h[i0:i1]), min(m15.l[i0:i1])
        end = london + timedelta(hours=hours)
        for i in range(i1, len(m15.ts)):
            if m15.ts[i] >= end:
                break
            c = m15.c[i]
            d = "up" if c > hi else "down" if c < lo else None
            if d is None:
                continue
            t_entry = m15.close_ts[i]
            atr = a[i] if i < len(a) else None
            if not atr:
                break
            deadline = day_trade.exit_deadline(cfg.filters, t_entry)
            got = R._bracket(cfg, symbol, m15c, i + 1, c, float(atr), d == "up", deadline)
            other = R._bracket(cfg, symbol, m15c, i + 1, c, float(atr), d != "up", deadline)
            out.append((t_entry.isoformat(), got["outcome"], got["r_net"],
                        other["outcome"], other["r_net"]))
            break
    return out
