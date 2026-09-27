# -*- coding: utf-8 -*-
"""相場つきの分類。

TREND_UP / TREND_DOWN / RANGE / BREAKOUT / HIGH_VOLATILITY /
LOW_VOLATILITY / NEWS / TRANSITION / NO_TRADE

しきい値はすべて config/regime.json にある。コードに数字を散らさない。

**NEWS は枠だけ用意し、勝手に立てない。** Phase 1 は指標カレンダーを持って
いないので、news_state は UNKNOWN のままにする。持っていない情報を持っている
ように見せるのが、いちばん危ない。

confidence は 0-100 の点数であって、確率ではない。「この相場つきだと言える
材料がどれだけ揃っているか」の目安にすぎない。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .indicator_engine import percentile_of_last, slope
from .models import (
    MarketStructure,
    NewsState,
    Regime,
    RegimeResult,
    StructureState,
)


def _ema_alignment(e20: Optional[float], e50: Optional[float],
                   e200: Optional[float]) -> int:
    """移動平均の並び。+1 上向き / -1 下向き / 0 揃っていない。"""
    if e20 is None or e50 is None or e200 is None:
        return 0
    if e20 > e50 > e200:
        return 1
    if e20 < e50 < e200:
        return -1
    return 0


def classify(indicators: Dict[str, List[Optional[float]]],
             structure: MarketStructure,
             cfg: Dict,
             news_state: NewsState = NewsState.UNKNOWN) -> RegimeResult:
    """指標と構造から相場つきを決める。

    順序に意味がある。まず「判定できない」を弾き、次に極端な変動、
    そのあとトレンドか持ち合いかを見る。資金の保全を先に置くため。
    """
    last = {k: (v[-1] if v else None) for k, v in indicators.items()}
    reasons: List[str] = []
    factors: Dict[str, object] = {}

    atr_v = last.get("atr14")
    adx_v = last.get("adx14")
    e20, e50, e200 = last.get("ema20"), last.get("ema50"), last.get("ema200")

    # 材料が足りないときは判定しない
    if atr_v is None or adx_v is None or e20 is None or e50 is None:
        return RegimeResult(
            regime=Regime.NO_TRADE, confidence=0.0, news_state=news_state,
            factors={"missing": True},
            reasons=["指標を計算できるだけの足がありません"],
        )

    align = _ema_alignment(e20, e50, e200)
    slope_v = slope(indicators.get("ema20", []), cfg["ema_slope"]["lookback"])
    norm_slope = (slope_v / atr_v) if (slope_v is not None and atr_v) else 0.0
    atr_pct = percentile_of_last(indicators.get("atr14", []),
                                 cfg["atr_percentile"].get("lookback", 100) if isinstance(
                                     cfg["atr_percentile"], dict) else 100)
    bbw_pct = percentile_of_last(indicators.get("bb_width", []), 100)

    factors.update({
        "ema_alignment": align,
        "ema_slope_norm": round(norm_slope, 4),
        "adx": round(adx_v, 2),
        "atr_percentile": None if atr_pct is None else round(atr_pct, 3),
        "bb_width_percentile": None if bbw_pct is None else round(bbw_pct, 3),
        "structure": structure.structure.value,
    })

    thr_adx = cfg["adx"]
    thr_slope = cfg["ema_slope"]
    thr_atr = cfg["atr_percentile"]
    thr_bbw = cfg["bollinger_width_percentile"]

    # 判定の順番には意味がある。
    # はっきりしたトレンドを先に見る。強い下降相場を「変動が小さい」と
    # 分類してしまうと、上位足の向きを取り違える。
    regime: Optional[Regime] = None
    thr_adx = cfg["adx"]
    thr_slope = cfg["ema_slope"]
    thr_atr = cfg["atr_percentile"]
    thr_bbw = cfg["bollinger_width_percentile"]

    strong_slope = abs(norm_slope) >= thr_slope["strong"]
    flat_slope = abs(norm_slope) < thr_slope["flat"]

    # --- 1. はっきりしたトレンド ---
    if adx_v >= thr_adx["trend"] and align != 0 and not flat_slope:
        regime = Regime.TREND_UP if align > 0 else Regime.TREND_DOWN
        reasons.append(
            f"ADX {adx_v:.1f} が {thr_adx['trend']} 以上で、移動平均も"
            f"{'上' if align > 0 else '下'}向きに並んでいる")
        if strong_slope:
            reasons.append("EMA20 の傾きも明確")

    # --- 2. 放れ（狭いもみ合いのあとバンドの外へ）---
    if regime is None and bbw_pct is not None:
        prior_pct = percentile_of_last(indicators.get("bb_width", [])[:-1], 100)
        was_squeeze = prior_pct is not None and prior_pct <= thr_bbw["squeeze"]
        upper, lower = last.get("bb_upper"), last.get("bb_lower")
        close = last.get("close")
        outside = (close is not None and upper is not None and lower is not None
                   and (close > upper or close < lower))
        if was_squeeze and outside:
            regime = Regime.BREAKOUT
            reasons.append("狭いもみ合いのあと、バンドの外へ出た")

    # --- 3. 極端な変動（トレンドと言い切れないときだけ）---
    if regime is None and atr_pct is not None:
        if atr_pct >= thr_atr["high_volatility"]:
            regime = Regime.HIGH_VOLATILITY
            reasons.append(f"ATR が直近の上位 {atr_pct:.0%} にあり、値動きが荒い")
        elif atr_pct <= thr_atr["low_volatility"]:
            regime = Regime.LOW_VOLATILITY
            reasons.append(f"ATR が直近の下位 {atr_pct:.0%} にあり、値動きが乏しい")

    # --- 4. 持ち合い / どちらとも言えない ---
    if regime is None:
        if adx_v < thr_adx["range"] and flat_slope:
            regime = Regime.RANGE
            reasons.append(
                f"ADX {adx_v:.1f} が {thr_adx['range']} 未満で、EMA20 もほぼ横ばい")
        else:
            regime = Regime.TRANSITION
            reasons.append("トレンドとも持ち合いとも言い切れない中間の状態")

    # --- 点数（確率ではない）---
    w = cfg["confidence"]["weights"]
    score = 0.0
    if regime in (Regime.TREND_UP, Regime.TREND_DOWN):
        if align != 0:
            score += w["ema_alignment"]
        score += w["ema_slope"] * min(abs(norm_slope) / max(thr_slope["strong"], 1e-9), 1.0)
        score += w["adx"] * min(max(adx_v - thr_adx["range"], 0) /
                                max(thr_adx["trend"] - thr_adx["range"], 1e-9), 1.0)
        want = StructureState.BULLISH if regime is Regime.TREND_UP else StructureState.BEARISH
        if structure.structure is want:
            score += w["market_structure"]
    elif regime is Regime.RANGE:
        score += w["adx"] * min(max(thr_adx["range"] - adx_v, 0) / max(thr_adx["range"], 1e-9), 1.0)
        if abs(norm_slope) < thr_slope["flat"]:
            score += w["ema_slope"]
        if structure.structure in (StructureState.NEUTRAL, StructureState.UNKNOWN):
            score += w["market_structure"]
    elif regime in (Regime.HIGH_VOLATILITY, Regime.LOW_VOLATILITY):
        score += w["atr_percentile"] * 2.0
    elif regime is Regime.BREAKOUT:
        score += w["bollinger_width"] * 2.0
        if align != 0:
            score += w["ema_alignment"] * 0.5

    if atr_pct is not None:
        score += w["atr_percentile"] * 0.3
    if bbw_pct is not None:
        score += w["bollinger_width"] * 0.3

    score = max(0.0, min(100.0, score))

    return RegimeResult(
        regime=regime, confidence=round(score, 1),
        news_state=news_state, factors=factors, reasons=reasons,
    )


def regime_direction(regime: Regime) -> int:
    """相場つきを方向に直す。持ち合いなどは 0。"""
    if regime is Regime.TREND_UP:
        return 1
    if regime is Regime.TREND_DOWN:
        return -1
    return 0
