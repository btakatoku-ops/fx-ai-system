# -*- coding: utf-8 -*-
"""検証の試験。

**ここがいちばん厳しくなければいけない。** 検証の道具が未来を覗いていたら、
出てくる成績は本物より良く見える。それを信じて建てると、実際に負ける。
market-radar で起きたのがまさにそれだった。

だから「未来を見ていない」ことを、性質として確かめる試験を先に置く。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.backtester import (
    HistoryProvider,
    _history_sizes,
    _stable_coin,
    run,
    run_pair,
    simulate_bracket,
)
from app.market_data import MarketDataError, MockMarketDataProvider
from app.models import Candle, CandleSeries
from app.performance import (
    Stats,
    by_band,
    compare,
    monotonicity,
    summarize,
    two_proportion_z,
    two_sided_p,
    wilson,
)


# ------------------------------------------------------------ 補助


def _bar(t: datetime, o: float, h: float, low: float, c: float) -> Candle:
    return Candle(timestamp=t, open=o, high=h, low=low, close=c, volume=100.0)


def _series(pair: str, tf: str, rows) -> CandleSeries:
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [_bar(t0 + timedelta(minutes=15 * i), *r) for i, r in enumerate(rows)]
    return CandleSeries(pair=pair, timeframe=tf, candles=candles)


class _FakeTrade:
    """performance の集計だけを確かめるための最小の入れ物。"""

    def __init__(self, outcome: str, r: float, score: float = 75.0) -> None:
        self.outcome = outcome
        self.r_multiple = r
        self.score = score


# ------------------------------------------------------- 未来を見ていないこと


def test_history_provider_never_returns_bars_after_cutoff():
    rows = [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(50)]
    s = _series("USDJPY", "M15", rows)
    hp = HistoryProvider("USDJPY", {"M15": s}, None)
    cutoff = s.candles[20].timestamp
    hp.set_cutoff(cutoff)

    got = hp.get_candles("USDJPY", "M15", limit=100)
    assert len(got.candles) == 21                    # 0〜20 の21本
    assert got.candles[-1].timestamp == cutoff
    assert all(c.timestamp <= cutoff for c in got.candles)


def test_history_provider_raises_when_nothing_is_visible_yet():
    s = _series("USDJPY", "M15", [(100, 101, 99, 100)] * 10)
    hp = HistoryProvider("USDJPY", {"M15": s}, None)
    hp.set_cutoff(s.candles[0].timestamp - timedelta(days=1))
    with pytest.raises(MarketDataError):
        hp.get_candles("USDJPY", "M15", 10)


def test_analysis_at_a_cutoff_is_unchanged_by_future_bars(cfg):
    """**この試験がいちばん大事。**

    同じ時刻で判断するとき、その先の足を持っているか否かで結果が変われば、
    未来を見ているということ。持っている履歴の長さを変えて、判断が
    1ビットも変わらないことを確かめる。
    """
    mp = MockMarketDataProvider()
    long_hist = {tf: mp.get_candles("USDJPY", tf, 900)
                 for tf in ("H4", "H1", "M15", "M5")}

    # 同じ足を、途中で打ち切った版も作る
    cutoff = long_hist["M15"].candles[-120].timestamp
    short_hist = {
        tf: CandleSeries(pair="USDJPY", timeframe=tf,
                         candles=[c for c in s.candles if c.timestamp <= cutoff])
        for tf, s in long_hist.items()
    }

    from app.analysis import analyze_pair

    a = HistoryProvider("USDJPY", long_hist, None)
    a.set_cutoff(cutoff)
    b = HistoryProvider("USDJPY", short_hist, None)
    b.set_cutoff(cutoff)

    ra = analyze_pair("USDJPY", a, cfg, now=cutoff)
    rb = analyze_pair("USDJPY", b, cfg, now=cutoff)

    assert ra.signal == rb.signal
    assert ra.direction == rb.direction
    assert ra.score == rb.score
    assert ra.regime == rb.regime
    assert ra.score_breakdown.model_dump() == rb.score_breakdown.model_dump()


def test_entry_uses_the_next_bar_open_not_the_decision_close(cfg):
    """建玉は判断した足の**次の**足の始値。終値で建てたら未来を見たことになる。"""
    bt = dict(cfg.backtest)
    bt["history_bars"] = 60
    bt["warmup_bars"] = 255
    bt["step_bars"] = 1
    bt["signals"] = {"trade_on": ["BUY", "SELL"], "observe_on": ["WAIT", "NO_TRADE"]}

    rep = run_pair("USDJPY", MockMarketDataProvider(), cfg, bt)
    recorded = rep.trades + rep.observations
    assert recorded, "記録が1件も出ていません"

    for t in recorded[:20]:
        assert t.entered_at > t.decided_at          # 必ず後ろの足で建てる


def test_history_sizes_cover_the_same_period_for_every_timeframe(cfg):
    """下位足ほど多く要る。同じ本数で揃えると前半で履歴が尽きる。"""
    sizes = _history_sizes(cfg, "M15", 400)
    assert sizes["M5"] > sizes["M15"] > sizes["H1"] > sizes["H4"]
    # M15 を400本ぶん＝6000分。M5 はそれを覆う1200本＋立ち上がり
    assert sizes["M5"] >= 400 * 15 // 5


def test_control_coin_is_stable_across_processes():
    """対照群が実行のたびに変わると、差が出たのか揺れたのか分からない。"""
    assert _stable_coin("USDJPY|2026-01-01T00:00:00") is _stable_coin(
        "USDJPY|2026-01-01T00:00:00")
    keys = [f"USDJPY|{i}" for i in range(200)]
    heads = sum(1 for k in keys if _stable_coin(k))
    assert 70 < heads < 130            # 偏りすぎていない


# ------------------------------------------------------------ 決済の再現


def _flat(n: int, o=100.0, h=100.5, low=99.5, c=100.0):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [_bar(t0 + timedelta(minutes=15 * i), o, h, low, c) for i in range(n)]


def test_bracket_long_target_is_a_win():
    bars = _flat(5)
    bars[2] = _bar(bars[2].timestamp, 100.0, 103.0, 99.8, 102.5)
    r = simulate_bracket(bars, 0, True, entry=100.0, stop=99.0, target=101.5,
                         max_hold=5)
    assert r["outcome"] == "WIN"
    assert r["r"] == pytest.approx(1.5)
    assert r["exit_price"] == 101.5


def test_bracket_long_stop_is_a_loss():
    bars = _flat(5)
    bars[1] = _bar(bars[1].timestamp, 100.0, 100.2, 98.5, 98.7)
    r = simulate_bracket(bars, 0, True, entry=100.0, stop=99.0, target=101.5,
                         max_hold=5)
    assert r["outcome"] == "LOSS"
    assert r["r"] == pytest.approx(-1.0)


def test_bracket_short_is_mirrored():
    bars = _flat(5)
    bars[1] = _bar(bars[1].timestamp, 100.0, 100.2, 98.0, 98.2)
    r = simulate_bracket(bars, 0, False, entry=100.0, stop=101.0, target=98.5,
                         max_hold=5)
    assert r["outcome"] == "WIN"
    assert r["r"] == pytest.approx(1.5)


def test_bar_touching_both_counts_as_a_loss():
    """足の中の順序は分からない。**分からないときは悪いほうに倒す。**

    ここで利確側を採ると、成績は実際より良く出る。検証の道具が自分に
    都合よく数えたら、検証にならない。
    """
    bars = _flat(3)
    bars[0] = _bar(bars[0].timestamp, 100.0, 102.0, 98.0, 100.0)
    r = simulate_bracket(bars, 0, True, entry=100.0, stop=99.0, target=101.5,
                         max_hold=3, ambiguous="stop_first")
    assert r["outcome"] == "LOSS"


def test_timeout_exits_at_the_last_close():
    bars = _flat(10, o=100.0, h=100.3, low=99.7, c=100.2)
    r = simulate_bracket(bars, 0, True, entry=100.0, stop=98.0, target=103.0,
                         max_hold=4)
    assert r["outcome"] == "TIMEOUT"
    assert r["bars"] == 4
    assert r["r"] == pytest.approx((100.2 - 100.0) / 2.0)


def test_bracket_stops_at_the_end_of_available_bars():
    bars = _flat(3)
    r = simulate_bracket(bars, 0, True, entry=100.0, stop=98.0, target=103.0,
                         max_hold=100)
    assert r["bars"] == 3


def test_zero_risk_does_not_divide_by_zero():
    bars = _flat(3)
    r = simulate_bracket(bars, 0, True, entry=100.0, stop=100.0, target=101.0,
                         max_hold=3)
    assert r["outcome"] == "TIMEOUT"
    assert r["r"] == 0.0


# ------------------------------------------------------------ 集計の歯止め


def test_wilson_matches_known_values():
    low, high = wilson(15, 30)
    assert low == pytest.approx(0.3315, abs=1e-3)
    assert high == pytest.approx(0.6685, abs=1e-3)


def test_wilson_never_leaves_zero_one_even_with_no_wins():
    low, high = wilson(0, 10)
    assert low == 0.0
    assert 0 < high < 1               # 幅0にならない


def test_win_rate_is_not_claimed_below_minimum_samples():
    """**件数が足りないときに勝率を出さないのが、この集計の要。**"""
    s = summarize([_FakeTrade("WIN", 1.5)] * 5 + [_FakeTrade("LOSS", -1.0)] * 5,
                  min_samples=30)
    assert s.n == 10
    assert s.win_rate is None
    assert s.reliable is False
    assert "名乗りません" in s.note
    # 区間だけは返す
    assert s.win_rate_low is not None and s.win_rate_high is not None


def test_win_rate_is_claimed_once_there_are_enough():
    s = summarize([_FakeTrade("WIN", 1.5)] * 20 + [_FakeTrade("LOSS", -1.0)] * 20,
                  min_samples=30)
    assert s.reliable is True
    assert s.win_rate == pytest.approx(0.5)


def test_timeouts_are_excluded_from_win_rate_but_kept_in_r():
    s = summarize([_FakeTrade("WIN", 1.5)] * 20 + [_FakeTrade("LOSS", -1.0)] * 20
                  + [_FakeTrade("TIMEOUT", 0.3)] * 10, min_samples=30)
    assert s.timeouts == 10
    assert s.wins + s.losses == 40
    assert s.win_rate == pytest.approx(0.5)
    assert s.total_r == pytest.approx(20 * 1.5 - 20 * 1.0 + 10 * 0.3)


def test_max_drawdown_follows_the_order_of_trades():
    s = summarize([_FakeTrade("WIN", 1.0), _FakeTrade("LOSS", -1.0),
                   _FakeTrade("LOSS", -1.0), _FakeTrade("WIN", 1.0)],
                  min_samples=1)
    assert s.max_drawdown_r == pytest.approx(-2.0)


def test_empty_summary_does_not_pretend():
    s = summarize([], min_samples=30)
    assert s.n == 0 and s.win_rate is None and s.note == "該当なし"


def test_comparison_refuses_when_samples_are_too_few():
    a = summarize([_FakeTrade("WIN", 1.0)] * 3, min_samples=30)
    b = summarize([_FakeTrade("LOSS", -1.0)] * 3, min_samples=30)
    c = compare(a, b)
    assert c.p_value is None
    assert "比較できません" in c.verdict


def test_comparison_says_no_difference_when_there_is_none():
    a = summarize([_FakeTrade("WIN", 1.0)] * 50 + [_FakeTrade("LOSS", -1.0)] * 50,
                  min_samples=30)
    b = summarize([_FakeTrade("WIN", 1.0)] * 50 + [_FakeTrade("LOSS", -1.0)] * 50,
                  min_samples=30)
    c = compare(a, b)
    assert c.win_rate_diff == pytest.approx(0.0)
    assert "区別がつきません" in c.verdict


def test_comparison_reports_when_selection_is_worse():
    worse = summarize([_FakeTrade("WIN", 1.0)] * 20 + [_FakeTrade("LOSS", -1.0)] * 80,
                      min_samples=30)
    better = summarize([_FakeTrade("WIN", 1.0)] * 70 + [_FakeTrade("LOSS", -1.0)] * 30,
                       min_samples=30)
    c = compare(worse, better)
    assert c.win_rate_diff < 0
    assert "低い" in c.verdict


def test_two_proportion_z_and_p_are_consistent():
    z = two_proportion_z(60, 100, 50, 100)
    assert z == pytest.approx(1.4213, abs=1e-3)
    assert two_sided_p(1.96) == pytest.approx(0.05, abs=1e-3)


def test_bands_split_by_score(cfg):
    """点数が区分に正しく振り分けられること。

    区分は到達しうる最大（80点）に合わせた割合で切ってある。
    """
    bands = cfg.backtest["score_bands"]
    top, mid = bands[-1], bands[1]
    trades = ([_FakeTrade("WIN", 1.5, score=top["min"] + 1)] * 3
              + [_FakeTrade("LOSS", -1.0, score=mid["min"] + 1)] * 4)
    got = {b.label: b.n for b in by_band(trades, bands, min_samples=30)}
    assert got[top["label"]] == 3
    assert got[mid["label"]] == 4
    assert got[bands[0]["label"]] == 0


def test_monotonicity_flags_a_broken_ordering():
    good = Stats(label="90-100", n=10, avg_r=0.1)
    bad = Stats(label="60-69", n=10, avg_r=0.9)
    assert "配点を疑う" in monotonicity([bad, good])


# ------------------------------------------------------------ 通しで動くこと


def test_run_produces_a_report_with_warnings_attached(cfg):
    """**警告は結果と同じ入れ物に入っている。** 数字だけ抜き出せないように。"""
    bt = dict(cfg.backtest)
    bt["history_bars"] = 40
    bt["warmup_bars"] = 255
    bt["step_bars"] = 4

    rep = run(["USDJPY"], MockMarketDataProvider(), cfg, bt)
    assert rep.provider == "mock"
    assert rep.decisions > 0
    assert rep.errors == 0
    assert any("合成データ" in w for w in rep.warnings)
    assert any("未完成" in w for w in rep.warnings)
    assert rep.taken is not None and rep.baseline is not None
    assert rep.control is not None
    d = rep.as_dict()
    assert d["warnings"] and d["settings"]["entry_timeframe"] == "M15"


def test_backtest_is_reproducible(cfg):
    """同じ条件なら同じ結果。揺れるなら、差を見ても意味がない。

    終端を固定しないと、走らせるたびに足の時刻がずれ、時間帯の点が変わり、
    結果が少しずつ動く。実際に全銘柄で勝ち数が 2148 と 2129 に割れた。
    """
    bt = dict(cfg.backtest)
    bt["history_bars"] = 30
    bt["warmup_bars"] = 255
    bt["step_bars"] = 5
    anchor = datetime(2026, 6, 1, tzinfo=timezone.utc)

    a = run(["USDJPY"], MockMarketDataProvider(end_time=anchor), cfg, bt)
    b = run(["USDJPY"], MockMarketDataProvider(end_time=anchor), cfg, bt)
    assert a.as_dict()["taken"] == b.as_dict()["taken"]
    assert a.as_dict()["control"] == b.as_dict()["control"]


def test_unknown_pair_is_reported_not_raised(cfg):
    bt = dict(cfg.backtest)
    bt["history_bars"] = 20
    bt["warmup_bars"] = 255
    rep = run(["XXXYYY"], MockMarketDataProvider(), cfg, bt)
    assert rep.pairs == []
    assert any("XXXYYY" in w for w in rep.warnings)


# ------------------------------------------- 銘柄を揃えた比較（composition）


class _P:
    """銘柄つきの最小の建玉。"""

    def __init__(self, pair: str, outcome: str, r: float = 0.0,
                 score: float = 75.0) -> None:
        self.pair, self.outcome, self.r_multiple, self.score = (
            pair, outcome, r, score)


def test_stratified_comparison_survives_a_composition_trap():
    """**まとめて比べると誤る例を、そのまま試験にしてある。**

    A は何もしなくても8割勝てる銘柄、B は2割しか勝てない銘柄。
    選別した組が B に偏っていると、全体の基準と比べただけで
    「選別すると下がる」と出る。銘柄を揃えれば、下がっていない
    （どころか B の中では基準どおり）と分かる。
    """
    from app.performance import stratified_compare

    universe = ([_P("A", "WIN")] * 80 + [_P("A", "LOSS")] * 20
                + [_P("B", "WIN")] * 20 + [_P("B", "LOSS")] * 80)
    # 選別は B ばかり。B の基準どおり2割勝ち
    selected = [_P("B", "WIN")] * 10 + [_P("B", "LOSS")] * 40

    st = stratified_compare(selected, universe, key="pair", min_samples=30)
    assert st.groups == 1
    assert st.expected_rate == pytest.approx(0.2, abs=1e-6)
    assert st.observed_rate == pytest.approx(0.2, abs=1e-6)
    assert "区別がつきません" in st.verdict

    # 一方、銘柄を揃えない比較だと「下がった」と出てしまう
    pooled = compare(summarize(selected, min_samples=30),
                     summarize(universe, min_samples=30))
    assert pooled.win_rate_diff < 0


def test_stratified_comparison_detects_a_real_improvement():
    from app.performance import stratified_compare

    universe = [_P("A", "WIN")] * 50 + [_P("A", "LOSS")] * 50
    selected = [_P("A", "WIN")] * 45 + [_P("A", "LOSS")] * 15
    st = stratified_compare(selected, universe, key="pair", min_samples=30)
    assert st.expected_rate == pytest.approx(0.5)
    assert st.observed_rate == pytest.approx(0.75)
    assert st.p_value is not None and st.p_value < 0.05
    assert "高い" in st.verdict


def test_stratified_comparison_refuses_below_minimum():
    from app.performance import stratified_compare

    universe = [_P("A", "WIN")] * 50 + [_P("A", "LOSS")] * 50
    st = stratified_compare([_P("A", "WIN")] * 5, universe, min_samples=30)
    assert st.p_value is None
    assert "できません" in st.verdict


def test_report_carries_the_stratified_verdict(cfg):
    bt = dict(cfg.backtest)
    bt["history_bars"] = 40
    bt["warmup_bars"] = 255
    bt["step_bars"] = 4
    rep = run(["USDJPY", "EURGBP"], MockMarketDataProvider(), cfg, bt)
    assert rep.stratified is not None
    assert rep.as_dict()["stratified"]["verdict"]


def test_mock_end_time_is_pinnable_and_changes_the_period():
    """終端を固定できること。固定しないと結果が揺れる。"""
    a1 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    a2 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    s1 = MockMarketDataProvider(end_time=a1).get_candles("USDJPY", "M15", 10)
    s2 = MockMarketDataProvider(end_time=a1).get_candles("USDJPY", "M15", 10)
    s3 = MockMarketDataProvider(end_time=a2).get_candles("USDJPY", "M15", 10)

    assert [c.timestamp for c in s1.candles] == [c.timestamp for c in s2.candles]
    # 終端は「その時刻に確定した足」＝ anchor から1本ぶん手前が始まりの足。
    # anchor ちょうどの足を返すと、それはまだ進行中の足になる。
    assert s1.candles[-1].timestamp == a1 - timedelta(minutes=15)
    assert s3.candles[-1].timestamp == a2 - timedelta(minutes=15)
    # 値段の道筋は種で決まるので、終端を変えても同じ
    assert [c.close for c in s1.candles] == [c.close for c in s3.candles]


# ------------------------------------------------ 配点のどの項目が効いているか


class _C:
    """内訳つきの最小の建玉。"""

    def __init__(self, sr: float, outcome: str, r: float) -> None:
        self.breakdown = {"support_resistance": sr}
        self.outcome, self.r_multiple, self.score = outcome, r, 75.0


def test_component_effect_finds_a_component_that_hurts():
    """**逆に効いている項目を見つけられること。**

    合計点だけ見ていると「80点台が弱い」までしか言えず、9項目のどれが
    原因かに辿り着けない。実際、support_resistance が逆に効いていた。
    """
    from app.performance import component_effect, effect_direction

    trades = ([_C(1.0, "WIN", 1.5)] * 30 + [_C(1.0, "LOSS", -1.0)] * 5
              + [_C(6.0, "WIN", 1.5)] * 20 + [_C(6.0, "LOSS", -1.0)] * 15
              + [_C(11.0, "WIN", 1.5)] * 12 + [_C(11.0, "LOSS", -1.0)] * 23
              + [_C(14.0, "WIN", 1.5)] * 5 + [_C(14.0, "LOSS", -1.0)] * 30)

    buckets = component_effect(trades, "support_resistance",
                               [0.0, 3.75, 7.5, 11.25, 15.0], min_samples=30)
    assert [b.n for b in buckets] == [35, 35, 35, 35]
    assert buckets[0].avg_r > buckets[-1].avg_r
    assert "逆に効いています" in effect_direction(buckets)


def test_component_effect_recognises_a_component_that_helps():
    from app.performance import component_effect, effect_direction

    trades = ([_C(1.0, "LOSS", -1.0)] * 30 + [_C(6.0, "LOSS", -1.0)] * 20
              + [_C(6.0, "WIN", 1.5)] * 10
              + [_C(11.0, "WIN", 1.5)] * 20 + [_C(11.0, "LOSS", -1.0)] * 10
              + [_C(14.0, "WIN", 1.5)] * 30)
    buckets = component_effect(trades, "support_resistance",
                               [0.0, 3.75, 7.5, 11.25, 15.0], min_samples=30)
    assert "順方向に効いています" in effect_direction(buckets)


def test_component_effect_ignores_trades_without_that_component():
    from app.performance import component_effect

    class _NoBreakdown:
        breakdown = {}
        outcome, r_multiple, score = "WIN", 1.0, 80.0

    buckets = component_effect([_NoBreakdown()], "support_resistance",
                               [0.0, 7.5, 15.0])
    assert all(b.n == 0 for b in buckets)


def test_effect_direction_refuses_with_too_few():
    from app.performance import effect_direction
    from app.performance import Stats as S

    assert "判断できません" in effect_direction([S(label="a", n=3, avg_r=0.1)])


def test_report_includes_component_effects(cfg):
    bt = dict(cfg.backtest)
    bt["history_bars"] = 60
    bt["warmup_bars"] = 255
    bt["step_bars"] = 2
    rep = run(["USDJPY", "AUDUSD"], MockMarketDataProvider(
        end_time=datetime(2026, 9, 1, tzinfo=timezone.utc)), cfg, bt)
    assert "support_resistance" in rep.component_effects
    assert rep.component_effects["support_resistance"]["direction"]
    assert rep.as_dict()["component_effects"]


# ------------------------------------- 強制条件で弾いた場面を基準に混ぜない


def test_hard_blocked_bars_are_excluded_from_the_baseline(cfg):
    """**基準は「強制条件を通った場面」に限ること。**

    全部の足を基準にすると、費用が見合わない場面まで混ざる。それは必ず
    負けるので基準が自動的にひどい数字になり、何と比べても「選別は有効」に
    見えてしまう。実データで実際にそうなった（16,224回の判断のうち
    15,778回が強制の見送りで、基準の勝率が 14% という有り得ない値になった）。
    """
    bt = dict(cfg.backtest)
    bt["history_bars"] = 60
    bt["warmup_bars"] = 255
    bt["step_bars"] = 2
    rep = run(["USDJPY", "EURGBP", "AUDUSD"], MockMarketDataProvider(
        end_time=datetime(2026, 9, 1, tzinfo=timezone.utc)), cfg, bt)

    assert rep.blocked is not None and rep.all_bars is not None
    # 基準と弾いた分を足すと全部になる（取りこぼしていない）
    assert rep.baseline.n + rep.blocked.n == rep.all_bars.n
    d = rep.as_dict()
    assert d["blocked"] is not None and d["all_bars"] is not None


def test_trades_carry_the_hard_blocked_flag(cfg):
    bt = dict(cfg.backtest)
    bt["history_bars"] = 60
    bt["warmup_bars"] = 255
    bt["step_bars"] = 2
    # 向きが定まる銘柄でないと1件も記録されない（EURGBP は H1/H4 が逆で
    # 常に NEUTRAL になり、記録の対象にならなかった）
    rep = run_pair("USDJPY", MockMarketDataProvider(
        end_time=datetime(2026, 9, 1, tzinfo=timezone.utc)), cfg, bt)
    recorded = rep.trades + rep.observations
    assert recorded
    # 建てたものが強制で弾かれていることはない
    assert all(not t.hard_blocked for t in rep.trades)
    # 印は bool として必ず入っている
    assert all(isinstance(t.hard_blocked, bool) for t in recorded)


def test_analysis_marks_hard_blocks_apart_from_low_score(cfg):
    """強制の見送りと、点数が足りない見送りを取り違えない。"""
    from app.analysis import analyze_pair

    p = MockMarketDataProvider()
    blocked = analyze_pair("EURGBP", p, cfg)      # H1 と H4 の向きが逆
    assert blocked.signal.value == "NO_TRADE"
    assert blocked.hard_blocked is True

    ok = analyze_pair("AUDUSD", p, cfg)
    assert ok.hard_blocked is False


def test_component_effects_exclude_hard_blocked_bars(cfg):
    """項目ごとの効き方も、強制条件を通った場面だけで見ること。

    弾いた場面を混ぜると「費用が見合わないから負けた」が
    「その項目のせいで負けた」に化ける。一度そのまま出してしまった。
    """
    bt = dict(cfg.backtest)
    bt["history_bars"] = 60
    bt["warmup_bars"] = 255
    bt["step_bars"] = 2
    rep = run(["USDJPY", "AUDUSD", "EURJPY"], MockMarketDataProvider(
        end_time=datetime(2026, 9, 1, tzinfo=timezone.utc)), cfg, bt)

    total = sum(b["n"] for b in rep.component_effects["trend"]["buckets"])
    assert total <= rep.baseline.n          # 弾いた分は入っていない
    assert total < rep.all_bars.n or rep.blocked.n == 0


def test_mock_never_returns_an_unclosed_bar():
    """合成の供給元も**確定した足しか返さない**。

    足の時刻は「始まり」を指す規約なので、時刻 T の足が確定するのは T+期間。
    終端を「いま」にすると最後の1本が進行中の足になる。進行中の足を確定として
    扱ったのが market-radar で的中率を29.6%まで落とした原因。
    """
    from app.freshness import check_series

    anchor = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)   # 水曜
    p = MockMarketDataProvider(end_time=anchor)
    for tf, minutes in (("M5", 5), ("M15", 15), ("H1", 60)):
        s = p.get_candles("USDJPY", tf, 300)
        rep = check_series(s, minutes, now=anchor)
        assert not rep.unclosed_last_bar, f"{tf} が未確定の足を返しています"
        assert rep.future_bars == 0


def test_backtest_bands_match_the_attainable_maximum(cfg):
    """検証の区分が、到達しうる最大に収まっていること。

    満点が80点なのに絶対点で 80-89 / 90-100 の区分を置くと、その区分は
    構造的に空になり、区分表が意味を失う。実際その状態を作った。
    """
    from app.signal_engine import attainable_max

    am = attainable_max(cfg.signal)
    bands = cfg.backtest["score_bands"]
    assert bands[-1]["max"] == pytest.approx(am)
    # 区分が隙間なく満点までを覆っている
    assert bands[0]["min"] == 0.0
    for a, b in zip(bands, bands[1:]):
        assert b["min"] > a["max"]
        assert b["min"] - a["max"] < 0.01


# ============ 向きの構成比で誤らない（2026-09-15 に踏みかけた罠）


def _t(direction, outcome):
    """比較に要る項目だけを持った、軽い当て物。"""
    class T:
        pass
    x = T()
    x.direction = direction
    x.outcome = outcome
    x.r_multiple = 1.5 if outcome == "WIN" else -1.0
    x.pair = "USDJPY"
    return x


def test_direction_mix_can_fake_an_edge_and_the_stratified_view_catches_it():
    """**向きを揃えずに比べると、期間の偏りを実力と読み違える。**

    下げ相場では、コイン投げでも SHORT 側だけ成績が良くなる。判断の
    ほとんどが SHORT なら、その良い半分をそのまま受け取るだけで勝率は
    上がる。**戦略が何もしていなくても差が出る。**

    ここでは、向きごとの勝率を対照群とまったく同じにした上で、構成比
    だけを変える。揃えずに比べれば差が出て、揃えれば消えるはずで、
    消えなければ比較の側が壊れている。

    実データで z=+4.50 / p<0.001 と出たものが、向きを揃えると
    p=0.802 になった。その経緯をここに残す。
    """
    from app.performance import stratified_compare, summarize

    # 対照群: SHORT は 46% 勝ち、LONG は 28% 勝ち（下げ相場のコイン投げ）
    control = ([_t("SHORT", "WIN")] * 460 + [_t("SHORT", "LOSS")] * 540
               + [_t("LONG", "WIN")] * 280 + [_t("LONG", "LOSS")] * 720)
    # 戦略: 向きごとの勝率は対照とまったく同じ。**違うのは構成比だけ。**
    strategy = ([_t("SHORT", "WIN")] * 414 + [_t("SHORT", "LOSS")] * 486
                + [_t("LONG", "WIN")] * 28 + [_t("LONG", "LOSS")] * 72)

    # 揃えずに比べると、差が出てしまう
    s_all = summarize(strategy, min_samples=30)
    c_all = summarize(control, min_samples=30)
    assert s_all.win_rate > c_all.win_rate + 0.05, (
        "この当て物では、揃えない比較で差が出ていないと罠にならない")

    # 向きを揃えると消える
    st = stratified_compare(strategy, control, key="direction", min_samples=30)
    assert st.p_value is not None
    assert st.p_value > 0.05, (
        f"向きを揃えたのに差が残っています（p={st.p_value}）。"
        f"比較の側が壊れています")
    assert "区別がつきません" in st.verdict


def test_the_report_carries_the_direction_matched_comparison(cfg):
    """**報告から外して読めないようにする。** 同じ入れ物に入れておく。"""
    from app.backtester import run

    bt = dict(cfg.backtest)
    bt["history_bars"] = 320
    bt["step_bars"] = 8
    rep = run(["USDJPY"], MockMarketDataProvider(
        end_time=datetime(2026, 9, 1, tzinfo=timezone.utc)), cfg, bt)
    assert rep.stratified_direction is not None
    assert rep.stratified_direction.verdict
    assert "stratified_direction" in rep.as_dict()
