# -*- coding: utf-8 -*-
"""数日〜数週間持つ型を、約2年の1時間足で測る（2026-10-18。**測るだけ。日計りの決まりは変えない**）。

日計り（刻限 01:00）では損切り幅が小さく、スプレッド・滑り込みの割合が大きい。試したどの決まりも、
1回あたり約 −0.2R の費用を超えられなかった（long_lab）。保有を延ばすと損切り幅が日足の ATR 基準に
なり、費用の割合が小さくなる。代わりに**スワップ（持ち越しの金利差）**が付くので、それも数える。

- 判断 … 毎日の終わり（ニューヨーク17時）に、日足（1時間足からまとめる）で向きを決める。
- 建値 … その日の終値にスプレッドを不利側に払う。損切り＝日足 ATR×2。
- 手仕舞い … N 日後の終値（5・10・20日）。利確を置く版（損切りの3倍）も数える。
  途中の値動きは1時間足でたどり、1本の中で両方に触れたら損切りとみなす（不利な側）。
- スワップ … config/swap.json（いまの値。過去にさかのぼって当てるので、時期によっては違う）を、
  持った暦日の数だけ足し引きする。円以外が決済通貨の銘柄は、手元の足の最新の円換算で直す。
- **重なり** … 同じ銘柄で玉を持っている間は、次の判断を見送る（実際に持てる玉だけを数える）。
- 前半（〜2025-06）で見つけ、後半で確かめる（long_lab と同じ）。
"""
from __future__ import annotations

from bisect import bisect_left
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import board_replay as R
from .board import fx_day_start
from .config import TradingConfig
from .indicator_engine import atr as atr_series
from .indicator_engine import ema

Day = namedtuple("Day", "start end open high low close")
SPLIT = datetime(2025, 7, 1, tzinfo=timezone.utc)
HOLDS = (5, 10, 20)
STOP_ATR = 2.0


def daily_bars(series) -> List[Day]:
    """1時間足を、為替の1日（ニューヨーク17時区切り）にまとめる。足が12本未満の日は使わない。"""
    days: Dict[datetime, List[int]] = {}
    for i, t in enumerate(series.ts):
        days.setdefault(fx_day_start(t), []).append(i)
    out = []
    for d in sorted(days):
        idx = days[d]
        if len(idx) < 12:
            continue
        out.append(Day(d, series.close_ts[idx[-1]], series.o[idx[0]],
                       max(series.h[i] for i in idx), min(series.l[i] for i in idx), series.c[idx[-1]]))
    return out


def swap_per_day(cfg: TradingConfig, pair: str, is_long: bool, quote_jpy: Optional[float]) -> float:
    """1通貨・1日あたりのスワップを、その銘柄の価格の単位で（受け取りは正、支払いは負）。"""
    tbl = (getattr(cfg, "swap", None) or {}).get("table") or {}
    row = tbl.get(pair)
    if not row:
        return 0.0
    jpy = float(row["buy" if is_long else "sell"])            # 1万通貨・1日あたりの円
    unit = int((cfg.account or {}).get("accounting_unit", 10000))
    per_unit_jpy = jpy / unit
    if pair.endswith("JPY"):
        return per_unit_jpy
    return per_unit_jpy / quote_jpy if quote_jpy else 0.0


def simulate(cfg: TradingConfig, pair: str, h1, days: List[Day], k: int, is_long: bool, atr: float,
             hold: int, target_r: Optional[float], swap_day: float) -> Optional[Dict]:
    """k 日目の終わりに建て、hold 日後の終わりに閉じる（途中で損切り・利確に触れれば、そこで）。"""
    if k + hold >= len(days):
        return None
    spec = cfg.pair(pair)
    from . import plan_inputs
    slip = float(plan_inputs.costs(cfg, pair).slippage_pips_per_side) * spec.pip
    spread = cfg.spread_price(pair)
    c = days[k].close
    entry = c + spread if is_long else c - spread
    gross = STOP_ATR * atr
    loss = gross + 2 * slip
    stop = entry - gross if is_long else entry + gross
    target = None
    if target_r:
        dist = target_r * loss + 2 * slip
        target = entry + dist if is_long else entry - dist
    t0, t1 = days[k].end, days[k + hold].end
    i0, i1 = bisect_left(h1.ts, t0), bisect_left(h1.ts, t1)
    exit_px, exit_t, outcome = None, None, "TIME"
    for i in range(i0, min(i1, len(h1.ts))):
        hs = h1.l[i] <= stop if is_long else h1.h[i] >= stop
        ht = target is not None and (h1.h[i] >= target if is_long else h1.l[i] <= target)
        if hs:                                                 # 両方に触れたら損切り（不利な側）
            exit_px, exit_t, outcome = stop, h1.close_ts[i], "LOSS"
            break
        if ht:
            exit_px, exit_t, outcome = target, h1.close_ts[i], "WIN"
            break
    if exit_px is None:
        exit_px, exit_t = days[k + hold].close, t1
    move = (exit_px - entry) if is_long else (entry - exit_px)
    nights = max(0, (exit_t.date() - t0.date()).days)
    swap = swap_day * nights
    return {"r": (move - 2 * slip + swap) / loss, "swap_r": swap / loss, "outcome": outcome,
            "exit_day": next((j for j in range(k, len(days)) if days[j].end >= exit_t), k + hold)}


# ------------------------------------------------------------ 向きの決まり（日足）

def _sig_ema(f, k):
    a, b = f["e20"][k], f["e50"][k]
    return None if a is None or b is None else ("up" if a > b else "down")


def _sig_donchian(n):
    def fn(f, k):
        d = f["days"]
        if k < n:
            return None
        hi, lo = max(x.high for x in d[k - n:k]), min(x.low for x in d[k - n:k])
        return "up" if d[k].close > hi else "down" if d[k].close < lo else None
    return fn


def _sig_mom(n):
    def fn(f, k):
        d = f["days"]
        if k < n:
            return None
        return "up" if d[k].close > d[k - n].close else "down" if d[k].close < d[k - n].close else None
    return fn


RULES: Dict[str, Tuple[str, Callable]] = {
    "ema": ("日足 EMA20 と EMA50 の並び（常に持つ）", _sig_ema),
    "donchian20": ("20日の高値・安値を抜けた向き", _sig_donchian(20)),
    "donchian55": ("55日の高値・安値を抜けた向き", _sig_donchian(55)),
    "mom20": ("20日前より上なら買い（勢い）", _sig_mom(20)),
    "mom60": ("60日前より上なら買い（勢い）", _sig_mom(60)),
}


def run(cfg: TradingConfig, pairs: Dict[str, object], quote_jpy: Dict[str, float]) -> Dict:
    """決まり×保有日数×利確の有無で、前半・後半の結果を集める。向きを問わずの基準も。"""
    out: Dict = {}
    for pair, h1 in pairs.items():
        days = daily_bars(h1)
        if len(days) < 120:
            continue
        closes = [d.close for d in days]
        a = atr_series([d.high for d in days], [d.low for d in days], closes, 14)
        f = {"days": days, "e20": ema(closes, 20), "e50": ema(closes, 50)}
        qj = quote_jpy.get(pair[3:]) if not pair.endswith("JPY") else None
        swap = {True: swap_per_day(cfg, pair, True, qj), False: swap_per_day(cfg, pair, False, qj)}
        for hold in HOLDS:
            for tgt in (None, 3.0):
                key_b = ("基準：向きを問わず", hold, tgt)
                for k in range(60, len(days) - hold):
                    if not a[k]:
                        continue
                    half = "前半" if days[k].end < SPLIT else "後半"
                    for is_long in (True, False):
                        got = simulate(cfg, pair, h1, days, k, is_long, a[k], hold, tgt, swap[is_long])
                        if got:
                            out.setdefault(key_b, {}).setdefault(half, []).append((got["outcome"], got["r"], got["swap_r"]))
                for name, (label, fn) in RULES.items():
                    key = (label, hold, tgt)
                    k, busy_until = 60, -1
                    while k < len(days) - hold:
                        d = fn(f, k) if a[k] else None
                        if d in ("up", "down") and k > busy_until:
                            got = simulate(cfg, pair, h1, days, k, d == "up", a[k], hold, tgt, swap[d == "up"])
                            if got:
                                half = "前半" if days[k].end < SPLIT else "後半"
                                out.setdefault(key, {}).setdefault(half, []).append((got["outcome"], got["r"], got["swap_r"]))
                                busy_until = got["exit_day"]
                        k += 1
    return out


def stats(res: List[Tuple[str, float, float]]) -> Dict:
    n = len(res)
    if not n:
        return {"n": 0}
    rs = [r for _, r, _ in res]
    mu = sum(rs) / n
    se = (sum((x - mu) ** 2 for x in rs) / (n - 1)) ** 0.5 / n ** 0.5 if n > 1 else None
    return {"n": n, "avg_r": mu, "r_lo": None if se is None else mu - 1.96 * se,
            "r_hi": None if se is None else mu + 1.96 * se,
            "win": sum(1 for o, _, _ in res if o == "WIN") / n,
            "pos": sum(1 for r in rs if r > 0) / n,
            "swap_r": sum(s for _, _, s in res) / n}
