# -*- coding: utf-8 -*-
"""取り込みの試験（``scripts/fetch_yahoo.py``）。

**engine にどんな足が入るかは、ここで決まる。** どれだけ engine を固めても、
入口で未確定の足が混ざれば、そこから先の判断はすべて狂う。実際、
取り寄せた直後なのに全26銘柄が「最後の足が未確定」で見送りになっていた。

ここで確かめること。

1. **確定していない足を、末尾から全部落とすこと。** 1本では足りない。
2. **確定した足は落とさないこと。** 安全側に倒しすぎて材料を失わない。
3. **まとめた上位足に、揃っていない区間を作らないこと。**
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "fetch_yahoo", ROOT / "scripts" / "fetch_yahoo.py")
assert _spec and _spec.loader
fetch_yahoo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_yahoo)

drop_incomplete = fetch_yahoo.drop_incomplete
resample = fetch_yahoo.resample


def bars(start: datetime, minutes: int, n: int):
    """等間隔の足を n 本。値は検証に使わないので固定でよい。"""
    return [
        {
            "timestamp": start + timedelta(minutes=minutes * i),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
            "volume": 1.0,
        }
        for i in range(n)
    ]


UTC = timezone.utc


# ------------------------------------------------ 未確定の足を落とす


def test_it_drops_every_unclosed_bar_not_just_one():
    """**1本落とすだけでは足りない。**

    21:03 に取り寄せると、M15 の最後は 21:00（確定は 21:15）。1本落として
    その手前が 20:45 なら確定しているが、Yahoo が先の足まで返してくると
    1本では届かない。実際にこれで全銘柄が止まった。
    """
    rows = bars(datetime(2026, 9, 15, 19, 0, tzinfo=UTC), 15, 10)
    # 19:00 から 15分刻みで 10本 → 最後は 21:15
    now = datetime(2026, 9, 15, 21, 3, tzinfo=UTC)
    kept, dropped = drop_incomplete(rows, 15, now)
    assert dropped == 2, "21:15 と 21:00 の2本とも落とすこと"
    assert kept[-1]["timestamp"] == datetime(2026, 9, 15, 20, 45, tzinfo=UTC)


def test_a_bar_is_closed_only_after_its_period_has_passed():
    """足 t が確定するのは t + 期間。**ちょうどの時刻は確定済み。**"""
    rows = bars(datetime(2026, 9, 15, 20, 0, tzinfo=UTC), 60, 2)  # 20:00, 21:00
    exactly = datetime(2026, 9, 15, 22, 0, tzinfo=UTC)
    kept, dropped = drop_incomplete(rows, 60, exactly)
    assert dropped == 0
    a_second_early = datetime(2026, 9, 15, 21, 59, 59, tzinfo=UTC)
    kept2, dropped2 = drop_incomplete(rows, 60, a_second_early)
    assert dropped2 == 1
    assert kept2[-1]["timestamp"] == datetime(2026, 9, 15, 20, 0, tzinfo=UTC)


def test_it_does_not_throw_away_closed_bars():
    """安全側に倒しすぎない。確定済みの足まで落とすと材料が無くなる。"""
    rows = bars(datetime(2026, 9, 15, 0, 0, tzinfo=UTC), 15, 40)
    now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)      # 十分あと
    kept, dropped = drop_incomplete(rows, 15, now)
    assert dropped == 0
    assert len(kept) == 40


def test_the_weekend_does_not_make_friday_bars_look_unclosed():
    """閉場をまたいでも、確定済みの足は確定済み。"""
    friday = datetime(2026, 9, 11, 20, 45, tzinfo=UTC)
    rows = bars(friday - timedelta(minutes=15 * 9), 15, 10)
    sunday = datetime(2026, 9, 13, 22, 0, tzinfo=UTC)
    kept, dropped = drop_incomplete(rows, 15, sunday)
    assert dropped == 0
    assert kept[-1]["timestamp"] == friday


def test_everything_unclosed_leaves_nothing_rather_than_guessing():
    """全部が未確定なら空で返す。**混ぜて返さない。**"""
    rows = bars(datetime(2026, 9, 15, 21, 0, tzinfo=UTC), 60, 3)
    now = datetime(2026, 9, 15, 21, 30, tzinfo=UTC)
    kept, dropped = drop_incomplete(rows, 60, now)
    assert kept == []
    assert dropped == 3


def test_an_empty_input_is_not_an_error():
    assert drop_incomplete([], 15, datetime(2026, 9, 15, tzinfo=UTC)) == ([], 0)


# ------------------------------------------------ まとめた上位足


def test_resample_does_not_build_a_half_finished_higher_bar():
    """**揃っていない区間で上位足を作らない。**

    H1 を4本まとめて H4 にする。3本しか無い区間で作ると、その H4 足の
    高値・安値・終値は途中までの値になる。未確定の足を確定として扱うのと
    同じことになる。
    """
    rows = bars(datetime(2026, 9, 15, 12, 0, tzinfo=UTC), 60, 6)
    # 12:00〜17:00 → 12-15 の4本は揃う、16-17 の2本は足りない
    out = resample(rows, 4, 60)
    assert [r["timestamp"] for r in out] == [
        datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    ]


def test_resample_aligns_to_midnight_not_to_the_first_bar():
    """境目は 00:00 UTC 起点。**取り寄せた時刻で切れ目が変わらない。**"""
    a = resample(bars(datetime(2026, 9, 15, 0, 0, tzinfo=UTC), 60, 8), 4, 60)
    b = resample(bars(datetime(2026, 9, 15, 1, 0, tzinfo=UTC), 60, 8), 4, 60)
    assert a[0]["timestamp"] == datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    # 1時始まりでも、最初に揃うのは 4:00 の区間（0時起点のまま）
    assert b[0]["timestamp"] == datetime(2026, 9, 15, 4, 0, tzinfo=UTC)


def test_resample_keeps_the_high_and_low_of_the_whole_group():
    rows = bars(datetime(2026, 9, 15, 0, 0, tzinfo=UTC), 60, 4)
    rows[1]["high"] = 120.0
    rows[2]["low"] = 80.0
    rows[0]["open"] = 95.0
    rows[3]["close"] = 110.0
    out = resample(rows, 4, 60)
    assert out[0]["high"] == 120.0
    assert out[0]["low"] == 80.0
    assert out[0]["open"] == 95.0
    assert out[0]["close"] == 110.0


# ------------------------------------------------ 通しで


def test_fetched_bars_would_pass_the_engine_unclosed_check():
    """**取り込んだ足が、engine の「未確定の足」検査を通ること。**

    ここが通らないと、取り寄せた直後に全銘柄が見送りになる。実際に
    そうなっていたので、通しで固定しておく。
    """
    from app.market_data import CandleSeries
    from app.models import Candle

    now = datetime(2026, 9, 15, 21, 3, tzinfo=UTC)
    rows = bars(datetime(2026, 9, 15, 15, 0, tzinfo=UTC), 15, 30)
    kept, _ = drop_incomplete(rows, 15, now)
    assert kept, "落としすぎて空になっている"

    series = CandleSeries(
        pair="USDJPY", timeframe="M15",
        candles=[Candle(timestamp=r["timestamp"], open=r["open"],
                        high=r["high"], low=r["low"], close=r["close"],
                        volume=r["volume"]) for r in kept])
    last = series.candles[-1]
    assert last.timestamp + timedelta(minutes=15) <= now, (
        "最後の足がまだ確定していない")


# ------------------------------------------------ 粗い足の穴を埋める


def test_a_hole_in_the_hourly_feed_is_filled_from_the_finer_one():
    """**1時間足だけ欠けているなら、15分足からまとめ直して埋める。**

    Yahoo の1時間足は数時間ぶんまとめて欠けることがある。そのままだと
    取り込んだ直後に「古すぎ」で全銘柄が止まる。
    """
    fill_gaps = fetch_yahoo.fill_gaps
    base = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    # 15分足は切れ目なく12時間ぶん
    fine = bars(base, 15, 48)
    # 1時間足は 15:00〜17:00 が抜けている
    coarse = [r for r in bars(base, 60, 12)
              if r["timestamp"].hour not in (15, 16, 17)]
    assert len(coarse) == 9

    merged, added = fill_gaps(coarse, fine, 4, 15)
    hours = {r["timestamp"].hour for r in merged}
    assert {15, 16, 17} <= hours, "穴が埋まっていません"
    assert added == 3


def test_it_does_not_invent_a_bar_when_the_finer_feed_is_missing_too():
    """**両方に穴があるなら、埋めない。** 作り話をしない。

    2026-09-23 に実際に起きた形。M5・M15・H1 のどれも同じ時間帯が
    丸ごと無かった。細かいほうから埋めようとしても、材料が無い。
    """
    fill_gaps = fetch_yahoo.fill_gaps
    base = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    fine = [r for r in bars(base, 15, 48)
            if r["timestamp"].hour not in (15, 16, 17)]
    coarse = [r for r in bars(base, 60, 12)
              if r["timestamp"].hour not in (15, 16, 17)]

    merged, added = fill_gaps(coarse, fine, 4, 15)
    hours = {r["timestamp"].hour for r in merged}
    assert not ({15, 16, 17} & hours), "材料が無いのに足を作っています"
    assert added == 0


def test_filling_does_not_touch_history_older_than_the_finer_feed():
    """細かい足が届いていない範囲は、粗い足のまま残す。"""
    fill_gaps = fetch_yahoo.fill_gaps
    old = bars(datetime(2026, 1, 1, tzinfo=UTC), 60, 24)
    fine = bars(datetime(2026, 9, 23, 12, 0, tzinfo=UTC), 15, 16)

    merged, _ = fill_gaps(old, fine, 4, 15)
    assert merged[0]["timestamp"] == old[0]["timestamp"]
    assert len([r for r in merged
                if r["timestamp"].year == 2026
                and r["timestamp"].month == 1]) == 24


# ------------------------------------------------ 最後の気配を足にしない


def test_a_live_quote_row_is_not_kept_as_a_bar():
    """**時刻が揃っていない行は足ではない。**

    Yahoo は末尾に「いまの気配」を付けてくる（``22:59:00`` など）。週末に
    取り込むと、未確定の判定では「もう終わった時刻」に見えて残ってしまう。
    実際に78ファイルに60行紛れ込み、前日の高値と安値が同じ値になった。
    """
    drop_misaligned = fetch_yahoo.drop_misaligned
    rows = bars(datetime(2026, 9, 25, 18, 0, tzinfo=UTC), 60, 4)   # 18〜21時
    rows.append({"timestamp": datetime(2026, 9, 25, 22, 59, tzinfo=UTC),
                 "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0})
    rows.append({"timestamp": datetime(2026, 9, 25, 23, 43, 40, tzinfo=UTC),
                 "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0})
    kept, dropped = drop_misaligned(rows, 60)
    assert dropped == 2
    assert [r["timestamp"].hour for r in kept] == [18, 19, 20, 21]


def test_aligned_bars_are_all_kept():
    drop_misaligned = fetch_yahoo.drop_misaligned
    for minutes in (5, 15, 60):
        rows = bars(datetime(2026, 9, 25, 0, 0, tzinfo=UTC), minutes, 30)
        kept, dropped = drop_misaligned(rows, minutes)
        assert dropped == 0 and len(kept) == 30


def test_recent_mode_appends_without_losing_history(tmp_path):
    """--recent は直近だけ取って継ぎ足す。**古い足を消さない。同じ時刻は新しい方。**"""
    fy = fetch_yahoo
    t0 = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)
    row = lambda i, c: {"timestamp": t0 + timedelta(minutes=5 * i), "open": c,  # noqa: E731
                        "high": c, "low": c, "close": c, "volume": 0}
    path = tmp_path / "X_M5.csv"
    fy.write_csv(path, [row(i, 1.0) for i in range(10)])
    old = fy.read_csv(path)
    merged = fy.merge_rows(old, [row(9, 2.0), row(10, 3.0)])
    assert len(merged) == 11
    assert merged[0]["close"] == 1.0                 # 古い足は残る
    assert merged[9]["close"] == 2.0                 # 同じ時刻は新しく取った値
    assert merged[-1]["timestamp"] == t0 + timedelta(minutes=50)
    assert not (tmp_path / "X_M5.csv.tmp").exists()  # 一時ファイルを残さない
