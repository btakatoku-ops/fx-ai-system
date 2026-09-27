# -*- coding: utf-8 -*-
"""合成データの生成。

**同じ入力からは必ず同じ足が出る**ようにしてある（乱数の種を固定）。試験が
実行のたびに揺れると、落ちた原因が仕様なのか偶然なのか分からなくなる。

ここで作るのは検査用の場面であって、相場の予測ではない。実データの代わりに
なると主張するものでもない。
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .models import Candle, CandleSeries

SCENARIOS = (
    "strong_uptrend",
    "strong_downtrend",
    "range",
    "breakout",
    "high_volatility",
    "low_volatility",
    "trend_pullback",
    "downtrend_pullback",
)


def _stable_hash(text: str) -> int:
    """起動しても変わらない整数を作る。

    Python の組み込み ``hash()`` は文字列に対して起動ごとに値が変わる
    （ハッシュの種が無作為化されるため）。それを使うと、同じ指定でも
    実行のたびに違う足が出てしまい、試験が落ちた理由を追えなくなる。
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _seed_for(pair: str, timeframe: str, scenario: str) -> int:
    """銘柄・時間足・場面から種を作る。実行のたびに同じ足になる。"""
    return _stable_hash(f"{pair}|{timeframe}|{scenario}") % (2 ** 31)


def generate_series(
    pair: str,
    timeframe: str,
    limit: int = 300,
    scenario: str = "range",
    start_price: float = 150.0,
    pip: float = 0.01,
    end_time: Optional[datetime] = None,
    minutes: int = 60,
) -> CandleSeries:
    """指定した場面の足を作る。

    価格の動きは「方向の成分 + 揺らぎ」で作る。場面ごとに両者の大きさを
    変えるだけの単純な作りにしてある。本物らしく見せる小細工は入れない。
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"未知の場面です: {scenario}")

    rnd = random.Random(_seed_for(pair, timeframe, scenario))
    end_time = end_time or datetime.now(timezone.utc).replace(second=0, microsecond=0)

    unit = pip * 10.0  # 1本あたりの基準の値幅
    drift, noise = {
        "strong_uptrend":   (0.45, 1.0),
        "strong_downtrend": (-0.45, 1.0),
        "range":            (0.0, 1.0),
        "breakout":         (0.0, 1.0),   # 後半で切り替える
        "high_volatility":  (0.0, 3.2),
        "low_volatility":   (0.0, 0.28),
        "trend_pullback":     (0.30, 0.7),
        "downtrend_pullback": (-0.30, 0.7),
    }[scenario]

    closes: List[float] = []
    price = start_price
    for i in range(limit):
        d, n = drift, noise
        if scenario == "breakout":
            # 前半は狭いもみ合い、後半で一方向に放れる
            if i < int(limit * 0.75):
                d, n = 0.0, 0.25
            else:
                d, n = 0.9, 1.1
        if scenario in ("trend_pullback", "downtrend_pullback"):
            # トレンドの途中で逆へ押し、最後に元の向きへ戻り始める形。
            # 狙い目は「押しが終わって再開したところ」なので、そこまで作る。
            sign = 1.0 if scenario == "trend_pullback" else -1.0
            if limit - 16 <= i < limit - 5:
                d, n = -0.55 * sign, 0.5      # 押し目・戻り
            elif i >= limit - 5:
                d, n = 0.70 * sign, 0.5       # 元の向きへ再開
        if scenario == "range":
            # 中心に引き戻す成分を入れて、行ったり来たりさせる
            d = (start_price - price) / unit * 0.08
        price = price + d * unit + rnd.gauss(0.0, n) * unit
        price = max(price, pip * 10)
        closes.append(price)

    candles: List[Candle] = []
    prev_close = start_price
    for i, c in enumerate(closes):
        o = prev_close
        span = abs(c - o) + abs(rnd.gauss(0.0, 0.45)) * unit
        h = max(o, c) + abs(rnd.gauss(0.0, 0.3)) * unit + span * 0.1
        low = min(o, c) - abs(rnd.gauss(0.0, 0.3)) * unit - span * 0.1
        low = max(low, pip)
        ts = end_time - timedelta(minutes=minutes * (limit - 1 - i))
        candles.append(Candle(
            timestamp=ts,
            open=round(o, 6), high=round(h, 6),
            low=round(low, 6), close=round(c, 6),
            volume=round(1000 + abs(rnd.gauss(0, 300)), 2),
        ))
        prev_close = c

    return CandleSeries(pair=pair, timeframe=timeframe, candles=candles)


def scenario_for_pair(pair: str) -> str:
    """銘柄ごとに場面を割り当てる。一覧が単調にならないよう散らすだけ。"""
    return SCENARIOS[_stable_hash(pair) % len(SCENARIOS)]


def broken_candle_rows() -> List[dict]:
    """壊れた足の見本。検証が効いているかを確かめるのに使う。"""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        # high が low より低い
        {"timestamp": base, "open": 150.0, "high": 149.0, "low": 150.5, "close": 150.2},
        # high が close より低い
        {"timestamp": base, "open": 150.0, "high": 150.1, "low": 149.5, "close": 150.6},
        # low が open より高い
        {"timestamp": base, "open": 150.0, "high": 151.0, "low": 150.4, "close": 150.8},
        # 価格が負
        {"timestamp": base, "open": -1.0, "high": 151.0, "low": 149.0, "close": 150.0},
    ]
