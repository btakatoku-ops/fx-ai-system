# -*- coding: utf-8 -*-
"""構造・相場つき・点数・強制条件・順位付けの試験。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.analysis import analyze_pair
from app.filters import check_all, error_result
from app.market_data import MockMarketDataProvider
from app.market_structure import analyze as analyze_structure
from app.models import (
    Candle,
    CandleSeries,
    DataQuality,
    Direction,
    MarketStructure,
    NewsState,
    ProviderState,
    ProviderStatus,
    Regime,
    ScoreBreakdown,
    SetupQuality,
    Signal,
    SpreadInfo,
    StructureState,
)
from app.pair_ranker import rank_pairs
from app.regime_engine import classify as classify_regime
from app.signal_engine import classify_quality, decide_signal
from app.filters import FilterResult


# ---------------------------------------------------------------- 構造

def _series(pattern, tf="M15"):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cs = []
    for i, c in enumerate(pattern):
        cs.append(Candle(timestamp=base + timedelta(minutes=15 * i),
                         open=c, high=c + 0.5, low=c - 0.5, close=c, volume=1))
    return CandleSeries(pair="USDJPY", timeframe=tf, candles=cs)


def _zigzag(turns, steps=3):
    """折り返し点をつないで、はっきりした波形を作る。

    転換点は「左右より高い／低い」ことで見つけるので、同じ値が並ぶと
    見つからない。間を直線で埋めて、同値が出ないようにする。
    """
    out = []
    for a, b in zip(turns, turns[1:]):
        for k in range(steps):
            out.append(a + (b - a) * k / steps)
    out.append(turns[-1])
    return out


def test_structure_detects_higher_highs(cfg):
    # 高値も安値も切り上がる形
    turns = []
    for k in range(10):
        turns += [100.0 + 4 * k, 112.0 + 4 * k]
    st = analyze_structure(_series(_zigzag(turns)), cfg.indicators)
    assert st.structure is StructureState.BULLISH
    assert st.pattern


def test_structure_detects_lower_lows(cfg):
    turns = []
    for k in range(10):
        turns += [200.0 - 4 * k, 188.0 - 4 * k]
    st = analyze_structure(_series(_zigzag(turns)), cfg.indicators)
    assert st.structure is StructureState.BEARISH


def test_structure_unknown_when_too_short(cfg):
    st = analyze_structure(_series([100.0, 101.0, 102.0]), cfg.indicators)
    assert st.structure is StructureState.UNKNOWN


# ---------------------------------------------------------------- 相場つき

def test_regime_no_trade_when_indicators_missing(cfg):
    res = classify_regime({"atr14": [], "adx14": [], "ema20": [], "ema50": []},
                          MarketStructure(), cfg.regime)
    assert res.regime is Regime.NO_TRADE
    assert res.confidence == 0.0


def test_news_state_is_never_invented(cfg):
    """指標の状態を推測で埋めない。

    予定表を取り込んでいなければ UNKNOWN。取り込んでいれば、その中身から
    QUIET / UPCOMING / ACTIVE を返す。**「たぶん静か」で埋めない。**
    """
    from datetime import datetime, timezone

    from app.news import clear_cache, evaluate

    clear_cache()
    missing = evaluate(cfg.pair("USDJPY"), datetime.now(timezone.utc),
                       cfg.news, path="__no_such_calendar__.json")
    assert missing.state is NewsState.UNKNOWN
    assert missing.available is False


def test_strong_uptrend_is_classified_as_trend_up(cfg):
    p = MockMarketDataProvider(scenario="strong_uptrend")
    r = analyze_pair("USDJPY", p, cfg)
    assert r.regime is Regime.TREND_UP


def test_strong_downtrend_is_classified_as_trend_down(cfg):
    p = MockMarketDataProvider(scenario="strong_downtrend")
    r = analyze_pair("USDJPY", p, cfg)
    assert r.regime is Regime.TREND_DOWN


def test_strong_uptrend_never_produces_sell(cfg):
    p = MockMarketDataProvider(scenario="strong_uptrend")
    r = analyze_pair("USDJPY", p, cfg)
    assert r.signal is not Signal.SELL


def test_strong_downtrend_never_produces_buy(cfg):
    p = MockMarketDataProvider(scenario="strong_downtrend")
    r = analyze_pair("USDJPY", p, cfg)
    assert r.signal is not Signal.BUY


def test_regime_confidence_is_within_0_100(cfg):
    for sc in ("strong_uptrend", "range", "breakout", "high_volatility"):
        r = analyze_pair("USDJPY", MockMarketDataProvider(scenario=sc), cfg)
        assert 0.0 <= r.regime_score <= 100.0


# ---------------------------------------------------------------- 点数と判定

def test_score_categories_follow_config(cfg):
    """区分は「測れる配点に対する割合」で決まる。

    相関10点とニュース10点は材料が無く常に0点なので、到達しうる最大は
    80点。境目を絶対点の80で持つと最大と一致し、満点以外は BUY/SELL が
    出なくなる。だから割合で持つ。
    """
    from app.signal_engine import attainable_max

    am = attainable_max(cfg.signal)
    weights = cfg.signal["weights"]
    avail = cfg.signal["data_available"]
    assert am == pytest.approx(sum(v for k, v in weights.items() if avail[k]))

    assert classify_quality(am * 0.10, cfg.signal) is SetupQuality.NO_TRADE
    assert classify_quality(am * 0.65, cfg.signal) is SetupQuality.WATCH
    assert classify_quality(am * 0.75, cfg.signal) is SetupQuality.SETUP
    assert classify_quality(am * 0.85, cfg.signal) is SetupQuality.STRONG_SETUP
    assert classify_quality(am * 0.95, cfg.signal)         is SetupQuality.VERY_STRONG_SETUP


def test_attainable_max_excludes_categories_without_data(cfg):
    """材料の無い項目は満点に数えない。**配点だけあって0点の項目。**"""
    from app.signal_engine import attainable_max

    weights = cfg.signal["weights"]
    avail = cfg.signal["data_available"]
    # 満点は「材料がある項目」の合計になる
    assert attainable_max(cfg.signal) == pytest.approx(
        sum(v for k, v in weights.items() if avail[k]))
    # 材料が無いと宣言した項目は満点から外れる
    off = {**cfg.signal, "data_available": {**avail, "news": False}}
    assert attainable_max(off) == pytest.approx(
        attainable_max(cfg.signal) - weights["news"])
    assert attainable_max(cfg.signal) == pytest.approx(
        sum(v for k, v in weights.items() if avail[k]))
    # 材料を繋げば満点が増える
    enabled = {**cfg.signal,
               "data_available": {k: True for k in avail}}
    assert attainable_max(enabled) == pytest.approx(100.0)
    # 実行時に測れなかった項目は満点から外せる
    assert attainable_max(cfg.signal, ["correlation"]) == pytest.approx(
        attainable_max(cfg.signal) - weights["correlation"])


def test_high_score_cannot_override_hard_filter():
    """**点数がいくら高くても強制の見送りは覆せない。**"""
    blocked = FilterResult()
    blocked.block("スプレッドが広すぎます")
    sig, reasons = decide_signal(Direction.LONG,
                                 SetupQuality.VERY_STRONG_SETUP, blocked)
    assert sig is Signal.NO_TRADE
    assert "スプレッドが広すぎます" in reasons


def test_buy_is_reachable_when_direction_and_quality_align():
    """BUY / SELL に到達できることを、判定の契約として確かめる。

    ここが通らないなら、どんな相場でも建てられない仕組みになっている。
    """
    ok = FilterResult()
    assert decide_signal(Direction.LONG, SetupQuality.STRONG_SETUP, ok)[0] is Signal.BUY
    assert decide_signal(Direction.SHORT, SetupQuality.VERY_STRONG_SETUP, ok)[0] is Signal.SELL


def test_neutral_direction_never_buys_even_at_top_score():
    ok = FilterResult()
    sig, _ = decide_signal(Direction.NEUTRAL, SetupQuality.VERY_STRONG_SETUP, ok)
    assert sig is Signal.WAIT


def test_setup_quality_alone_does_not_trade():
    """80点未満は方向が決まっていても様子見。点数だけで建てない。"""
    ok = FilterResult()
    assert decide_signal(Direction.LONG, SetupQuality.SETUP, ok)[0] is Signal.WAIT
    assert decide_signal(Direction.LONG, SetupQuality.WATCH, ok)[0] is Signal.WAIT


def test_score_breakdown_total_matches_sum():
    b = ScoreBreakdown(trend=20, momentum=15, support_resistance=15,
                       volatility=10, price_action=10, correlation=10,
                       news=10, session=5, spread_risk=5)
    assert b.total == pytest.approx(100.0)


def test_score_breakdown_fields_match_the_configured_weights(cfg):
    """内訳の項目と配点の項目が一致していること。

    片方だけ変えると、合計に入らない配点や、配点の無い内訳ができる。
    """
    assert set(ScoreBreakdown().model_dump()) == set(cfg.signal["weights"])


# ---------------------------------------------------------------- 強制条件

def _dummy_series(n, tf, minutes, now):
    cs = [Candle(timestamp=now - timedelta(minutes=minutes * (n - i)),
                 open=150, high=151, low=149, close=150, volume=1)
          for i in range(n)]
    return CandleSeries(pair="USDJPY", timeframe=tf, candles=cs)


def test_filter_blocks_on_insufficient_candles(cfg):
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    series = {tf: _dummy_series(5, tf, m, now)
              for tf, m in (("H4", 240), ("H1", 60), ("M15", 15), ("M5", 5))}
    res = check_all(cfg.pair("USDJPY"), series, {}, None, 0, 0, cfg.filters,
                    now=now, timeframe_minutes={"H4": 240, "H1": 60, "M15": 15, "M5": 5})
    assert res.blocked
    assert res.data_quality is DataQuality.INSUFFICIENT


def test_filter_blocks_on_stale_data(cfg):
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    old = now - timedelta(days=10)
    series = {tf: _dummy_series(100, tf, m, old)
              for tf, m in (("H4", 240), ("H1", 60), ("M15", 15), ("M5", 5))}
    res = check_all(cfg.pair("USDJPY"), series, {}, None, 0, 0, cfg.filters,
                    now=now, timeframe_minutes={"H4": 240, "H1": 60, "M15": 15, "M5": 5})
    assert res.blocked
    assert any("古すぎ" in r for r in res.reasons)


def test_filter_blocks_on_extreme_spread(cfg):
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    series = {tf: _dummy_series(100, tf, m, now)
              for tf, m in (("H4", 240), ("H1", 60), ("M15", 15), ("M5", 5))}
    spread = SpreadInfo(pair="USDJPY", spread_price=1.0, spread_pips=100.0,
                        timestamp=now)
    res = check_all(cfg.pair("USDJPY"), series, {}, spread, 0, 0, cfg.filters,
                    now=now, timeframe_minutes={"H4": 240, "H1": 60, "M15": 15, "M5": 5})
    assert res.blocked
    assert any("スプレッド" in r for r in res.reasons)


def test_filter_blocks_on_h1_h4_conflict(cfg):
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    series = {tf: _dummy_series(100, tf, m, now)
              for tf, m in (("H4", 240), ("H1", 60), ("M15", 15), ("M5", 5))}
    res = check_all(cfg.pair("USDJPY"), series, {}, None, 1, -1, cfg.filters,
                    now=now, timeframe_minutes={"H4": 240, "H1": 60, "M15": 15, "M5": 5})
    assert any("H1 と H4" in r for r in res.reasons)


def test_error_result_always_blocks():
    assert error_result("何かが壊れました").blocked


# ---------------------------------------------------------------- 供給元の異常

class _OfflineProvider(MockMarketDataProvider):
    def provider_status(self):
        return ProviderStatus(name="offline", state=ProviderState.OFFLINE,
                              detail="接続できません")


def test_offline_provider_gives_no_trade(cfg):
    r = analyze_pair("USDJPY", _OfflineProvider(), cfg)
    assert r.signal is Signal.NO_TRADE
    assert r.data_quality is DataQuality.PROVIDER_ERROR


class _ExplodingProvider(MockMarketDataProvider):
    def get_candles(self, pair, timeframe, limit=300):
        raise RuntimeError("想定外の失敗")


def test_unexpected_exception_falls_back_to_no_trade(cfg):
    """**想定外でも BUY/SELL には倒さない。**"""
    r = analyze_pair("USDJPY", _ExplodingProvider(), cfg)
    assert r.signal is Signal.NO_TRADE
    assert r.direction is Direction.NEUTRAL
    assert r.score == 0.0


def test_unknown_pair_gives_no_trade(cfg):
    r = analyze_pair("XXXYYY", MockMarketDataProvider(), cfg)
    assert r.signal is Signal.NO_TRADE
    assert r.data_quality is DataQuality.INVALID


# ---------------------------------------------------------------- 順位付け

def test_ranking_covers_all_enabled_pairs(cfg, mock_provider):
    res = rank_pairs(mock_provider, cfg)
    assert res.analyzed == len(cfg.enabled_pairs()) == 26
    assert len({e.pair for e in res.entries}) == 26


def test_ranking_keeps_no_trade_pairs_visible(cfg, mock_provider):
    """見送りの銘柄も消さない。原因を追うのに要る。"""
    res = rank_pairs(mock_provider, cfg)
    assert any(e.signal is Signal.NO_TRADE for e in res.entries)


def test_ranking_places_actionable_above_no_trade(cfg, mock_provider):
    res = rank_pairs(mock_provider, cfg)
    order = [e.signal for e in res.entries]
    last_actionable = max((i for i, s in enumerate(order)
                           if s in (Signal.BUY, Signal.SELL, Signal.WAIT)), default=-1)
    first_no_trade = min((i for i, s in enumerate(order)
                          if s is Signal.NO_TRADE), default=len(order))
    assert last_actionable < first_no_trade


def test_ranking_ranks_are_sequential(cfg, mock_provider):
    res = rank_pairs(mock_provider, cfg)
    assert [e.rank for e in res.entries] == list(range(1, len(res.entries) + 1))


def test_one_broken_pair_does_not_break_ranking(cfg):
    class _PartlyBroken(MockMarketDataProvider):
        def get_candles(self, pair, timeframe, limit=300):
            if pair == "EURUSD":
                raise RuntimeError("この銘柄だけ壊れている")
            return super().get_candles(pair, timeframe, limit)

    res = rank_pairs(_PartlyBroken(), cfg)
    assert res.analyzed == 26
    broken = next(e for e in res.entries if e.pair == "EURUSD")
    assert broken.signal is Signal.NO_TRADE


def test_spread_filter_compares_against_the_stop_sizing_timeframe(cfg):
    """スプレッドは**損切り幅を決めている足**の ATR と比べること。

    以前は spread が M5 の ATR、損切りが M15 の ATR を見ていた。M5 の ATR は
    M15 の半分ほどなので、意図の倍ほど厳しい条件になっていた。
    「費用が見合うか」は、実際に賭ける幅と比べないと判断できない。
    """
    assert (cfg.filters["spread"]["atr_timeframe"]
            == cfg.filters["risk_reward"]["atr_timeframe"])


def test_spread_filter_uses_the_configured_timeframe(cfg):
    """設定した時間足の ATR を実際に見ていること（読み飛ばしていない）。"""
    from app.filters import check_all
    from app.models import SpreadInfo
    from datetime import datetime, timezone
    from app.synthetic import generate_series

    spec = cfg.pair("USDJPY")
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    series = {tf: generate_series("USDJPY", tf, 300, "range", 150.0, spec.pip,
                                  end_time=now, minutes=cfg.timeframe_minutes(tf))
              for tf in ("H4", "H1", "M15", "M5")}
    # M15 の ATR は大きく、M5 の ATR は小さい、と分かる値を置く
    ind = {"H4": {}, "H1": {"atr14": [1.0]},
           "M15": {"atr14": [1.0]}, "M5": {"atr14": [0.01]}}
    spread = SpreadInfo(pair="USDJPY", spread_price=0.1, spread_pips=10.0,
                        timestamp=now)

    f = dict(cfg.filters)
    f["spread"] = dict(f["spread"], atr_timeframe="M15", max_ratio_of_atr=0.25)
    r_m15 = check_all(spec=spec, series_by_tf=series, indicators_by_tf=ind,
                      spread=spread, h1_dir=1, h4_dir=1, cfg=f, now=now,
                      timeframe_minutes={tf: cfg.timeframe_minutes(tf)
                                         for tf in series})
    f["spread"] = dict(f["spread"], atr_timeframe="M5")
    r_m5 = check_all(spec=spec, series_by_tf=series, indicators_by_tf=ind,
                     spread=spread, h1_dir=1, h4_dir=1, cfg=f, now=now,
                     timeframe_minutes={tf: cfg.timeframe_minutes(tf)
                                        for tf in series})

    # 0.1 / 1.0 = 10% は通り、0.1 / 0.01 = 1000% は弾かれる
    assert not any("ATR の" in x for x in r_m15.reasons)
    assert any("ATR の" in x for x in r_m5.reasons)
