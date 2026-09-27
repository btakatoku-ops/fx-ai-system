# -*- coding: utf-8 -*-
"""経済指標（配点10点 ＋ 強制条件）の試験。

**停止は強制条件、余裕は点数。** 分けていることを確かめる。
点数で表すと、他の項目が高ければ上書きできてしまう。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models import NewsState
from app.news import clear_cache, evaluate, load_calendar

EVENT_AT = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)


def _cfg(**over):
    base = {
        "coverage_margin_hours": 24,
        "source_max_age_hours": 36,
        "blackout": {"High": {"before_minutes": 30, "after_minutes": 15},
                     "Medium": {"before_minutes": 10, "after_minutes": 10}},
        "comfortable_minutes": 240,
        "relevant_impacts": ["High", "Medium"],
    }
    base.update(over)
    return base


def _calendar(tmp_path, events, fetched=None, name="c.json"):
    path = tmp_path / name
    path.write_text(json.dumps({
        "source": "test",
        "fetched_at": (fetched or EVENT_AT - timedelta(hours=2)).isoformat(),
        "events": events,
    }), encoding="utf-8")
    clear_cache()
    return str(path)


def _ev(currency="GBP", impact="High", at=None, title="Test Event"):
    return {"title": title, "currency": currency, "impact": impact,
            "at": (at or EVENT_AT).isoformat()}


# ======================================================== 停止（強制条件）


@pytest.mark.parametrize("offset,expect_blocked", [
    (-45, False), (-31, False), (-29, True), (-1, True),
    (0, True), (14, True), (16, False), (60, False),
])
def test_blackout_window_around_a_high_impact_event(tmp_path, cfg, offset,
                                                    expect_blocked):
    """High 指標の30分前〜15分後は建てない。"""
    path = _calendar(tmp_path, [_ev()])
    v = evaluate(cfg.pair("GBPJPY"), EVENT_AT + timedelta(minutes=offset),
                 _cfg(), path=path)
    assert v.blocked is expect_blocked, f"{offset}分"
    if expect_blocked:
        assert v.state is NewsState.ACTIVE


def test_an_unrelated_currency_does_not_block(tmp_path, cfg):
    """GBP の指標で AUDNZD を止めない。"""
    path = _calendar(tmp_path, [_ev(currency="GBP")])
    v = evaluate(cfg.pair("AUDNZD"), EVENT_AT, _cfg(), path=path)
    assert v.blocked is False
    assert v.state is NewsState.QUIET


def test_an_unreadable_currency_blocks_every_pair(tmp_path, cfg):
    """**通貨が読めない指標を「関係なし」として捨てない。**

    捨てると、止めるべき場面を見落とす。
    """
    path = _calendar(tmp_path, [_ev(currency=None)])
    for sym in ("AUDNZD", "USDJPY", "EURGBP"):
        clear_cache()
        v = evaluate(cfg.pair(sym), EVENT_AT, _cfg(), path=path)
        assert v.blocked is True, sym


def test_low_impact_events_do_not_block(tmp_path, cfg):
    """Low まで止めると、ほぼ常時停止になる。"""
    path = _calendar(tmp_path, [_ev(impact="Low")])
    v = evaluate(cfg.pair("GBPJPY"), EVENT_AT, _cfg(), path=path)
    assert v.blocked is False


def test_medium_impact_has_a_narrower_window(tmp_path, cfg):
    path = _calendar(tmp_path, [_ev(impact="Medium")])
    inside = evaluate(cfg.pair("GBPJPY"), EVENT_AT - timedelta(minutes=5),
                      _cfg(), path=path)
    assert inside.blocked is True
    clear_cache()
    outside = evaluate(cfg.pair("GBPJPY"), EVENT_AT - timedelta(minutes=20),
                       _cfg(), path=path)
    assert outside.blocked is False


# ======================================================== 予定表の鮮度


def test_a_stale_calendar_blocks(tmp_path, cfg):
    """**古い予定表で「指標なし」と言わない。** それがいちばん危ない。

    収録範囲には入っているが、取り込みが古い場合。
    """
    path = _calendar(tmp_path,
                     [_ev(at=EVENT_AT - timedelta(hours=6)),
                      _ev(at=EVENT_AT + timedelta(hours=6))],
                     fetched=EVENT_AT - timedelta(hours=100))
    v = evaluate(cfg.pair("GBPJPY"), EVENT_AT, _cfg(), path=path)
    assert v.blocked is True
    assert v.state is NewsState.UNKNOWN
    assert any("古すぎ" in r for r in v.reasons)


def test_a_calendar_without_a_fetch_time_blocks(tmp_path, cfg):
    path = tmp_path / "nofetch.json"
    path.write_text(json.dumps({"source": "test", "events": [_ev()]}),
                    encoding="utf-8")
    clear_cache()
    v = evaluate(cfg.pair("GBPJPY"), EVENT_AT, _cfg(), path=str(path))
    assert v.blocked is True
    assert v.state is NewsState.UNKNOWN


def test_a_missing_calendar_is_unavailable_not_blocking(tmp_path, cfg):
    """予定表がそもそも無いのは「確かめようがない」。

    ここで止めると、取り込む前は何も出せなくなる。配点から外す扱いにする。
    """
    clear_cache()
    v = evaluate(cfg.pair("GBPJPY"), EVENT_AT, _cfg(),
                 path=str(tmp_path / "nope.json"))
    assert v.available is False
    assert v.blocked is False
    assert v.ratio == 0.0


# ======================================================== 余裕（点数）


def test_score_ratio_grows_with_time_until_the_next_event(tmp_path, cfg):
    """近いほど低く、離れるほど高い。"""
    path = _calendar(tmp_path, [_ev(at=EVENT_AT)],
                     fetched=EVENT_AT - timedelta(hours=6))
    seen = []
    for hours in (1, 2, 4, 8):
        clear_cache()
        v = evaluate(cfg.pair("GBPJPY"), EVENT_AT - timedelta(hours=hours),
                     _cfg(), path=path)
        seen.append(v.ratio)
    assert seen == sorted(seen)
    assert seen[0] < 1.0
    assert seen[-1] == pytest.approx(1.0)


def test_a_distant_upcoming_event_is_quiet_and_full_score(tmp_path, cfg):
    """次の指標が十分に先なら満点。

    **「最後のイベントが過去だから静か」とは判定しない。** 予定表が
    そこで終わっているだけかもしれない。その場合は収録範囲の外として
    「判断できない」を返す（別の試験で確認）。
    """
    path = _calendar(tmp_path,
                     [_ev(at=EVENT_AT - timedelta(hours=6)),
                      _ev(at=EVENT_AT + timedelta(hours=12))],
                     fetched=EVENT_AT - timedelta(hours=1))
    v = evaluate(cfg.pair("GBPJPY"), EVENT_AT, _cfg(), path=path)
    assert v.state is NewsState.QUIET
    assert v.ratio == pytest.approx(1.0)      # 12時間先 > comfortable 4時間
    assert v.blocked is False


def test_a_calendar_that_only_has_past_events_is_unavailable(tmp_path, cfg):
    """**予定表が過去で終わっていたら「静か」と言わない。**

    そこで終わっているだけで、その先に何があるかは分からない。
    """
    path = _calendar(tmp_path, [_ev(at=EVENT_AT - timedelta(days=5))],
                     fetched=EVENT_AT - timedelta(hours=1))
    v = evaluate(cfg.pair("GBPJPY"), EVENT_AT, _cfg(), path=path)
    assert v.available is False
    assert v.ratio == 0.0


# ======================================================== 読み込み


def test_broken_events_are_skipped_not_guessed(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({
        "source": "t", "fetched_at": EVENT_AT.isoformat(),
        "events": [_ev(), {"title": "no date"}, {"at": "not-a-date"}],
    }), encoding="utf-8")
    clear_cache()
    _f, _s, events = load_calendar(str(path))
    assert len(events) == 1


def test_a_corrupt_file_does_not_raise(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{ not json", encoding="utf-8")
    clear_cache()
    fetched, source, events = load_calendar(str(path))
    assert events == ()


# ==================================================== 収録範囲の外


def test_a_time_outside_the_calendar_range_is_unavailable(tmp_path, cfg):
    """**範囲外を「指標なし」と読まない。**

    予定表が1週間しか収録していないのに数か月前の時刻を渡すと、
    「次の指標まで1500時間」＝満点、という架空の加点になる。
    過去を検証すると全バーに10点が乗り、比較が丸ごと壊れる。
    """
    path = _calendar(tmp_path, [_ev(at=EVENT_AT)],
                     fetched=EVENT_AT - timedelta(hours=2))
    before = evaluate(cfg.pair("GBPJPY"), EVENT_AT - timedelta(days=60),
                      _cfg(), path=path)
    assert before.available is False
    assert before.ratio == 0.0
    assert before.state is NewsState.UNKNOWN
    assert any("含んでいません" in r for r in before.reasons)

    clear_cache()
    after = evaluate(cfg.pair("GBPJPY"), EVENT_AT + timedelta(days=60),
                     _cfg(), path=path)
    assert after.available is False
    assert after.ratio == 0.0


def test_a_time_inside_the_range_is_still_evaluated(tmp_path, cfg):
    """範囲内なら、これまでどおり判定する。"""
    path = _calendar(tmp_path,
                     [_ev(at=EVENT_AT - timedelta(hours=6)),
                      _ev(at=EVENT_AT + timedelta(hours=6))],
                     fetched=EVENT_AT - timedelta(hours=1))
    v = evaluate(cfg.pair("GBPJPY"), EVENT_AT, _cfg(), path=path)
    assert v.available is True
    assert v.state in (NewsState.QUIET, NewsState.UPCOMING)


def test_the_covered_range_is_reported(tmp_path, cfg):
    """どこまで収録しているかを結果に持たせる（後から確かめられるように）。"""
    path = _calendar(tmp_path, [_ev(at=EVENT_AT)],
                     fetched=EVENT_AT - timedelta(hours=1))
    v = evaluate(cfg.pair("GBPJPY"), EVENT_AT, _cfg(), path=path)
    assert v.covers_from and v.covers_to
