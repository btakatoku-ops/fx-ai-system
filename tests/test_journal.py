# -*- coding: utf-8 -*-
"""画面に出た判断の記録と、後からの突き合わせ。

**これが無いと、良くなったかどうかを測れない。** これまで優位性を測れて
いたのは過去データの再現だけで、実際に画面へ出た判断は誰も確かめて
いなかった。

ここで確かめること。

1. **同じ判断を何度も残さない。** 30秒ごとに読み直すので、そのまま書くと
   1日に数万行たまり、中身はほとんど同じになる。
2. **判断が変わったら残す。** まとめすぎて変化を落とさない。
3. **採点に、判断した時刻より後の足しか使わない。** 同じ足を混ぜると、
   結果を知ってから採点することになる。
4. **決着していないものを時間切れにしない。** 足が足りないのは
   「まだ分からない」であって、結果ではない。
5. **記録に失敗しても分析を止めない。** 記録のために判断が出せなく
   なるのは本末転倒。
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import trade_logger
from app.analysis import analyze_pair
from app.market_data import MockMarketDataProvider
from app.models import Candle

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "score_journal", ROOT / "scripts" / "score_journal.py")
assert _spec and _spec.loader
score_journal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score_journal)

UTC = timezone.utc


@pytest.fixture
def url(tmp_path):
    return f"sqlite:///{(tmp_path / 'journal.sqlite3').as_posix()}"


@pytest.fixture
def result(cfg):
    return analyze_pair("USDJPY", MockMarketDataProvider(), cfg)


# ------------------------------------------------ 残し方


def test_the_same_decision_is_not_written_twice(url, result):
    """**30秒ごとに読み直しても、行は増えない。** 回数だけ増える。"""
    first = trade_logger.record(result, None, database_url=url, provider="mock")
    assert first is not None
    for _ in range(5):
        assert trade_logger.record(
            result, None, database_url=url, provider="mock") is None

    rows = trade_logger.recent(url)
    assert len(rows) == 1
    assert rows[0]["seen_count"] == 6
    assert rows[0]["last_seen"] > rows[0]["decided_at"]


def test_a_changed_decision_is_written_as_a_new_row(url, result):
    """まとめすぎて変化を落とさない。"""
    trade_logger.record(result, None, database_url=url, provider="mock")

    # シグナルだけ差し替える（他はそのまま）
    from app.models import Signal

    changed = result.model_copy(update={"signal": Signal.WAIT})
    trade_logger.record(changed, None, database_url=url, provider="mock")

    rows = trade_logger.recent(url)
    if result.signal is Signal.WAIT:
        assert len(rows) == 1, "同じシグナルなら増えないこと"
    else:
        assert len(rows) == 2
        assert {r["signal"] for r in rows} == {
            result.signal.value, Signal.WAIT.value}


def test_each_pair_is_tracked_separately(url, cfg):
    provider = MockMarketDataProvider()
    for sym in ("USDJPY", "EURUSD", "GBPJPY"):
        trade_logger.record(analyze_pair(sym, provider, cfg), None,
                            database_url=url, provider="mock")
    rows = trade_logger.recent(url)
    assert {r["pair"] for r in rows} == {"USDJPY", "EURUSD", "GBPJPY"}


def test_the_summary_does_not_claim_a_win_rate(url, result):
    """**件数だけ出す。** 突き合わせていない段階で勝率を名乗らない。"""
    trade_logger.record(result, None, database_url=url, provider="mock")
    got = trade_logger.summary(url)
    assert got["total"] == 1
    assert got["scored"] == 0
    assert "win_rate" not in got
    assert "実際に建てたかどうかは含みません" in got["note"]


def test_a_broken_database_does_not_stop_the_analysis(result):
    """**記録のために判断が出せなくなるのは本末転倒。**"""
    got = trade_logger.record(
        result, None, database_url="sqlite:///Z:/does/not/exist/x.sqlite3",
        provider="mock")
    assert got is None            # 例外を外へ出さない


def test_what_is_stored_is_what_was_shown(url, result):
    """**あとで作り直さない。** 設定を変えても当時の判断は変わらない。"""
    trade_logger.record(result, None, database_url=url, provider="csv")
    row = trade_logger.recent(url)[0]
    assert row["signal"] == result.signal.value
    assert row["direction"] == result.direction.value
    assert row["regime"] == result.regime.value
    assert row["strategy"] == result.strategy
    assert row["score"] == pytest.approx(result.score)
    assert bool(row["hard_blocked"]) is result.hard_blocked
    assert row["provider"] == "csv"
    assert row["outcome"] is None, "判断した瞬間に結果は分からない"


# ------------------------------------------------ 突き合わせ


def bars(start: datetime, n: int, base: float = 150.0, step: float = 0.0):
    return [
        Candle(timestamp=start + timedelta(minutes=15 * i),
               open=base + step * i, high=base + step * i + 0.05,
               low=base + step * i - 0.05, close=base + step * i, volume=0)
        for i in range(n)
    ]


def row_for(decided: datetime, **over):
    base = {
        "decided_at": decided.isoformat(), "direction": "LONG",
        "entry_low": 150.0, "entry_high": 150.0,
        "stop": 149.5, "target": 150.75,
    }
    base.update(over)
    return base


def test_scoring_never_uses_the_bar_it_decided_on():
    """**判断した足で決着させない。** 結果を知ってから採点することになる。"""
    start = datetime(2026, 9, 1, tzinfo=UTC)
    candles = bars(start, 60)
    decided = candles[10].timestamp          # ちょうどその足の時刻

    got = score_journal.score_one(row_for(decided), candles, 15, 32,
                                  "stop_first")
    assert got is not None
    # 決着は 11本目以降にしか置かれない
    assert got["outcome_at"] > candles[10].timestamp.isoformat()


def test_scoring_waits_until_there_are_enough_bars():
    """**足りないのは「まだ分からない」。時間切れにしない。**"""
    start = datetime(2026, 9, 1, tzinfo=UTC)
    candles = bars(start, 20)                # max_hold 32 に届かない
    decided = candles[0].timestamp
    assert score_journal.score_one(
        row_for(decided), candles, 15, 32, "stop_first") is None


def test_scoring_marks_a_win_when_the_target_is_reached():
    start = datetime(2026, 9, 1, tzinfo=UTC)
    # 0.75 上がれば利確に届く。**32本以内に届く速さにしておく。**
    candles = bars(start, 60, base=150.0, step=0.05)
    decided = candles[0].timestamp
    got = score_journal.score_one(row_for(decided), candles, 15, 32,
                                  "stop_first")
    assert got is not None
    assert got["outcome"] == "WIN"
    assert got["r_multiple"] > 0


def test_scoring_marks_a_loss_when_the_stop_is_hit():
    start = datetime(2026, 9, 1, tzinfo=UTC)
    candles = bars(start, 60, base=150.0, step=-0.05)
    decided = candles[0].timestamp
    got = score_journal.score_one(row_for(decided), candles, 15, 32,
                                  "stop_first")
    assert got is not None
    assert got["outcome"] == "LOSS"
    assert got["r_multiple"] < 0


def test_scoring_skips_rows_without_a_plan():
    """Entry / SL / TP が無いものは採点しない。**埋めない。**"""
    start = datetime(2026, 9, 1, tzinfo=UTC)
    candles = bars(start, 60)
    decided = candles[0].timestamp
    assert score_journal.score_one(
        row_for(decided, stop=None), candles, 15, 32, "stop_first") is None
    assert score_journal.score_one(
        row_for(decided, target=None), candles, 15, 32, "stop_first") is None


def test_a_decision_after_the_last_bar_is_not_scored():
    """足より後に出した判断は、まだ確かめようがない。"""
    start = datetime(2026, 9, 1, tzinfo=UTC)
    candles = bars(start, 60)
    later = candles[-1].timestamp + timedelta(days=1)
    assert score_journal.score_one(
        row_for(later), candles, 15, 32, "stop_first") is None


# ============ 記録用の計画（2026-09-21 に壊れていた）


def test_the_plan_for_a_recorded_decision_is_actually_built(cfg):
    """**BUY/SELL の記録に Entry / SL / TP が入ること。**

    以前ここで build_plan を違う引数で呼んでいて、例外を握り潰していた。
    記録はできるが計画の欄が常に空になり、**採点が一件もできない**状態に
    なっていた。広い except の裏に隠れていて、動いているように見えた。
    """
    from app.models import Direction, Signal

    provider = MockMarketDataProvider()
    base = analyze_pair("USDJPY", provider, cfg)

    # 見送りの場面では作らない（見送りに「もし買うなら」を添えない）
    assert trade_logger.plan_for(base, cfg, provider) is None or \
        base.signal.value in ("BUY", "SELL")

    forced = base.model_copy(update={
        "signal": Signal.BUY, "direction": Direction.LONG})
    plan = trade_logger.plan_for(forced, cfg, provider)
    assert plan is not None, "計画を作れていません（例外を握り潰していないか）"
    assert plan.stop is not None
    assert plan.targets, "利確が空です"


def test_a_usable_plan_is_stored_with_its_levels(url, cfg):
    """計画が成り立っていれば、そのときの損切り・利確を残すこと。

    ここが空だと、**記録はできても一件も採点できない。**
    """
    from app.models import Direction, Signal
    from app.trade_plan import Target, TradePlan

    res = analyze_pair("USDJPY", MockMarketDataProvider(), cfg).model_copy(
        update={"signal": Signal.BUY, "direction": Direction.LONG})
    plan = TradePlan(
        pair="USDJPY", ok=True, direction="LONG", signal="BUY",
        entry_low=150.0, entry_high=150.2, stop=149.5,
        targets=[Target(label="TP1", price=150.95, r_multiple=1.5)])
    trade_logger.record(res, plan, database_url=url, provider="mock")

    row = trade_logger.recent(url)[0]
    assert row["stop"] == pytest.approx(149.5)
    assert row["target"] == pytest.approx(150.95)
    assert row["target_r"] == pytest.approx(1.5)


def test_an_unusable_plan_leaves_the_levels_empty(url, cfg):
    """**成り立っていない計画の数字を、成り立ったように残さない。**

    損益比が下限に届かない場面などでは ok=False になる。そこで損切りだけ
    残すと、採点のときに「出していたことになっている」値が入ってしまう。
    """
    from app.models import Direction, Signal
    from app.trade_plan import Target, TradePlan

    res = analyze_pair("USDJPY", MockMarketDataProvider(), cfg).model_copy(
        update={"signal": Signal.BUY, "direction": Direction.LONG})
    plan = TradePlan(
        pair="USDJPY", ok=False, direction="LONG", signal="BUY",
        entry_low=150.0, entry_high=150.2, stop=149.5,
        targets=[Target(label="TP1", price=150.05, r_multiple=0.1)],
        blocked_reasons=["TP1 までの比が下限に届きません"])
    trade_logger.record(res, plan, database_url=url, provider="mock")

    row = trade_logger.recent(url)[0]
    assert row["stop"] is None
    assert row["target"] is None


def test_what_is_recorded_is_what_the_list_shows(cfg):
    """**利用者が見ていないものを記録しない。**

    一覧は期限切れを NO_TRADE に落としてから並べる。記録へ渡すのを
    その前にしていたので、画面には NO_TRADE と出ているのに、記録と
    朝の確認には WAIT が残っていた。
    """
    from app.pair_ranker import rank_pairs

    seen = []
    res = rank_pairs(MockMarketDataProvider(), cfg,
                     symbols=["USDJPY", "EURUSD"], on_result=seen.append)
    shown = {e.pair: e.signal.value for e in res.entries}
    recorded = {r.pair: r.signal.value for r in seen}
    assert recorded == shown, "記録と一覧が食い違っています"
