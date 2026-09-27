# -*- coding: utf-8 -*-
"""指標の計算の試験。

**手で計算できる値と突き合わせる。** 「例外が出ない」だけの試験は、式を
取り違えても通ってしまうので意味がない。
"""
from __future__ import annotations

import math

import pytest

from app.indicator_engine import (
    adx,
    atr,
    bollinger,
    ema,
    macd,
    percentile_of_last,
    relative_level,
    rsi,
    sma,
    stochastic,
    swing_points,
    true_range,
)


def test_sma_matches_hand_calculation():
    vals = [1, 2, 3, 4, 5]
    out = sma(vals, 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)      # (1+2+3)/3
    assert out[3] == pytest.approx(3.0)
    assert out[4] == pytest.approx(4.0)
    assert len(out) == len(vals)


def test_ema_seed_is_sma_then_recursive():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    out = ema(vals, 3)
    assert out[2] == pytest.approx(2.0)      # 起点は単純平均
    k = 2 / 4
    assert out[3] == pytest.approx(4.0 * k + 2.0 * (1 - k))
    assert out[4] == pytest.approx(5.0 * k + out[3] * (1 - k))


def test_ema_returns_none_when_too_short():
    assert ema([1.0, 2.0], 5) == [None, None]


def test_rsi_all_gains_is_100():
    closes = [float(i) for i in range(1, 40)]
    out = rsi(closes, 14)
    assert out[-1] == pytest.approx(100.0)


def test_rsi_all_losses_is_zero():
    closes = [float(i) for i in range(40, 1, -1)]
    out = rsi(closes, 14)
    assert out[-1] == pytest.approx(0.0)


def test_rsi_is_between_0_and_100():
    closes = [100 + math.sin(i / 3) * 5 for i in range(80)]
    for v in rsi(closes, 14):
        if v is not None:
            assert 0.0 <= v <= 100.0


def test_macd_hist_is_line_minus_signal():
    closes = [100 + i * 0.5 for i in range(80)]
    line, sig, hist = macd(closes, 12, 26, 9)
    for a, b, c in zip(line, sig, hist):
        if a is not None and b is not None:
            assert c == pytest.approx(a - b)


def test_true_range_uses_previous_close():
    highs = [10.0, 12.0]
    lows = [9.0, 11.5]
    closes = [9.5, 11.8]
    tr = true_range(highs, lows, closes)
    assert tr[0] == pytest.approx(1.0)             # 初回は高安のみ
    # 2本目: max(12-11.5, |12-9.5|, |11.5-9.5|) = 2.5
    assert tr[1] == pytest.approx(2.5)


def test_atr_of_constant_range_equals_range():
    n = 40
    highs = [101.0] * n
    lows = [100.0] * n
    closes = [100.5] * n
    out = atr(highs, lows, closes, 14)
    assert out[-1] == pytest.approx(1.0, abs=1e-6)


def test_bollinger_bands_are_symmetric_around_middle():
    closes = [100 + math.sin(i / 4) * 2 for i in range(60)]
    mid, up, low, width = bollinger(closes, 20, 2.0)
    i = -1
    assert up[i] - mid[i] == pytest.approx(mid[i] - low[i])
    assert width[i] == pytest.approx((up[i] - low[i]) / mid[i])


def test_bollinger_zero_variance_gives_zero_width():
    closes = [100.0] * 40
    mid, up, low, width = bollinger(closes, 20, 2.0)
    assert up[-1] == pytest.approx(mid[-1])
    assert width[-1] == pytest.approx(0.0)


def test_adx_is_high_in_clean_trend_and_low_in_chop():
    n = 120
    trend_h = [100 + i * 0.5 + 0.5 for i in range(n)]
    trend_l = [100 + i * 0.5 - 0.5 for i in range(n)]
    trend_c = [100 + i * 0.5 for i in range(n)]
    trend_adx = adx(trend_h, trend_l, trend_c, 14)[-1]

    chop_c = [100 + (1 if i % 2 else -1) for i in range(n)]
    chop_h = [c + 0.5 for c in chop_c]
    chop_l = [c - 0.5 for c in chop_c]
    chop_adx = adx(chop_h, chop_l, chop_c, 14)[-1]

    assert trend_adx is not None and chop_adx is not None
    assert trend_adx > chop_adx


def test_stochastic_extremes():
    n = 30
    highs = [10.0] * n
    lows = [0.0] * n
    closes = [10.0] * n            # 最高値で引ける
    k, d = stochastic(highs, lows, closes, 14, 3)
    assert k[-1] == pytest.approx(100.0)
    closes_low = [0.0] * n
    k2, _ = stochastic(highs, lows, closes_low, 14, 3)
    assert k2[-1] == pytest.approx(0.0)


def test_percentile_handles_ties():
    """値がほぼ一定のとき 0 や 1 に振り切れないこと。

    ここが壊れていたせいで、滑らかな相場を「変動が異常」と判定して
    すべての取引を止める不具合を出した。
    """
    assert percentile_of_last([1.0] * 50) == pytest.approx(0.5)
    assert percentile_of_last(list(range(50))) > 0.9
    assert percentile_of_last(list(range(50, 0, -1))) < 0.1


def test_relative_level_is_scale_free():
    assert relative_level([1.0] * 50) == pytest.approx(1.0)
    assert relative_level([1.0] * 49 + [3.0]) == pytest.approx(3.0)
    assert relative_level([1.0] * 49 + [0.2]) == pytest.approx(0.2)
    assert relative_level([1.0, 2.0]) is None      # 本数が足りない


def test_swing_points_finds_local_extremes():
    # 明確な山と谷を作る
    highs = [1, 2, 5, 2, 1, 2, 6, 2, 1] * 3
    lows = [h - 1 for h in highs]
    sh, sl = swing_points(highs, lows, 2, 2, 120)
    assert sh, "山を1つも見つけられていません"
    assert all(highs[i] in (5, 6) for i in sh)


def test_swing_points_empty_on_monotonic_series():
    """単調な系列に転換点はない。ここで何か返すなら実装が誤っている。"""
    highs = [float(i) for i in range(100)]
    lows = [h - 1 for h in highs]
    sh, sl = swing_points(highs, lows, 2, 2, 120)
    assert sh == []
    assert sl == []


def test_all_indicator_series_have_same_length(cfg):
    from app.indicator_engine import compute_all
    from app.market_data import MockMarketDataProvider
    s = MockMarketDataProvider().get_candles("USDJPY", "H1", 300)
    out = compute_all(s, cfg.indicators)
    assert out, "指標がひとつも計算されていません"
    for name, arr in out.items():
        assert len(arr) == len(s), f"{name} の長さがずれています"
