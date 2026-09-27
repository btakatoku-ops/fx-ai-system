# -*- coding: utf-8 -*-
"""日計り（その日のうちに閉じる）の決まり。

ここで確かめること。

1. **刻限は夏時間で1時間ずれる。** 固定すると半年ぶん間違える。
2. **刻限まで時間が残っていない場面では建てない。** 届かないと分かって
   いる賭けを、費用を払って始めない。
3. **本数ではなく時刻で区切る。** 足が欠けていると、32本ぶんが32×15分
   より長い時間に広がり、刻限を越える。実測で48件中3件が越えていた。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import day_trade as dt
from app.backtester import simulate_bracket
from app.models import Candle

UTC = timezone.utc
JST = timezone(timedelta(hours=9))


def conf(**over):
    base = {
        "enabled": True,
        "force_exit_jst": "07:00",
        "force_exit_jst_dst": "06:00",
        "min_minutes_to_exit": 90,
        "entry_from_jst": None,
        "entry_until_jst": None,
    }
    base.update(over)
    return {"day_trade": base}


# ------------------------------------------------ 刻限


def test_the_deadline_moves_with_daylight_saving():
    """**ニューヨークの夕方が基準。** 夏時間で1時間ずれる。"""
    summer = datetime(2026, 9, 22, 5, 0, tzinfo=UTC)      # 14:00 JST
    winter = datetime(2026, 12, 22, 5, 0, tzinfo=UTC)
    assert dt.exit_deadline(conf(), summer).astimezone(JST).hour == 6
    assert dt.exit_deadline(conf(), winter).astimezone(JST).hour == 7


def test_the_deadline_is_always_in_the_future():
    """刻限を過ぎていたら、次の日の刻限を見る。"""
    for hour in range(0, 24):
        now = datetime(2026, 9, 22, hour, 0, tzinfo=UTC)
        assert dt.exit_deadline(conf(), now) > now


def test_disabled_means_no_deadline_at_all():
    """**使わない設定なら、勝手に縛らない。**"""
    now = datetime(2026, 9, 22, 5, 0, tzinfo=UTC)
    off = conf(enabled=False)
    assert dt.exit_deadline(off, now) is None
    assert dt.minutes_left(off, now) is None
    assert dt.can_open(off, now) == (True, None)
    assert dt.max_hold_bars(off, now, 15, 32) == 32


# ------------------------------------------------ 建てるかどうか


def test_it_refuses_to_open_near_the_deadline():
    """**届かないと分かっている賭けを始めない。**"""
    # 05:00 JST（刻限 06:00 の1時間前。90分に足りない）
    late = datetime(2026, 9, 21, 20, 0, tzinfo=UTC)
    ok, why = dt.can_open(conf(), late)
    assert ok is False
    assert "刻限" in why and "持ち越さない" in why


def test_it_opens_when_there_is_room():
    midday = datetime(2026, 9, 22, 5, 0, tzinfo=UTC)      # 14:00 JST
    ok, why = dt.can_open(conf(), midday)
    assert ok is True and why is None


def test_an_entry_window_can_narrow_the_day():
    """時間帯を絞る設定（流動性の薄い時間を避けたいとき）。"""
    c = conf(entry_from_jst="08:00", entry_until_jst="23:00")
    inside = datetime(2026, 9, 22, 5, 0, tzinfo=UTC)      # 14:00 JST
    outside = datetime(2026, 9, 21, 22, 0, tzinfo=UTC)    # 07:00 JST
    assert dt.can_open(c, inside)[0] is True
    ok, why = dt.can_open(c, outside)
    assert ok is False
    assert "時間帯" in why


# ------------------------------------------------ 保有の打ち切り


def bars(start: datetime, minutes: int, n: int, gap_after: int = -1,
         gap_minutes: int = 0):
    """足を並べる。``gap_after`` 本目のあとに穴を開けられる。"""
    out = []
    t = start
    for i in range(n):
        out.append(Candle(timestamp=t, open=150.0, high=150.02,
                          low=149.98, close=150.0, volume=0))
        t += timedelta(minutes=minutes)
        if i == gap_after:
            t += timedelta(minutes=gap_minutes)
    return out


def test_holding_is_cut_by_the_clock_not_the_bar_count():
    """**本数で区切るだけでは足りない。**

    足が欠けていると、32本ぶんが32×15分より長い時間に広がる。
    週末や配信元の穴（19:25〜23:00 が丸ごと無い日が実際にあった）を
    跨ぐと、刻限を越えた玉ができる。実測で48件中3件が越えていた。
    """
    start = datetime(2026, 9, 22, 0, 0, tzinfo=UTC)
    # 5本目のあとに6時間の穴を開ける
    candles = bars(start, 15, 40, gap_after=4, gap_minutes=360)
    deadline = start + timedelta(hours=2)

    got = simulate_bracket(candles, 0, True, 150.0, 149.0, 151.0,
                           max_hold=32, deadline=deadline)
    assert candles[got["index"]].timestamp < deadline, (
        "刻限を越えた足で決着しています")


def test_without_a_deadline_the_bar_count_still_applies():
    """刻限を渡さなければ、これまでどおり本数で切る。"""
    start = datetime(2026, 9, 22, 0, 0, tzinfo=UTC)
    candles = bars(start, 15, 40)
    got = simulate_bracket(candles, 0, True, 150.0, 149.0, 151.0,
                           max_hold=10)
    assert got["bars"] == 10


def test_the_hold_never_becomes_zero():
    """刻限が目前でも、最低1本は見る（0本だと計算が壊れる）。"""
    late = datetime(2026, 9, 21, 20, 55, tzinfo=UTC)
    assert dt.max_hold_bars(conf(), late, 15, 32) >= 1


# ------------------------------------------------ 01:00 に閉じる（ブリーフに合わせた設定）


def one_am():
    return conf(force_exit_jst="01:00", force_exit_jst_dst="01:00")


def test_the_one_am_deadline_is_tonight():
    """朝8:30に見たボードの刻限は、今夜（翌日）の 01:00。"""
    morning = datetime(2026, 9, 21, 23, 30, tzinfo=UTC)   # 9/22 08:30 JST
    got = dt.exit_deadline(one_am(), morning).astimezone(JST)
    assert (got.month, got.day, got.hour) == (9, 23, 1)


def test_after_one_am_nothing_opens_until_the_rollover():
    """**01:00 を過ぎたら、日替わりまで建てない。**

    刻限を「次の 01:00」にすると、02:00 に建てた玉が翌日の 01:00 まで
    持てることになり、日替わり（06:00）を跨いでスワップが付く。
    """
    late = datetime(2026, 9, 22, 17, 0, tzinfo=UTC)        # 9/23 02:00 JST
    dl = dt.exit_deadline(one_am(), late)
    assert dl < late                                         # もう過ぎている
    ok, why = dt.can_open(one_am(), late)
    assert ok is False and "過ぎています" in why and "持ち越さない" in why


def test_the_next_trading_day_opens_again_after_the_rollover():
    after = datetime(2026, 9, 22, 21, 30, tzinfo=UTC)       # 9/23 06:30 JST（夏）
    assert dt.can_open(one_am(), after)[0] is True
    assert dt.exit_deadline(one_am(), after).astimezone(JST).day == 24


def test_no_setting_ever_crosses_the_rollover():
    """**どう設定しても、刻限は次の日替わりより前。**"""
    for hhmm in ("01:00", "06:00", "07:00", "12:00", "23:30"):
        c = conf(force_exit_jst=hhmm, force_exit_jst_dst=hhmm)
        for hour in range(0, 24):
            for day in (22, 23):
                now = datetime(2026, 12 if hhmm == "07:00" else 9, day, hour, 10,
                               tzinfo=UTC)
                start = dt.rollover_start(now)
                assert start <= now < start + timedelta(days=1)
                assert dt.exit_deadline(c, now) <= start + timedelta(days=1)


def test_backtests_stop_at_one_am():
    """**検証でも 01:00 で切る。** 実際には持てない時間まで持った成績を測らない。"""
    morning = datetime(2026, 9, 21, 23, 30, tzinfo=UTC)
    assert dt.max_hold_bars(one_am(), morning, 15, 999) == int(
        (dt.exit_deadline(one_am(), morning) - morning).total_seconds() // 900)
