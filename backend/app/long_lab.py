# -*- coding: utf-8 -*-
"""約2年の1時間足で、候補の決まりを**前半で見つけ、後半で確かめる**（2026-10-18）。

8週間の再生（board_replay）では件数が少なく、たまたまの相場（ドル高の時期など）に引きずられる。
ここでは data/long の1時間足（scripts/fetch_long.py）で、同じ時刻（東京 09:00・ロンドン・NY の
始まり）に決まりを当てはめ、**前半（見つける）と後半（確かめる）に分けて**数える。
前半で良く、**後半でも良かったものだけ**を本物の候補とみなす。

**1時間足で近似している点（甘くはしない側に寄せてある）**

- 損切り幅 … 計画は M15 の ATR×2。1時間足では ATR(H1)×1 で代える（値動きは√時間で
  広がるので、M15 の2倍 ≒ H1 の1倍）。
- 足の中の順序 … 1本の中で利確と損切りの両方に触れたら損切りとみなす。1時間足は M15 より
  この「両方」が増えるので、**実際より不利な側に出る**。
- トレンド … H4 の EMA20/50 の代わりに H1 の EMA80/200（4倍の期間）。モメンタムは H1 の RSI・MACD。
- スプレッドは一定（平常時）。経済指標の除外・ファンダ・ブリーフは無し。
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import namedtuple
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import board_replay as R
from . import day_trade, sessions
from . import signal_lab as S
from .board import fx_day_start
from .config import TradingConfig
from .indicator_engine import atr as atr_series
from .indicator_engine import ema, macd, rsi

Bar = namedtuple("Bar", "timestamp open high low close")
SPLIT = datetime(2025, 7, 1, tzinfo=timezone.utc)      # これより前で見つけ、後で確かめる
MULTIPLES = (1.0, 1.5, 2.0, 3.0)


def decision_times(cfg: TradingConfig, first: date, last: date) -> List[Tuple[datetime, str]]:
    """東京 09:00 JST、ロンドン・NY の始まり（1時間足なので、始まりを含む時の終わり＝次の正時）。"""
    out = []
    d = first
    while d <= last:
        if d.weekday() < 5:
            tokyo = datetime(d.year, d.month, d.day, 0, 0, tzinfo=timezone.utc)      # 09:00 JST
            day = fx_day_start(tokyo)
            out.append((tokyo, sessions.TOKYO))
            for name in (sessions.LONDON, sessions.NEWYORK):
                at = sessions.open_at(name, day, getattr(cfg, "board", None))
                if at is not None:
                    at = at.replace(minute=0) + (timedelta(hours=1) if at.minute else timedelta(0))
                    out.append((at, name))
        d += timedelta(days=1)
    return out


def pair_features(series: "S.Series") -> Dict[str, List]:
    """1銘柄ぶんの指標（どれも、その足までの値だけで計算する）。"""
    c, h, l = series.c, series.h, series.l
    _, _, hist = macd(c)
    return {"atr": atr_series(h, l, c, 14), "e20": ema(c, 20), "e50": ema(c, 50),
            "e80": ema(c, 80), "e200": ema(c, 200), "rsi": rsi(c, 14), "hist": hist}


def run(cfg: TradingConfig, pairs: Dict[str, "S.Series"], drivers: Dict[str, Optional["S.Series"]],
        times: Sequence[Tuple[datetime, str]]) -> List[Dict]:
    rows: List[Dict] = []
    for pair, s in pairs.items():
        f = pair_features(s)
        bars = [Bar(s.ts[i], s.o[i], s.h[i], s.l[i], s.c[i]) for i in range(len(s.ts))]
        atr_ok = [(s.close_ts[i], v) for i, v in enumerate(f["atr"]) if v]
        atr_t, atr_v = [t for t, _ in atr_ok], [v for _, v in atr_ok]
        for t, session in times:
            n = s.upto(t)
            if n < 250 or n >= len(s.ts):
                continue
            i = n - 1
            if t - s.close_ts[i] > timedelta(hours=3):           # 足が欠けている時刻は使わない
                continue
            a = f["atr"][i]
            if not a or None in (f["e20"][i], f["e50"][i], f["e80"][i], f["e200"][i]):
                continue
            k = bisect_right(atr_t, t)
            win = atr_v[max(0, k - 24 * 20):k]
            vol = a / median(win) if len(win) >= 24 * 5 else None
            deadline = day_trade.exit_deadline(cfg.filters, t)
            if deadline is None:
                continue
            start = bisect_left(s.ts, t)
            row = {"pair": pair, "at": t.isoformat(), "session": session, "close": s.c[i], "atr": a,
                   "vol": vol,
                   "trend": ("up" if f["e20"][i] > f["e50"][i] and f["e80"][i] > f["e200"][i] else
                             "down" if f["e20"][i] < f["e50"][i] and f["e80"][i] < f["e200"][i] else None),
                   "mom": ("up" if (f["rsi"][i] or 50) >= 55 and (f["hist"][i] or 0) > 0 else
                           "down" if (f["rsi"][i] or 50) <= 45 and (f["hist"][i] or 0) < 0 else None),
                   "day": None, "dxy": None, "us10y": None, "res": {}}
            op = S._day_open(s, t)
            row["day"] = S._sign(s.c[i] - op) if op is not None else None
            ctx = {"t": t, "drivers": drivers}
            row["dxy"] = S.dxy_4h({"pair": pair}, ctx)
            row["us10y"] = S.us10y_session({"pair": pair}, ctx)
            end = bisect_left(s.ts, deadline)
            row["move"] = (s.c[end - 1] - s.c[i]) / a if end - 1 > i else None
            for m in MULTIPLES:
                row["res"][m] = {}
                for side, is_long in (("long", True), ("short", False)):
                    # 計画の損切りは ATR(M15)×2。1時間足では ATR(H1)×1 で代える（_bracket は×2 するので半分を渡す）
                    got = R._bracket(cfg, pair, bars, start, s.c[i], a / 2.0, is_long, deadline, m=m)
                    row["res"][m][side] = (got["outcome"], got["r_net"])
            rows.append(row)
    return rows


# ------------------------------------------------------------ 決まり（向き）

def _lean(r):
    if r["trend"] and r["trend"] == r["mom"]:
        return r["trend"]
    return None


RULES: Dict[str, Tuple[str, Callable]] = {
    "trend": ("トレンド（H1・H4相当の EMA がそろう）", lambda r: r["trend"]),
    "mom": ("モメンタム（H1 の RSI・MACD）", lambda r: r["mom"]),
    "lean": ("テクニカル2つがそろう（ボード相当）", _lean),
    "day": ("その日の動きに乗る", lambda r: r["day"]),
    "dxy": ("ドル指数の直近4時間", lambda r: r["dxy"]),
    "us10y": ("米10年金利（USDJPY）", lambda r: r["us10y"]),
    "dxy_and_trend": ("ドル指数とトレンドが同じ向き", lambda r: r["dxy"] if r["dxy"] and r["dxy"] == r["trend"] else None),
    "dxy_high_vol": ("ドル指数＋値動き1.2倍以上", lambda r: r["dxy"] if r["dxy"] and (r["vol"] or 0) >= 1.2 else None),
    "lean_high_vol": ("テクニカル2つ＋値動き1.2倍以上", lambda r: _lean(r) if (r["vol"] or 0) >= 1.2 else None),
    "trend_london": ("トレンド＋ロンドンの始まりだけ", lambda r: r["trend"] if r["session"] == sessions.LONDON else None),
}


def evaluate(rows: List[Dict], pick: Callable, m: float) -> Dict:
    """前半・後半に分けて、決まりの向き／同じ時刻の向きを問わず、を数える。"""
    out = {}
    for name, part in (("前半", [r for r in rows if datetime.fromisoformat(r["at"]) < SPLIT]),
                       ("後半", [r for r in rows if datetime.fromisoformat(r["at"]) >= SPLIT])):
        sig, base, hits = [], [], []
        for r in part:
            d = pick(r)
            if d not in ("up", "down"):
                continue
            side = "long" if d == "up" else "short"
            sig.append(r["res"][m][side])
            base += [r["res"][m]["long"], r["res"][m]["short"]]
            if r.get("move"):
                hits.append(1 if (r["move"] > 0) == (d == "up") else 0)
        s, b = R._stats(sig), R._stats(base)
        se = None
        if s["n"] > 1:
            rs = [x for _, x in sig]
            mu = sum(rs) / len(rs)
            se = (sum((x - mu) ** 2 for x in rs) / (len(rs) - 1)) ** 0.5 / len(rs) ** 0.5
        lo, hi = R.wilson(sum(hits), len(hits))
        out[name] = {"n": s["n"], "win_rate": s["win_rate"], "avg_r": s["avg_r"],
                     "r_lo": None if se is None else s["avg_r"] - 1.96 * se,
                     "r_hi": None if se is None else s["avg_r"] + 1.96 * se,
                     "base_r": b["avg_r"], "edge": (s["avg_r"] - b["avg_r"]) if s["n"] else None,
                     "dir": (sum(hits) / len(hits)) if hits else None, "dir_lo": lo, "dir_hi": hi}
    return out


def baseline(rows: List[Dict], m: float, key: Callable = lambda r: True) -> Dict:
    res = []
    for r in rows:
        if key(r):
            res += [r["res"][m]["long"], r["res"][m]["short"]]
    return R._stats(res)
