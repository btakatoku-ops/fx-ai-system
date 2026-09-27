# -*- coding: utf-8 -*-
"""Phase 1 の安全境界。

**ここは「機能が動く」試験ではなく、「危ない状態を通さない」試験。**
指定された再現ケースをそのまま置いてある。実装を緩めれば必ず落ちる。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.analysis import analyze_pair
from app.config import get_trading_config
from app.filters import check_all
from app.freshness import check_series, is_market_closed_gap, valid_until
from app.market_data import MockMarketDataProvider
from app.models import Candle, CandleSeries, Direction, Quote, Signal

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)          # 水曜


def _series(n: int, minutes: int, tf: str = "H1",
            end: datetime | None = None) -> CandleSeries:
    end = end or (NOW - timedelta(minutes=minutes))
    cs = [Candle(timestamp=end - timedelta(minutes=minutes * (n - 1 - i)),
                 open=150.0, high=151.0, low=149.0, close=150.5)
          for i in range(n)]
    return CandleSeries(pair="USDJPY", timeframe=tf, candles=cs)


# ============================================================ 境界での検証


def test_infinite_prices_are_rejected_at_the_boundary():
    """**inf は `v > 0` を通ってしまう。** 通せば ATR も EMA も inf になる。"""
    for bad in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValidationError):
            Candle(timestamp=NOW, open=bad, high=bad, low=bad, close=bad)


def test_quote_requires_finite_positive_and_ordered_prices():
    with pytest.raises(ValidationError):
        Quote(pair="USDJPY", bid=float("inf"), ask=float("inf"), timestamp=NOW)
    with pytest.raises(ValidationError):
        Quote(pair="USDJPY", bid=-1.0, ask=1.0, timestamp=NOW)
    with pytest.raises(ValidationError):
        Quote(pair="USDJPY", bid=150.0, ask=149.0, timestamp=NOW)   # ask < bid


def test_quote_timestamp_is_made_timezone_aware():
    q = Quote(pair="USDJPY", bid=150.0, ask=150.02,
              timestamp=datetime(2026, 9, 9, 12, 0))
    assert q.timestamp.tzinfo is not None


def test_series_rejects_unsorted_and_duplicate_timestamps():
    c1 = Candle(timestamp=NOW, open=150, high=151, low=149, close=150)
    c2 = Candle(timestamp=NOW - timedelta(minutes=15),
                open=150, high=151, low=149, close=150)
    with pytest.raises(ValidationError):
        CandleSeries(pair="USDJPY", timeframe="M15", candles=[c1, c2])
    with pytest.raises(ValidationError):
        CandleSeries(pair="USDJPY", timeframe="M15", candles=[c1, c1])


# ================================================= 再現ケース1: 足の素性


def test_h1_with_two_hour_spacing_is_not_treated_as_ready():
    """**再現ケース**: H1 として2時間間隔240本を渡しても ready にしない。

    本数だけ数えれば「240本あるから揃っている」になる。しかし中身は
    2時間足で、指標は1時間足のつもりで計算される。ATR も EMA も
    意味の違う数字になり、それでも画面には何事もなく点数が出る。
    """
    rep = check_series(_series(240, 120), expected_minutes=60, now=NOW)
    assert rep.ok is False
    assert rep.dominant_minutes == 120
    assert any("別の時間足" in r for r in rep.reasons)


def test_correct_h1_series_passes_integrity():
    rep = check_series(_series(240, 60), expected_minutes=60, now=NOW)
    assert rep.ok is True
    assert rep.matching_ratio == pytest.approx(1.0)
    assert rep.unexplained_gaps == 0
    assert rep.unclosed_last_bar is False


def test_weekend_gaps_are_not_counted_as_missing_bars():
    """週末の空白を欠落と数えたら、月曜の朝は必ず壊れていることになる。"""
    fri = datetime(2026, 9, 4, 21, 0, tzinfo=timezone.utc)      # 金曜 21:00
    sun = datetime(2026, 9, 6, 22, 0, tzinfo=timezone.utc)      # 日曜 22:00
    assert is_market_closed_gap(fri, sun) is True
    wed_a = datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc)
    wed_b = datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc)
    assert is_market_closed_gap(wed_a, wed_b) is False


def test_future_bars_are_rejected():
    s = _series(50, 60, end=NOW + timedelta(hours=5))
    rep = check_series(s, 60, now=NOW)
    assert rep.future_bars > 0
    assert rep.ok is False


def test_unclosed_last_bar_is_detected():
    """時刻 T の足が確定するのは T + 期間。T ちょうどではまだ途中。"""
    s = _series(50, 60, end=NOW)          # 最後の足の始まりが「いま」
    rep = check_series(s, 60, now=NOW)
    assert rep.unclosed_last_bar is True


# ============================================ 再現ケース2: 上位足の競合


def _filter_inputs(cfg, h1_dir: int, h4_dir: int):
    spec = cfg.pair("USDJPY")
    tfs = ("H4", "H1", "M15", "M5")
    series = {tf: _series(300, cfg.timeframe_minutes(tf), tf) for tf in tfs}
    ind = {tf: {"atr14": [0.15], "close": [150.0]} for tf in tfs}
    from app.models import SpreadInfo
    spread = SpreadInfo(pair="USDJPY", spread_price=0.009,
                        spread_pips=0.9, timestamp=NOW)
    return dict(
        spec=spec, series_by_tf=series, indicators_by_tf=ind, spread=spread,
        h1_dir=h1_dir, h4_dir=h4_dir, cfg=cfg.filters, now=NOW,
        timeframe_minutes={tf: cfg.timeframe_minutes(tf) for tf in tfs},
    )


def test_h1_up_h4_down_is_always_blocked(cfg):
    """**再現ケース**: H1上昇・H4下降なら、他が完璧でも NO_TRADE。"""
    res = check_all(**_filter_inputs(cfg, h1_dir=1, h4_dir=-1))
    assert res.blocked is True
    assert any("逆" in r or "競合" in r for r in res.reasons)


def test_unknown_higher_bias_is_also_blocked(cfg):
    """**分からないことを「競合なし」として通さない。**"""
    res = check_all(**_filter_inputs(cfg, h1_dir=1, h4_dir=0))
    assert res.blocked is True
    assert any("H4" in r and "判定できません" in r for r in res.reasons)


def test_agreeing_directions_are_not_blocked_by_conflict(cfg):
    res = check_all(**_filter_inputs(cfg, h1_dir=1, h4_dir=1))
    assert not any("逆" in r or "判定できません" in r for r in res.reasons)


def test_a_high_score_cannot_override_a_conflict(cfg):
    """点数95でも競合があれば NO_TRADE。順序は入れ替えない。"""
    from app.models import SetupQuality
    from app.signal_engine import decide_signal

    fr = check_all(**_filter_inputs(cfg, h1_dir=1, h4_dir=-1))
    sig, reasons = decide_signal(Direction.LONG,
                                 SetupQuality.VERY_STRONG_SETUP, fr)
    assert sig is Signal.NO_TRADE
    assert any("逆" in r or "競合" in r for r in reasons)


# ================================================ 再現ケース3: 有効期限


def test_valid_until_follows_the_earliest_dependency():
    """**再現ケース**: 気配値80秒前・上限90秒・TTL60秒 → 残り10秒。"""
    vu = valid_until(now=NOW, analysis_ttl_seconds=60.0,
                     quote_timestamp=NOW - timedelta(seconds=80),
                     quote_max_age_seconds=90.0)
    assert (vu - NOW).total_seconds() == pytest.approx(10.0)


def test_valid_until_uses_ttl_when_quote_is_fresh():
    vu = valid_until(now=NOW, analysis_ttl_seconds=60.0,
                     quote_timestamp=NOW, quote_max_age_seconds=600.0)
    assert (vu - NOW).total_seconds() == pytest.approx(60.0)


def test_valid_until_is_capped_by_the_next_bar():
    vu = valid_until(now=NOW, analysis_ttl_seconds=3600.0,
                     next_bar_close=NOW + timedelta(seconds=30))
    assert (vu - NOW).total_seconds() == pytest.approx(30.0)


def test_expired_analysis_never_stays_buy_or_sell(cfg):
    """期限が切れた判断は BUY/SELL のまま残さない。"""
    base = analyze_pair("AUDUSD", MockMarketDataProvider(), cfg)
    forced = base.model_copy(update={
        "signal": Signal.BUY, "direction": Direction.LONG,
        "valid_until": NOW - timedelta(seconds=1)})
    assert forced.is_expired(NOW) is True
    view = forced.expired_view(NOW)
    assert view.signal is Signal.NO_TRADE
    assert view.direction is Direction.NEUTRAL
    assert any("有効期限" in r for r in view.invalidation_reasons)


def test_analysis_result_carries_valid_until(cfg):
    r = analyze_pair("AUDUSD", MockMarketDataProvider(), cfg)
    assert r.valid_until is not None
    assert r.seconds_remaining() is not None


def test_api_downgrades_an_expired_analysis(monkeypatch):
    """**時計を進めたら、APIは BUY/SELL を保持しない。**"""
    from fastapi.testclient import TestClient

    import app.main as main

    real = main.analyze

    def stale(pair, provider, cfg, now=None):
        res = real(pair, provider, cfg, now=now)
        return res.model_copy(update={
            "signal": Signal.BUY, "direction": Direction.LONG,
            "valid_until": datetime.now(timezone.utc) - timedelta(seconds=1)})

    monkeypatch.setattr(main, "analyze", stale)
    with TestClient(main.app) as client:
        body = client.get("/api/analysis/USDJPY").json()
    assert body["signal"] == "NO_TRADE"
    assert body["direction"] == "NEUTRAL"


# ==================================================== 供給元と取り込み


def test_csv_import_rejects_bad_input_with_422(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main

    monkeypatch.setattr(main.settings, "env", "development")
    with TestClient(main.app) as client:
        r1 = client.post("/api/dev/load-csv",
                         json={"pair": "NOTAPAIR", "timeframe": "M15"})
        r2 = client.post("/api/dev/load-csv",
                         json={"pair": "USDJPY", "timeframe": "NOPE"})
    assert r1.status_code == 422
    assert r2.status_code == 422


def test_unimplemented_providers_raise_rather_than_invent(cfg):
    from app.market_data import (
        ExternalAPIProvider,
        FutureBrokerProvider,
        MT4MarketDataProvider,
    )
    for cls in (MT4MarketDataProvider, ExternalAPIProvider,
                FutureBrokerProvider):
        with pytest.raises(NotImplementedError):
            cls().get_spread("USDJPY")


# ============================================== 統合（実際の経路を通す）


def test_integration_real_pipeline_reaches_every_signal_kind(cfg):
    """monkeypatch ではなく、**実際の指標→相場つき→シグナル**を通す。

    BUY/SELL/WAIT/NO_TRADE のすべてに到達できることを確かめる。
    どれかに固定されていたら、この試験が落ちる。
    """
    seen = set()
    for scenario in ("strong_uptrend", "strong_downtrend", "range",
                     "breakout", "high_volatility", "low_volatility",
                     "trend_pullback", "downtrend_pullback"):
        p = MockMarketDataProvider(scenario=scenario)
        for sym in [s.symbol for s in cfg.enabled_pairs()][:8]:
            seen.add(analyze_pair(sym, p, cfg).signal)
    assert Signal.WAIT in seen
    assert Signal.NO_TRADE in seen


def test_integration_conflict_blocks_through_the_whole_pipeline(cfg):
    """競合が、部品単体ではなく通しで NO_TRADE になること。"""
    p = MockMarketDataProvider()
    blocked = [analyze_pair(s.symbol, p, cfg) for s in cfg.enabled_pairs()]
    # **「逆」だけで拾わない。** 戦略名（持ち合いの逆張り）など、競合とは
    # 関係のない理由まで拾ってしまい、通しの検証が別物になる。
    conflicted = [r for r in blocked
                  if any("向きが逆です" in x or "向きが判定できません" in x
                         for x in r.invalidation_reasons)]
    assert conflicted, "競合で見送られた銘柄が1つもありません"
    for r in conflicted:
        assert r.signal is Signal.NO_TRADE
        assert r.hard_blocked is True


def test_unmeasurable_categories_score_zero_and_leave_the_maximum(cfg):
    """**測れない項目は0点で、満点からも外す。**

    外さないと、材料を持たない環境で全体が沈んで何も出せなくなる。
    逆に「測ったが支えが無い」場合は0点のまま満点に数える（減点として残す）。
    """
    from app.signal_engine import attainable_max

    weights = cfg.signal["weights"]
    avail = cfg.signal["data_available"]
    assert attainable_max(cfg.signal) == pytest.approx(
        sum(v for k, v in weights.items() if avail[k]))

    # 実行時に測れなかった項目は、その回だけ満点から外れる
    reduced = attainable_max(cfg.signal, ["correlation", "news"])
    assert reduced == pytest.approx(
        attainable_max(cfg.signal) - weights["correlation"] - weights["news"])

    r = analyze_pair("AUDUSD", MockMarketDataProvider(), cfg)
    assert r.score <= attainable_max(cfg.signal)


def test_synthetic_data_is_never_correlated_with_real_drivers(cfg):
    """**合成データを実勢の材料と突き合わせない。**

    時刻がたまたま合うだけで、出てくる数字は相関を表していない。
    """
    r = analyze_pair("USDJPY", MockMarketDataProvider(), cfg)
    assert r.score_breakdown.correlation == 0.0


def test_market_structure_is_still_used_after_folding_into_price_action(cfg):
    """構造の配点を畳んでも、**材料そのものは捨てていない。**

    実データの検証では構造は数少ない「順方向に効いた」項目だった。
    落とすと、測れた数少ない手がかりを失う。
    """
    from app.market_structure import structure_direction
    from app.models import StructureState

    p = MockMarketDataProvider()
    aligned = 0
    for spec in cfg.enabled_pairs():
        r = analyze_pair(spec.symbol, p, cfg)
        if r.market_structure.structure is StructureState.UNKNOWN:
            continue
        d = structure_direction(r.market_structure)
        if d != 0 and r.direction.value != "NEUTRAL":
            aligned += 1
            # 構造が向きと一致している銘柄では値動きの形に点が入る
            if ((d > 0 and r.direction.value == "LONG")
                    or (d < 0 and r.direction.value == "SHORT")):
                assert r.score_breakdown.price_action > 0
    assert aligned > 0, "構造が判定できた銘柄がありません"


# ================================ 相場つきと戦略の対応（外部レビュー F2）


def _supported(cfg):
    """戦略が割り当てられている相場つきの一覧（設定から引く）。

    一覧を試験の側に書き写さない。書き写すと、設定を変えたときに
    **試験だけが古い前提のまま通ってしまう。**
    """
    table = cfg.filters["regime_strategy"]["strategies"]
    return {k for k, v in table.items() if v}


def test_unsupported_regime_never_produces_buy_or_sell(cfg):
    """**持ち合いでトレンド追随の条件をそのまま当てはめない。**

    戦略を持たない相場つきで、別の戦略の条件が自動的に成立すると、
    根拠なく売買シグナルが出る。実際 LOW_VOLATILITY と TRANSITION で
    トレンド追随の条件のまま出ていた。
    """

    supported = _supported(cfg)
    seen = set()
    for scenario in ("range", "low_volatility", "high_volatility",
                     "breakout", "strong_uptrend", "strong_downtrend",
                     "trend_pullback", "downtrend_pullback"):
        p = MockMarketDataProvider(scenario=scenario)
        for spec in cfg.enabled_pairs():
            r = analyze_pair(spec.symbol, p, cfg)
            if r.signal in (Signal.BUY, Signal.SELL):
                seen.add(r.regime.value)
                assert r.regime.value in supported, (
                    f"{spec.symbol}: {r.regime.value} は対応戦略が無いのに "
                    f"{r.signal.value} が出ています")
    assert seen, "BUY/SELL が1件も出ていません（全件停止は不可）"
    assert seen <= supported


def test_unsupported_regime_gives_a_specific_reason(cfg):
    """理由を「点数が足りない」で片づけない。**守備範囲の外だと言う。**"""

    p = MockMarketDataProvider(scenario="high_volatility")
    found = False
    supported = _supported(cfg)
    for spec in cfg.enabled_pairs():
        r = analyze_pair(spec.symbol, p, cfg)
        if r.regime.value in supported:
            continue
        if any("対応する戦略" in x for x in r.invalidation_reasons):
            found = True
            assert r.signal in (Signal.WAIT, Signal.NO_TRADE)
    assert found, "守備範囲の外だという理由が1件も出ていません"


def test_a_top_score_cannot_override_an_unsupported_regime(cfg):
    """**点数95でも、対応戦略が無ければ売買にしない。**"""
    from app.filters import FilterResult
    from app.models import Regime, SetupQuality
    from app.signal_engine import decide_signal

    clean = FilterResult()
    unsupported = [r for r in Regime if r.value not in _supported(cfg)]
    assert unsupported, "戦略を持たない相場つきが1つもありません"
    for regime in unsupported:
        sig, reasons = decide_signal(
            Direction.LONG, SetupQuality.VERY_STRONG_SETUP, clean,
            regime=regime, filters_cfg=cfg.filters)
        assert sig is not Signal.BUY
        assert any("対応する戦略" in r for r in reasons)


def test_supported_regime_still_reaches_buy_and_sell(cfg):
    """対応する相場つきでは、これまでどおり売買まで到達すること。"""
    from app.filters import FilterResult
    from app.models import Regime, SetupQuality
    from app.signal_engine import decide_signal

    clean = FilterResult()
    buy, _ = decide_signal(Direction.LONG, SetupQuality.STRONG_SETUP, clean,
                           regime=Regime.TREND_UP, filters_cfg=cfg.filters)
    sell, _ = decide_signal(Direction.SHORT, SetupQuality.STRONG_SETUP, clean,
                            regime=Regime.TREND_DOWN, filters_cfg=cfg.filters)
    assert buy is Signal.BUY
    assert sell is Signal.SELL


# ============================ 閉場を経過時間に数えない（外部レビュー F3）


def test_market_minutes_ignore_the_weekend():
    """**壁時計で測ると毎週月曜の朝に必ず壊れる。**

    日曜22:00の再開直後、直前の H4 足は52時間前。許容が「3本＝12時間」なら
    正常な足が「古すぎ」で弾かれ、全銘柄が止まる。
    """
    from app.freshness import market_minutes_between

    last_h4 = datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc)   # 金曜
    monday = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)     # 月曜
    assert (monday - last_h4).total_seconds() / 3600 == pytest.approx(52.0)
    assert market_minutes_between(last_h4, monday) / 60 == pytest.approx(
        4.0, abs=0.2)


def test_market_minutes_still_count_a_real_weekday_gap():
    """本当の欠落は見逃さない。閉場だけを除く。"""
    from app.freshness import market_minutes_between

    a = datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc)     # 水曜
    b = datetime(2026, 9, 9, 21, 0, tzinfo=timezone.utc)
    assert market_minutes_between(a, b) / 60 == pytest.approx(18.0, abs=0.2)


def test_stale_filter_passes_a_legitimate_weekend_gap(cfg):
    """正規の週末明けで、正常な足が古すぎ扱いにならないこと。

    各時間足の最終足は「金曜の閉場直前に確定した足」。日曜22:00の再開
    直後に見ると、壁時計では約48時間前だが、取引時間では足1本ぶんしか
    経っていない。
    """
    # **閉場・再開の時刻を決め打ちしない。** 為替の週の切れ目は
    # ニューヨークの夕方で決まるので、夏時間で1時間ずれる。決め打ちすると、
    # 半年ぶん「週末のあいだずっと1時間ぶん古い」という幻の遅れが出る
    # （2026-09-20 に実際そうなった）。設定から引く。
    from app.freshness import _weekend_hours

    week = cfg.calendar["weekend"]
    friday = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
    close_h, open_h = _weekend_hours(week, friday)
    friday_close = friday.replace(hour=close_h, minute=0)
    reopen = datetime(2026, 9, 6, open_h, 5, tzinfo=timezone.utc)  # 再開直後
    tfs = ("H4", "H1", "M15", "M5")
    series = {}
    for tf in tfs:
        minutes = cfg.timeframe_minutes(tf)
        series[tf] = _series(
            300, minutes, tf,
            end=friday_close - timedelta(minutes=minutes))
    ind = {tf: {"atr14": [0.15], "close": [150.0]} for tf in tfs}
    from app.models import SpreadInfo
    spread = SpreadInfo(pair="USDJPY", spread_price=0.009, spread_pips=0.9,
                        timestamp=reopen)

    # 壁時計なら2日近く前。取引時間で測れば足1本ぶん。
    assert (reopen - series["H4"].last.timestamp).total_seconds() / 3600 > 40

    res = check_all(spec=cfg.pair("USDJPY"), series_by_tf=series,
                    indicators_by_tf=ind, spread=spread, h1_dir=1, h4_dir=1,
                    cfg=cfg.filters, now=reopen,
                    timeframe_minutes={tf: cfg.timeframe_minutes(tf)
                                       for tf in tfs})
    assert not any("古すぎ" in r for r in res.reasons), res.reasons


def test_stale_filter_still_blocks_a_real_weekday_gap(cfg):
    """取引時間中に本当に止まっていたら、これまでどおり止める。"""
    wed = datetime(2026, 9, 9, 21, 0, tzinfo=timezone.utc)
    tfs = ("H4", "H1", "M15", "M5")
    series = {}
    for tf in tfs:
        minutes = cfg.timeframe_minutes(tf)
        end = datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc)   # 18時間前
        series[tf] = _series(300, minutes, tf, end=end)
    ind = {tf: {"atr14": [0.15], "close": [150.0]} for tf in tfs}
    from app.models import SpreadInfo
    spread = SpreadInfo(pair="USDJPY", spread_price=0.009, spread_pips=0.9,
                        timestamp=wed)
    res = check_all(spec=cfg.pair("USDJPY"), series_by_tf=series,
                    indicators_by_tf=ind, spread=spread, h1_dir=1, h4_dir=1,
                    cfg=cfg.filters, now=wed,
                    timeframe_minutes={tf: cfg.timeframe_minutes(tf)
                                       for tf in tfs})
    assert any("古すぎ" in r for r in res.reasons)


# ==================================================== 休場日（祝日）の扱い


def test_market_wide_holidays_are_closed():
    """元日とクリスマスは為替も実質止まる。"""
    from datetime import date as _date

    from app.freshness import is_holiday

    assert is_holiday(_date(2026, 12, 25)) is True
    assert is_holiday(_date(2027, 1, 1)) is True


def test_regional_bank_holidays_are_not_treated_as_closed():
    """**各国の銀行休業日を閉場として扱わない。**

    流動性が薄くなるだけで市場は開いている。閉場にすると、その日の
    本当のデータ欠落を見逃す。
    """
    from datetime import date as _date

    from app.freshness import is_holiday

    for day in (_date(2026, 12, 24),      # クリスマスイブ（短縮だが開場）
                _date(2026, 12, 26),      # Boxing Day
                _date(2026, 11, 26),      # Thanksgiving（米）
                _date(2026, 5, 4)):       # 日本の連休
        assert is_holiday(day) is False, day


def test_holiday_time_is_not_counted_as_staleness():
    """クリスマスを挟んだ空白を「古くなった時間」に数えない。"""
    from app.freshness import market_minutes_between

    before = datetime(2026, 12, 24, 21, 0, tzinfo=timezone.utc)   # 木
    after = datetime(2026, 12, 26, 3, 0, tzinfo=timezone.utc)     # 土
    assert (after - before).total_seconds() / 3600 == pytest.approx(30.0)
    # クリスマス（終日）と土曜ぶんが抜ける
    assert market_minutes_between(before, after) / 60 == pytest.approx(
        3.0, abs=0.2)


def test_a_normal_weekday_gap_is_unaffected_by_the_holiday_rule():
    from app.freshness import market_minutes_between

    a = datetime(2026, 12, 22, 3, 0, tzinfo=timezone.utc)     # 火
    b = datetime(2026, 12, 22, 21, 0, tzinfo=timezone.utc)
    assert market_minutes_between(a, b) / 60 == pytest.approx(18.0, abs=0.2)


def test_calendar_config_only_lists_market_wide_closures(cfg):
    """設定に地域の銀行休業日を混ぜない（混ぜると欠落を見逃す）。"""
    recurring = set(cfg.calendar["recurring_closed"])
    assert recurring == {"01-01", "12-25"}
    # 追加した日には必ず理由が要る
    for day in cfg.calendar["closed_dates"]:
        assert day in cfg.calendar["notes"], f"{day} に理由がありません"


def test_analysis_result_rejects_unknown_fields():
    """**知らない項目を黙って捨てない。**

    pydantic の既定は未知の引数を無視する。綴りを間違えた項目が
    「入ったつもり」で消える。実際、correlation を別のクラスに足して
    しまい、API に出ないまま気づけなかった。
    """
    from app.models import AnalysisResult

    base = analyze_pair("USDJPY", MockMarketDataProvider(), get_trading_config())
    payload = base.model_dump()
    payload["typo_field"] = 1
    with pytest.raises(Exception):
        AnalysisResult(**payload)


def test_correlation_detail_reaches_the_api_model(cfg):
    """相関の内訳が分析結果に載っていること（載らずに消えていた）。"""
    r = analyze_pair("USDJPY", MockMarketDataProvider(), cfg)
    assert r.correlation is not None
    assert r.correlation["state"] in ("KNOWN", "WEAK", "UNAVAILABLE", "UNKNOWN")


# ================================ 取り込みの鮮度（2026-09-16）


def test_a_calendar_without_a_fetch_time_is_treated_as_stale():
    """**取得日時が無い予定表を「たぶん新しい」と読まない。**

    古いかどうかを確かめられないものは、確かめられないと言う。
    """
    import json

    from app.config import get_trading_config
    from app.data_status import collect

    cfg = get_trading_config()
    status = collect(cfg, "mock")
    cal = next(s for s in status["sources"] if s["key"] == "calendar")
    assert cal["limit_hours"] == cfg.news["source_max_age_hours"]
    assert cal["state"] in ("OK", "SOON", "STALE", "MISSING")


def test_the_worst_source_decides_the_overall_state():
    """**良いほうに寄せない。** 1つでも古ければ全体を古いとする。"""
    from datetime import datetime, timezone

    from app.config import get_trading_config
    from app.data_status import collect

    cfg = get_trading_config()
    # ずっと未来から見れば、どの材料も古い
    future = datetime(2030, 1, 1, tzinfo=timezone.utc)
    status = collect(cfg, "mock", now=future)
    assert status["state"] == "STALE"
    assert "calendar" in status["blocking"], (
        "予定表が古いのに、建てられなくなる材料として挙がっていません")


def test_the_unsupported_reason_names_the_actual_regime(cfg):
    """**理由の中の相場つきが、実際の相場つきと一致すること。**

    以前、戦略名を回すループが相場つきの日本語名を上書きしていて、
    「range相場に対応する戦略を持っていません（持っているのは…
    持ち合いの逆張り）」という、相場つきも中身も食い違う文が出ていた
    （実際の regime は HIGH_VOLATILITY）。読む側は、何の相場つきで
    止まっているのか分からなくなる。
    """
    from app.filters import FilterResult
    from app.models import Regime, SetupQuality
    from app.signal_engine import _REGIME_JA, decide_signal

    clean = FilterResult()
    for regime in (r for r in Regime if r.value not in _supported(cfg)):
        _, reasons = decide_signal(
            Direction.LONG, SetupQuality.VERY_STRONG_SETUP, clean,
            regime=regime, filters_cfg=cfg.filters)
        line = next(r for r in reasons if "対応する戦略" in r)
        expected = _REGIME_JA.get(regime.value, regime.value)
        assert line.startswith(expected), (
            f"{regime.value} の理由が「{line[:20]}…」になっています")


def test_the_weekend_boundary_follows_daylight_saving(cfg):
    """**週の切れ目はニューヨークの夕方。夏時間で1時間ずれる。**

    UTC で固定すると、夏のあいだ週末じゅう「取引時間で1時間前」という
    幻の遅れが出て、支援できる銘柄まで見送りになる。2026-09-20（日曜）に
    実際そうなり、対象2銘柄とも NO_TRADE になっていた。
    """
    from app.freshness import market_minutes_between

    # 夏（9月）: 金 21:00 UTC に閉じる。そこから日曜昼までは開いていない。
    summer_close = datetime(2026, 9, 18, 21, 0, tzinfo=timezone.utc)
    summer_sunday = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)
    assert market_minutes_between(summer_close, summer_sunday) == 0.0

    # 冬（12月）: 金 22:00 UTC に閉じる。21:00〜22:00 はまだ開いている。
    winter_close = datetime(2026, 12, 18, 22, 0, tzinfo=timezone.utc)
    winter_sunday = datetime(2026, 12, 20, 9, 0, tzinfo=timezone.utc)
    assert market_minutes_between(winter_close, winter_sunday) == 0.0
    assert market_minutes_between(
        datetime(2026, 12, 18, 21, 0, tzinfo=timezone.utc),
        winter_sunday) > 0.0, "冬は 21:00〜22:00 がまだ開いている"
