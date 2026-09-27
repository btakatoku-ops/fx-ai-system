# -*- coding: utf-8 -*-
"""持ち合い・変動の小さい相場の戦略。

いちばん大事なのは **同じ場面で、戦略が違えば向きが逆になる** こと。
抵抗帯のすぐ下は、トレンド追随なら「上げが続いている」だが、持ち合いなら
「上限に当たった＝売り」になる。この差が出ていなければ、戦略を分けた
意味がない。
"""
from __future__ import annotations

import pytest

from app.models import (
    Direction,
    MarketStructure,
    Regime,
    ScoreBreakdown,
    SetupQuality,
    Signal,
    StructureState,
)
from app.signal_engine import (
    RANGE,
    TREND,
    decide_direction,
    decide_direction_range,
    decide_signal,
    score_setup,
    strategy_for,
    strategy_supports,
)


@pytest.fixture
def rcfg(cfg):
    return cfg.filters["regime_strategy"]["range"]


def band(close, sup, res_, *, atr=0.5, rsi=50.0, history=None):
    """M15 の指標の並びを、必要なところだけ作る。

    ``close`` の並びは抜けの判定に使うので、既定では帯の内側で揃える。
    """
    hist = list(history) if history is not None else [(sup + res_) / 2] * 20
    hist.append(close)
    n = len(hist)
    return {
        "close": hist,
        "support": [sup] * n,
        "resistance": [res_] * n,
        "atr14": [atr] * n,
        "rsi14": [rsi] * n,
        "macd_hist": [0.0] * n,
    }


# ------------------------------------------------ 戦略の割り当て


def test_the_regime_decides_the_strategy(cfg):
    """相場つきに割り当てた戦略が引けること。**順番も意味を持つ。**"""
    from app.strategies import strategies_for

    def names(regime):
        return [s.name for s in strategies_for(regime, cfg.filters)]

    assert TREND in names(Regime.TREND_UP)
    assert RANGE in names(Regime.RANGE)
    assert RANGE in names(Regime.LOW_VOLATILITY)
    assert names(Regime.HIGH_VOLATILITY) == []
    assert strategy_for(Regime.TREND_UP, cfg.filters) == names(Regime.TREND_UP)[0]
    assert strategy_supports(Regime.RANGE, cfg.filters) is True
    assert strategy_supports(Regime.TRANSITION, cfg.filters) is False


def test_a_regime_missing_from_the_table_is_treated_as_unsupported():
    """**一覧に無い相場つきを、既定でトレンド扱いにしない。**

    既定を置くと、相場つきを増やしたときに前提の違う戦略で黙って
    判断してしまう。
    """
    cfg = {"regime_strategy": {"strategies": {"TREND_UP": "trend"}}}
    assert strategy_for(Regime.RANGE, cfg) is None
    assert strategy_supports(Regime.RANGE, cfg) is False


# ------------------------------------------------ 向きの決め方


def test_the_same_scene_gives_opposite_directions(cfg, rcfg):
    """**抵抗帯のすぐ下。追随なら買い、持ち合いなら売り。**"""
    up_ind = {"ema20": [1.10], "ema50": [1.09], "ema200": [1.05],
              "close": [1.0995]}
    trend_dir, _ = decide_direction(
        up_ind, up_ind,
        MarketStructure(structure=StructureState.BULLISH),
        Regime.TREND_UP, cfg.signal)
    assert trend_dir is Direction.LONG

    m15 = band(1.0995, 1.09, 1.10, atr=0.004, rsi=62.0)
    range_dir, reasons = decide_direction_range(m15, rcfg)
    assert range_dir is Direction.SHORT
    assert any("上端" in r for r in reasons)


def test_it_buys_at_the_lower_edge(rcfg):
    m15 = band(1.0905, 1.09, 1.10, atr=0.004, rsi=38.0)
    d, reasons = decide_direction_range(m15, rcfg)
    assert d is Direction.LONG
    assert any("下端" in r for r in reasons)


def test_it_does_not_enter_in_the_middle(rcfg):
    """**真ん中では入らない。** 損切りが近く、値幅も取れない。"""
    m15 = band(1.095, 1.09, 1.10, atr=0.004, rsi=50.0)
    d, reasons = decide_direction_range(m15, rcfg)
    assert d is Direction.NEUTRAL
    assert any("端に来るまで" in r for r in reasons)


def test_a_narrow_band_is_skipped(rcfg):
    """帯が狭ければ、当たっても取れる値幅が無い。"""
    m15 = band(1.0901, 1.09, 1.0910, atr=0.004, rsi=35.0)
    d, reasons = decide_direction_range(m15, rcfg)
    assert d is Direction.NEUTRAL
    assert any("帯の幅" in r for r in reasons)


def test_a_recent_breakout_stops_the_counter_trade(rcfg):
    """抜けていれば持ち合いは終わっている。**伸びる側へ逆らわない。**"""
    inside = [1.095] * 18
    m15 = band(1.0905, 1.09, 1.10, atr=0.004, rsi=38.0,
               history=inside + [1.1030])
    d, reasons = decide_direction_range(m15, rcfg)
    assert d is Direction.NEUTRAL
    assert any("抜けています" in r for r in reasons)


def test_the_edge_alone_is_not_enough(rcfg):
    """端に来ただけでは入らない。勢いも反転の側にあること。"""
    m15 = band(1.0905, 1.09, 1.10, atr=0.004, rsi=58.0)
    d, reasons = decide_direction_range(m15, rcfg)
    assert d is Direction.NEUTRAL
    assert any("反転の側にありません" in r for r in reasons)


def test_missing_atr_or_band_is_not_guessed(rcfg):
    no_atr = band(1.0905, 1.09, 1.10, atr=0.0, rsi=38.0)
    assert decide_direction_range(no_atr, rcfg)[0] is Direction.NEUTRAL
    no_band = band(1.0905, 1.09, 1.10, atr=0.004, rsi=38.0)
    no_band["support"] = [None] * len(no_band["support"])
    assert decide_direction_range(no_band, rcfg)[0] is Direction.NEUTRAL


# ------------------------------------------------ 点数の付け方


def _score(direction, strategy, cfg, *, structure, m15, h=None):
    flat = {"ema20": [1.0950], "ema50": [1.0950], "ema200": [1.0950],
            "atr14": [0.004], "close": [1.0950]}
    h = h or flat
    return score_setup(
        direction=direction, h1_ind=h, h4_ind=h, m15_ind=m15, m5_ind={},
        m15_structure=structure, regime_result=None, spread=None,
        max_spread_pips=0.0, cfg=cfg.signal, strategy=strategy)[0]


def test_a_break_of_structure_is_a_minus_for_the_range_strategy(cfg):
    """**同じ BOS が、追随では加点、持ち合いでは減点。**

    直近の転換点を終値で抜けたのは、追随には追い風だが、持ち合いには
    「前提が崩れた」合図。同じ扱いにすると、崩れた場面ほど高い点になる。
    """
    m15 = band(1.0905, 1.09, 1.10, atr=0.004, rsi=38.0)
    neutral = MarketStructure(structure=StructureState.NEUTRAL)
    broken = MarketStructure(structure=StructureState.NEUTRAL, bos=True)

    quiet = _score(Direction.LONG, RANGE, cfg, structure=neutral, m15=m15)
    after_break = _score(Direction.LONG, RANGE, cfg, structure=broken, m15=m15)
    assert after_break.price_action < quiet.price_action

    bull = MarketStructure(structure=StructureState.BULLISH)
    bull_bos = MarketStructure(structure=StructureState.BULLISH, bos=True)
    t_quiet = _score(Direction.LONG, TREND, cfg, structure=bull, m15=m15)
    t_break = _score(Direction.LONG, TREND, cfg, structure=bull_bos, m15=m15)
    assert t_break.price_action > t_quiet.price_action


def test_the_range_strategy_scores_the_absence_of_a_trend(cfg):
    """持ち合いの20点は「向きが出ていないこと」を測る。"""
    flat = {"ema20": [1.0950], "ema50": [1.0950], "atr14": [0.004]}
    trending = {"ema20": [1.1000], "ema50": [1.0900], "atr14": [0.004]}
    m15 = band(1.0905, 1.09, 1.10, atr=0.004, rsi=38.0)
    st = MarketStructure(structure=StructureState.NEUTRAL)
    a = _score(Direction.LONG, RANGE, cfg, structure=st, m15=m15, h=flat)
    b = _score(Direction.LONG, RANGE, cfg, structure=st, m15=m15, h=trending)
    assert a.trend > b.trend
    assert b.trend == pytest.approx(0.0)


def test_the_weights_are_not_changed_by_the_strategy(cfg):
    """**戦略で配点を外さない。** 外すと満点が下がって基準が緩む。"""
    from app.signal_engine import attainable_max

    m15 = band(1.0905, 1.09, 1.10, atr=0.004, rsi=38.0)
    st = MarketStructure(structure=StructureState.NEUTRAL)
    for strategy in (TREND, RANGE):
        b = _score(Direction.LONG, strategy, cfg, structure=st, m15=m15)
        assert isinstance(b, ScoreBreakdown)
        assert b.total <= attainable_max(cfg.signal)


def test_a_range_regime_can_now_reach_buy_and_sell(cfg):
    """戦略を持った以上、持ち合いでも売買まで到達すること。

    ただし **向きは持ち合いの決め方で出したものに限る**（呼び出し側の
    analysis が戦略で振り分ける）。
    """
    from app.filters import FilterResult

    clean = FilterResult()
    for regime in (Regime.RANGE, Regime.LOW_VOLATILITY):
        sig, _ = decide_signal(Direction.SHORT, SetupQuality.STRONG_SETUP,
                               clean, regime=regime, filters_cfg=cfg.filters)
        assert sig is Signal.SELL


def test_a_hard_filter_still_wins(cfg):
    """強制の見送りは、戦略を増やしても覆らない。"""
    from app.filters import FilterResult

    fr = FilterResult()
    fr.block("スプレッドが広すぎます")
    sig, _ = decide_signal(Direction.SHORT, SetupQuality.VERY_STRONG_SETUP,
                           fr, regime=Regime.RANGE, filters_cfg=cfg.filters)
    assert sig is Signal.NO_TRADE
