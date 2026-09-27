# -*- coding: utf-8 -*-
"""戦略の一覧（registry）と、追加した戦略。

ここで確かめたいのは3つ。

1. **設定に書いた戦略名が、実際に存在すること。** 綴りを間違えると、
   その相場つきは黙って「戦略なし」になる。
2. **同じ足で、抜けと抜けの失敗が両方は出ないこと。** 出るなら、
   どちらかの条件が緩すぎる。
3. **どの戦略で入ったかが返ること。** 返らないと、効かない戦略が
   効く戦略の成績に紛れる。
"""
from __future__ import annotations

import pytest

from app.models import Direction, MarketStructure, Regime
from app.strategies import (
    REGISTRY,
    Context,
    decide,
    decide_breakout,
    decide_failed_breakout,
    decide_momentum_flag,
    strategies_for,
)


def m15(closes, *, atr=1.0, highs=None, lows=None, ema20=None, rsi=50.0,
        atr_last=None):
    """M15 の指標の並びを、必要なところだけ作る。

    既定ではひげを付けない（高値＝安値＝終値）。帯がそのまま終値で決まる
    ので、どこが境目かを試験側で読み違えずに済む。
    """
    n = len(closes)
    atrs = [atr] * n
    if atr_last is not None:
        atrs[-1] = atr_last
    return {
        "close": list(closes),
        "high": list(highs) if highs else list(closes),
        "low": list(lows) if lows else list(closes),
        "atr14": atrs,
        "ema20": [ema20 if ema20 is not None else closes[-1]] * n,
        "rsi14": [rsi] * n,
        "support": [min(closes)] * n,
        "resistance": [max(closes)] * n,
    }


def ctx(ind, cfg, *, h1=None, regime=Regime.BREAKOUT, signal_cfg=None):
    return Context(h1=h1 or {}, h4={}, m15=ind, m5={},
                   structure=MarketStructure(), regime=regime,
                   signal_cfg=signal_cfg or {}, cfg=cfg)


BREAK_CFG = {"lookback_bars": 20, "max_bars_since_break": 3,
             "min_break_atr": 0.15, "max_extension_atr": 1.5,
             "min_atr_expansion": 1.0}
FAIL_CFG = {"lookback_bars": 20, "failure_window_bars": 5,
            "min_break_atr": 0.15, "min_return_atr": 0.1}


# ------------------------------------------------ 一覧そのもの


def test_every_strategy_named_in_the_config_exists(cfg):
    """**設定の綴り間違いを、試験で捕まえる。**

    知らない名前はその戦略を飛ばすだけなので、実行しても落ちない。
    落ちない代わりに、その相場つきが黙って「戦略なし」になる。
    """
    table = cfg.filters["regime_strategy"]["strategies"]
    for regime, value in table.items():
        names = [value] if isinstance(value, str) else list(value or ())
        for n in names:
            assert n in REGISTRY, f"{regime} に知らない戦略名: {n}"


def test_an_unknown_name_falls_to_the_safe_side():
    """知らない名前で全銘柄を止めない。その戦略だけ飛ばす。"""
    filters = {"regime_strategy": {"strategies": {"RANGE": ["typo", "range"]}}}
    got = [s.name for s in strategies_for(Regime.RANGE, filters)]
    assert got == ["range"]


def test_a_regime_with_no_strategy_returns_nothing(cfg):
    assert strategies_for(Regime.TRANSITION, cfg.filters) == []
    assert strategies_for(Regime.HIGH_VOLATILITY, cfg.filters) == []


def test_the_first_strategy_that_produces_a_direction_wins(cfg):
    """順に試し、**最初に出たものを採る。どれで入ったかを返す。**"""
    up = [10.0] * 40 + [11.0]
    ind = m15(up, atr=1.0, atr_last=1.3)
    d, reasons, used = decide(
        ctx(ind, {}, regime=Regime.BREAKOUT, signal_cfg=cfg.signal),
        cfg.filters)
    assert d is Direction.LONG
    assert used is not None and used.name == "breakout"
    assert reasons[0].startswith("抜けに付く")


def test_when_nothing_fires_the_reasons_name_each_strategy(cfg):
    """出なかったときは、**どの戦略が何で止まったか**が読めること。"""
    flat = [10.0] * 50
    d, reasons, used = decide(
        ctx(m15(flat), {}, regime=Regime.BREAKOUT, signal_cfg=cfg.signal),
        cfg.filters)
    assert d is Direction.NEUTRAL
    assert used is None
    assert any(r.startswith("[抜けに付く]") for r in reasons)


# ------------------------------------------------ 抜け


def test_a_clean_break_above_is_a_buy():
    closes = [10.0] * 20 + [11.0]
    d, why = decide_breakout(ctx(m15(closes), BREAK_CFG))
    assert d is Direction.LONG
    assert any("終値で抜け" in w for w in why)


def test_a_clean_break_below_is_a_sell():
    closes = [10.0] * 20 + [9.0]
    d, _ = decide_breakout(ctx(m15(closes), BREAK_CFG))
    assert d is Direction.SHORT


def test_a_wick_only_break_is_not_a_break():
    """**ひげだけでは抜けとしない。** 終値で外に出ていること。"""
    closes = [10.0] * 21
    highs = [10.0] * 20 + [13.0]          # 高値は大きく上抜けたが終値は戻った
    d, why = decide_breakout(ctx(m15(closes, highs=highs), BREAK_CFG))
    assert d is Direction.NEUTRAL
    assert any("抜けていません" in w for w in why)


def test_it_does_not_chase_a_break_that_already_ran():
    """伸びきってから飛びつかない。損切りが遠くなる。"""
    closes = [10.0] * 20 + [12.0]
    d, why = decide_breakout(ctx(m15(closes, atr=1.0), BREAK_CFG))
    assert d is Direction.NEUTRAL
    assert any("離れています" in w for w in why)


def test_a_stale_break_is_not_entered():
    """抜けてから時間が経ったものは、抜けの場面ではない。"""
    closes = [10.0] * 20 + [11.0, 11.0, 11.0, 11.0, 11.0]
    d, why = decide_breakout(ctx(m15(closes), BREAK_CFG))
    assert d is Direction.NEUTRAL


def test_a_break_without_volatility_expansion_is_skipped():
    """動きを伴わない抜けは続かない。"""
    closes = [10.0] * 20 + [11.0]
    cfg_exp = dict(BREAK_CFG, min_atr_expansion=1.15, atr_compare_bars=10)
    d, why = decide_breakout(ctx(m15(closes, atr=1.0), cfg_exp))
    assert d is Direction.NEUTRAL
    assert any("ATR が" in w for w in why)

    d2, _ = decide_breakout(
        ctx(m15(closes, atr=1.0, atr_last=1.3), cfg_exp))
    assert d2 is Direction.LONG


# ------------------------------------------------ 抜けの失敗


def test_a_break_that_came_back_is_faded():
    closes = [10.0] * 20 + [11.0, 9.8]
    d, why = decide_failed_breakout(ctx(m15(closes), FAIL_CFG))
    assert d is Direction.SHORT
    assert any("戻されています" in w for w in why)


def test_a_break_still_outside_is_not_a_failure():
    """**戻っていなければ失敗していない。** ここを省くと、抜けの途中で
    毎回逆に入ることになる。"""
    closes = [10.0] * 20 + [11.0, 11.2]
    d, why = decide_failed_breakout(ctx(m15(closes), FAIL_CFG))
    assert d is Direction.NEUTRAL
    assert any("まだ帯の外" in w for w in why)


def test_break_and_failed_break_never_point_opposite_ways():
    """**同じ足で、逆向きの指示を同時に出さないこと。**

    両方が出ること自体は構わない。上へ抜けて跳ね返され、そのまま下へ
    抜けた場面では、抜けも抜けの失敗も「下」を指す。これは矛盾ではない。
    いけないのは **同じ足で片方が買い・片方が売り**になること。条件が
    噛み合っていない証拠になる。
    """
    cases = [
        [10.0] * 20 + [11.0],                 # 抜けた直後
        [10.0] * 20 + [11.0, 9.8],            # 抜けて戻された
        [10.0] * 20 + [11.0, 11.2],           # 抜けたまま
        [10.0] * 20 + [9.0],                  # 下に抜けた
        [10.0] * 20 + [9.0, 10.2],            # 下に抜けて戻された
        [10.0] * 25,                          # 何も起きていない
    ]
    for closes in cases:
        ind = m15(closes)
        a, _ = decide_breakout(ctx(ind, BREAK_CFG))
        b, _ = decide_failed_breakout(ctx(ind, FAIL_CFG))
        if a is Direction.NEUTRAL or b is Direction.NEUTRAL:
            continue
        assert a is b, f"{closes[-3:]} で抜けと抜けの失敗が逆を指しています"


# ------------------------------------------------ 押し目継続


FLAG_CFG = {"impulse_bars": 10, "min_impulse_atr": 2.0, "max_retrace": 0.5,
            "max_distance_ema20_atr": 0.8, "require_h1_agreement": False}


def test_a_shallow_pullback_after_a_push_is_entered():
    closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 16.0, 15.6, 15.4, 15.4]
    d, why = decide_momentum_flag(
        ctx(m15(closes, atr=1.0, ema20=15.3), FLAG_CFG))
    assert d is Direction.LONG
    assert any("押しは" in w for w in why)


def test_a_deep_pullback_is_not_a_flag():
    closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 15.0, 13.5, 12.5, 12.4]
    d, why = decide_momentum_flag(
        ctx(m15(closes, atr=1.0, ema20=12.4), FLAG_CFG))
    assert d is Direction.NEUTRAL
    assert any("深く" in w for w in why)


def test_a_flat_market_is_not_a_flag():
    d, why = decide_momentum_flag(ctx(m15([10.0] * 12), FLAG_CFG))
    assert d is Direction.NEUTRAL
    assert any("届きません" in w for w in why)


def test_it_does_not_take_a_flag_against_the_higher_timeframe():
    """**上位足に逆らわない。**"""
    closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 16.0, 15.6, 15.4, 15.4]
    cfg_h1 = dict(FLAG_CFG, require_h1_agreement=True)
    down_h1 = {"ema20": [1.0], "ema50": [2.0]}
    d, why = decide_momentum_flag(
        ctx(m15(closes, atr=1.0, ema20=15.3), cfg_h1, h1=down_h1))
    assert d is Direction.NEUTRAL
    assert any("H1 の向きと逆" in w for w in why)


def test_a_flag_far_from_the_moving_average_is_not_a_pullback():
    closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 16.0, 15.8, 15.7, 15.7]
    d, why = decide_momentum_flag(
        ctx(m15(closes, atr=1.0, ema20=13.0), FLAG_CFG))
    assert d is Direction.NEUTRAL
    assert any("EMA20 から" in w for w in why)


# ------------------------------------------------ 安全の境界は変わらない


def test_adding_strategies_does_not_bypass_the_hard_filters(cfg):
    """戦略をいくつ増やしても、強制の見送りは覆らない。"""
    from app.filters import FilterResult
    from app.models import SetupQuality, Signal
    from app.signal_engine import decide_signal

    fr = FilterResult()
    fr.block("スプレッドが広すぎます")
    for regime in Regime:
        sig, _ = decide_signal(Direction.LONG, SetupQuality.VERY_STRONG_SETUP,
                               fr, regime=regime, filters_cfg=cfg.filters)
        assert sig is Signal.NO_TRADE
