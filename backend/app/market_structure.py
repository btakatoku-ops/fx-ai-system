# -*- coding: utf-8 -*-
"""相場の構造。高値・安値の切り上げ／切り下げを読む。

HH 高値切り上げ / HL 安値切り上げ / LH 高値切り下げ / LL 安値切り下げ

転換点の感度は設定で持つ。ここを固定値にすると、特定の相場に合わせ込んだ
だけのものになりやすい。

BOS と CHOCH は枠だけ用意して、判定は控えめにしてある。定義が流派で割れる
ところを断定すると、あとで意味が変わったときに全体が狂う。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from .indicator_engine import swing_points
from .models import MarketStructure, StructureState


def _labels(highs: Sequence[float], lows: Sequence[float],
            sh: List[int], sl: List[int]) -> List[str]:
    """転換点の並びを HH/HL/LH/LL に直す。"""
    marks: List[tuple] = []
    for i in sh:
        marks.append((i, "H", highs[i]))
    for i in sl:
        marks.append((i, "L", lows[i]))
    marks.sort(key=lambda x: x[0])

    out: List[str] = []
    last_h: Optional[float] = None
    last_l: Optional[float] = None
    for _, kind, value in marks:
        if kind == "H":
            if last_h is not None:
                out.append("HH" if value > last_h else "LH")
            last_h = value
        else:
            if last_l is not None:
                out.append("HL" if value > last_l else "LL")
            last_l = value
    return out


def analyze(series, cfg: Dict) -> MarketStructure:
    """足の並びから構造を読む。

    判定は直近4つの符号だけで決める。長く見るほど過去に引きずられ、いまの
    状態を表さなくなるため。
    """
    swing_cfg = cfg["swing"]
    highs, lows = series.highs, series.lows
    if len(highs) < swing_cfg["left"] + swing_cfg["right"] + 5:
        return MarketStructure(structure=StructureState.UNKNOWN)

    sh, sl = swing_points(highs, lows, swing_cfg["left"],
                          swing_cfg["right"], swing_cfg["lookback"])
    if not sh and not sl:
        return MarketStructure(structure=StructureState.UNKNOWN)

    labels = _labels(highs, lows, sh, sl)
    recent = labels[-4:]
    up = sum(1 for x in recent if x in ("HH", "HL"))
    down = sum(1 for x in recent if x in ("LL", "LH"))

    if not recent:
        state = StructureState.UNKNOWN
    elif up > down:
        state = StructureState.BULLISH
    elif down > up:
        state = StructureState.BEARISH
    else:
        state = StructureState.NEUTRAL

    last_sh = highs[sh[-1]] if sh else None
    last_sl = lows[sl[-1]] if sl else None

    # BOS: 直近の転換点を終値で抜けたか。CHOCH: 直前までの向きと逆へ抜けたか。
    close = series.closes[-1]
    bos = False
    choch = False
    if last_sh is not None and close > last_sh:
        bos = True
        choch = state is StructureState.BEARISH
    elif last_sl is not None and close < last_sl:
        bos = True
        choch = state is StructureState.BULLISH

    return MarketStructure(
        structure=state,
        last_swing_high=last_sh,
        last_swing_low=last_sl,
        bos=bos,
        choch=choch,
        pattern=recent,
    )


def structure_direction(structure: MarketStructure) -> int:
    """構造を +1 / 0 / -1 に直す。点数付けで使う。"""
    if structure.structure is StructureState.BULLISH:
        return 1
    if structure.structure is StructureState.BEARISH:
        return -1
    return 0
