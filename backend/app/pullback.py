# -*- coding: utf-8 -*-
"""押し目・戻りの判定（2026-10-20、利用者の指定「勝率を重点に」）。**参考・測定中。**

勝率だけを測る検証（win_lab、利確＝損切り・費用なし、根拠が無ければ 50%）で、試した 23 種の
うち**前半・後半とも 50% を確かに上回ったのはこれだけ**だった：

    日足の流れ（前日の終値が20日線の上、20日線が5日前より上）の向きに、
    1時間足の RSI(14) が逆へ振れた時（上の流れで 40以下＝押し目買い、下の流れで 60以上＝戻り売り）

ただし**円がらみ（USDJPY・EURJPY・GBPJPY など）では効いていない**（2年で 50.6%→45.7%、
直近12週の M15 でも 46.9%）。円以外は 54.1%→57.8%、直近12週の M15 で 53.4%。

ボードの判定（そろう・除外）と今日の判断には入れない。決まりは win_lab と共通（同じ関数）。
確定した1時間足だけを使う（足の時刻＋60分で確定）。
"""
from __future__ import annotations

import json
from bisect import bisect_left
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from . import signal_lab as S
from . import win_lab as W
from .board import fx_day_start
from .indicator_engine import rsi, sma

H1_LIMIT = 24 * 40                 # board.py と同じ本数（取り込みの手間を増やさない）
STALE_HOURS = 3


def state(provider, symbol: str, now: datetime) -> Optional[Dict]:
    try:
        candles = provider.get_candles(symbol, "H1", limit=H1_LIMIT).candles
    except Exception:                            # noqa: BLE001
        return None
    s = S.from_candles(candles, 60)
    n = s.upto(now)
    if n < 60:
        return None
    cut = S.Series(s.ts[:n], s.o[:n], s.h[:n], s.l[:n], s.c[:n], 60)
    days, _, _, _, dc = W.daily(cut)
    k = bisect_left(days, fx_day_start(now))       # 今日（まだ終わっていない日）は使わない
    k1 = k - 1
    if k1 < 25:
        return None
    s20 = sma(dc[:k1 + 1], 20)
    daily = W.daily_trend(dc[k1], s20[k1], s20[k1 - 5])
    r = rsi(cut.c, 14)[-1]
    last_at = cut.close_ts[-1]
    return {"daily": daily, "rsi": r, "lean": W.pullback_lean(daily, r),
            "sma20": s20[k1], "close_yday": dc[k1], "jpy": "JPY" in symbol,
            "last_at": last_at.isoformat(), "stale": (now - last_at) > timedelta(hours=STALE_HOURS)}


def stats(replay_dir: Path) -> Optional[Dict]:
    """最新の検証結果（毎週土曜に測り直す）。円がらみ／円以外の、2年の前半・後半と直近12週の M15。"""
    out: Dict = {}
    if not replay_dir.exists():
        return None
    longs = sorted(replay_dir.glob("win-20??-??-??.json"))
    if longs:
        try:
            d = json.loads(longs[-1].read_text(encoding="utf-8"))
            g = d["pullback_groups"]
            out["long"] = {"first": d["first"], "last": d["last"], "split": d["split"],
                           **{k: {"a": g[k]["前半"]["win"], "b": g[k]["後半"]["win"],
                                  "a_n": g[k]["前半"]["n"], "b_n": g[k]["後半"]["n"],
                                  "b_lo": g[k]["後半"]["lo"]} for k in ("jpy", "other")}}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    m15 = sorted(replay_dir.glob("win-m15-20??-??-??.json"))
    if m15:
        try:
            d = json.loads(m15[-1].read_text(encoding="utf-8"))
            g = d["pullback_groups"]
            out["m15"] = {"first": d["first"], "last": d["last"],
                          **{k: {"win": g[k]["win"], "n": g[k]["n"]} for k in ("jpy", "other")}}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    # 費用込み（scripts/win_cost.py）。計画と同じ形＝損切り ATR(H1)×1・利確 1.5倍 の、2年の前半・後半
    costs = sorted(replay_dir.glob("win-cost-20??-??-??.json"))
    if costs:
        try:
            d = json.loads(costs[-1].read_text(encoding="utf-8"))
            out["cost"] = {"first": d["long_period"][0], "last": d["long_period"][1]}
            for k, ja in (("jpy", "円がらみ"), ("other", "円以外")):
                e = d["long"][f"{ja}_1_1.5"]
                out["cost"][k] = {"a": e["a"]["avg"], "b": e["b"]["avg"], "b_hi": e["b"]["hi"],
                                  "a0": e["a0"]["avg"], "b0": e["b0"]["avg"]}
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            pass
    return out or None
