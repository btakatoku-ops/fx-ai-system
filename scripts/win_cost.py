# -*- coding: utf-8 -*-
"""押し目・戻り（win_lab で唯一 50% を上回った決まり）は、**費用を引いても残るか**（2026-10-20）。

費用は計画と同じ（board_replay._bracket）：スプレッドは片道で丸ごと、滑り込みは往復。
R＝損切り幅（滑り込み込み）を1とした、費用込みの損益。利確の倍率を変えて並べる。

- 約2年（1時間足）：損切り＝ATR(H1)×1（計画の ATR(M15)×2 の近似）。前半・後半に分ける。
- 直近12週（M15）：損切り＝計画と同じ ATR(M15)×2、判断は時間帯の始まりちょうど。
- 比べる相手：同じ時刻に、向きを問わず（買い・売り両方）建てた場合＝費用の重さそのもの。

    .venv/Scripts/python.exe scripts/win_cost.py

結果は logs/replay/win-cost-<日付>.md と .json。**過去の数え上げで、予測ではない。**
"""
from __future__ import annotations

import json
import math
import sys
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app import board_replay as R                              # noqa: E402
from app import long_lab as LL                                 # noqa: E402
from app import signal_lab as S                                # noqa: E402
from app import win_lab as W                                   # noqa: E402
from app.board import fx_day_start                             # noqa: E402
from app.config import get_trading_config                      # noqa: E402
from app.indicator_engine import atr as atr_series             # noqa: E402
from app.market_data import CSVMarketDataProvider              # noqa: E402

import win_check_m15 as C                                      # noqa: E402

JST = timezone(timedelta(hours=9))
LONG = ROOT / "data" / "long"
MULTS = (1.0, 1.5, 2.0, 3.0)
STOPS = (1.0, 2.0)                       # 損切り＝ATR(H1)×これ（2年）／ATR(M15)×2×これ（直近）
GROUPS = (("円以外", lambda r: "JPY" not in r["pair"]),
          ("うちドルストレート", lambda r: "JPY" not in r["pair"] and "USD" in r["pair"]),
          ("円がらみ", lambda r: "JPY" in r["pair"]))
PB = W.RULES["d_trend_pullback"][2]


def bars_of(s: "S.Series"):
    return [LL.Bar(s.ts[i], s.o[i], s.h[i], s.l[i], s.c[i]) for i in range(len(s.ts))]


def stat(vals: List[float], days: List[datetime], wins: List[int]) -> Dict:
    n = len(vals)
    if not n:
        return {"n": 0, "avg": None, "lo": None, "hi": None, "tp": None}
    mu = sum(vals) / n
    by = defaultdict(list)
    for v, d in zip(vals, days):
        by[d].append(v)
    lo = hi = None
    if len(by) > 1:
        k = len(by)
        dev = sum((sum(v) - mu * len(v)) ** 2 for v in by.values())
        se = math.sqrt(dev * k / (k - 1)) / n
        lo, hi = mu - 1.96 * se, mu + 1.96 * se
    return {"n": n, "avg": mu, "lo": lo, "hi": hi, "tp": sum(wins) / n}


def measure(cfg, rows: List[Dict], bars: Dict, atr_for, m: float, pick=None) -> Dict:
    """pick が None なら、同じ時刻に買い・売り両方（比べる相手）。"""
    vals, days, wins = [], [], []
    for r in rows:
        sides = ("up", "down") if pick is None else (pick(r),)
        for d in sides:
            if d not in ("up", "down"):
                continue
            got = atr_for(r)
            if got is None:
                continue
            start, close, a = got
            try:
                g = R._bracket(cfg, r["pair"], bars[r["pair"]], start, close, a, d == "up",
                               R.day_trade.exit_deadline(cfg.filters, r["at"]), m=m)
            except Exception:                    # noqa: BLE001  （スプレッドの設定が無い銘柄など）
                continue
            vals.append(g["r_net"])
            days.append(fx_day_start(r["at"]))
            wins.append(1 if g["outcome"] == "WIN" else 0)
    return stat(vals, days, wins)


def _r(x):
    return "—" if x is None else f"{x:+.3f}"


def cell(e: Dict) -> str:
    if not e["n"]:
        return "—"
    ci = "" if e["lo"] is None else f"［{e['lo']:+.2f}〜{e['hi']:+.2f}］"
    return f"{e['n']}件・**{_r(e['avg'])}R**{ci}・利確 {e['tp'] * 100:.0f}%"


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    cfg = get_trading_config()
    out: Dict = {"long": {}, "m15": {}}
    L: List[str] = ["# 押し目・戻りは費用を引いても残るか", "",
                    "費用は計画と同じ（スプレッドは片道で丸ごと・滑り込みは往復）。**R＝費用込みの損益（損切り幅を1）。**",
                    "比べる相手＝同じ時刻に向きを問わず建てた場合（費用の重さそのもの）。"
                    "95%の幅は日ごとにまとめて数えたもの。**幅の下側が0より上なら、費用込みでプラスと言える。**", ""]

    # ---- 約2年（1時間足）
    prov = CSVMarketDataProvider(LONG)
    pairs = {p: S.from_candles(prov.get_candles(p, "H1", limit=10 ** 6).candles, 60)
             for p in sorted(x.name.split("_")[0] for x in LONG.glob("*_H1.csv")
                             if x.name.split("_")[0] not in ("DXY", "US10Y", "GOLD", "VIX", "SPX"))}
    first = min(s.ts[0] for s in pairs.values()).date() + timedelta(days=20)
    last = max(s.ts[-1] for s in pairs.values()).date()
    rows: List[Dict] = []
    for p, s in pairs.items():
        rows += W.rows_for(cfg, p, s, LL.decision_times(cfg, first, last))
    bars = {p: bars_of(s) for p, s in pairs.items()}

    def h1_at_k(k):
        def h1_at(r):
            s = pairs[r["pair"]]
            return bisect_left(s.ts, r["at"]), r["close"], r["atr"] / 2.0 * k   # _bracket は×2（ATR(H1)×k）
        return h1_at

    L += [f"## 約2年（{first}〜{last}・1時間足）", "",
          "| 区分 | 損切り | 利確の倍率 | 前半：押し目・戻り | 前半：向きを問わず | 後半：押し目・戻り | 後半：向きを問わず |",
          "|---|---|---|---|---|---|---|"]
    for (grp, keep), k in [(g, k) for g in GROUPS for k in STOPS]:
        h1_at = h1_at_k(k)
        g_rows = [r for r in rows if keep(r)]
        a_rows = [r for r in g_rows if r["at"] < W.SPLIT]
        b_rows = [r for r in g_rows if r["at"] >= W.SPLIT]
        for m in MULTS:
            res = {"a": measure(cfg, a_rows, bars, h1_at, m, PB), "a0": measure(cfg, a_rows, bars, h1_at, m),
                   "b": measure(cfg, b_rows, bars, h1_at, m, PB), "b0": measure(cfg, b_rows, bars, h1_at, m)}
            out["long"][f"{grp}_{k:g}_{m:g}"] = res
            L.append(f"| {grp} | ATR(H1)×{k:g} | {m:g} 倍 | {cell(res['a'])} | {_r(res['a0']['avg'])}R | "
                     f"{cell(res['b'])} | {_r(res['b0']['avg'])}R |")

    # ---- 直近12週（M15・計画と同じ幅）
    rp = CSVMarketDataProvider(C.REAL)
    names = sorted({p.name.split("_")[0] for p in C.REAL.glob("*_M15.csv")}
                   & {p.name.split("_")[0] for p in C.REAL.glob("*_H1.csv")})
    h1, m15 = {}, {}
    for n in names:
        h1[n] = S.from_candles(rp.get_candles(n, "H1", limit=10 ** 6).candles, 60)
        m15[n] = S.from_candles(rp.get_candles(n, "M15", limit=10 ** 6).candles, 15)
    f2 = max(s.ts[0] for s in m15.values()).date() + timedelta(days=1)
    l2 = max(s.ts[-1] for s in m15.values()).date()
    ex = C.exact_times(cfg, f2, l2)
    rows2: List[Dict] = []
    for n in names:
        rows2 += W.rows_for(cfg, n, h1[n], ex, exec_s=m15[n])
    bars2 = {n: bars_of(s) for n, s in m15.items()}
    xatr = {n: atr_series(s.h, s.l, s.c, 14) for n, s in m15.items()}

    def m15_at_k(w):
        def m15_at(r):
            s = m15[r["pair"]]
            k = s.upto(r["at"])
            if k < 15 or not xatr[r["pair"]][k - 1]:
                return None
            return bisect_left(s.ts, r["at"]), s.c[k - 1], xatr[r["pair"]][k - 1] * w
        return m15_at

    L += ["", f"## 直近12週（{f2}〜{l2}・M15・損切り＝ATR(M15)×2（計画と同じ）／×4（2倍に広げる））", "",
          "| 区分 | 損切り | 利確の倍率 | 押し目・戻り | 向きを問わず |", "|---|---|---|---|---|"]
    for (grp, keep), w in [(g, w) for g in GROUPS for w in STOPS]:
        m15_at = m15_at_k(w)
        g_rows = [r for r in rows2 if keep(r)]
        for m in MULTS:
            res = {"s": measure(cfg, g_rows, bars2, m15_at, m, PB), "s0": measure(cfg, g_rows, bars2, m15_at, m)}
            out["m15"][f"{grp}_{w:g}_{m:g}"] = res
            L.append(f"| {grp} | ATR(M15)×{2 * w:g} | {m:g} 倍 | {cell(res['s'])} | {_r(res['s0']['avg'])}R |")

    path = ROOT / "logs" / "replay" / f"win-cost-{datetime.now(JST):%Y-%m-%d}.md"
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps(
        {"long_period": [str(first), str(last)], "m15_period": [str(f2), str(l2)], **out},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(L))
    print(f"\n書きました: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
