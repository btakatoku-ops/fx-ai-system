# -*- coding: utf-8 -*-
"""時間帯ごとのボードと、週のまとめ。

1. **ロンドンとニューヨークの始まりは、それぞれの夏時間の規則で決める。**
   英国と米国は切り替わる日が違う。
2. **採点の区間は重ねない。** 東京→ロンドン→ニューヨーク→刻限。
3. **1銘柄・1日・1時間帯に1枚。** 東京の1枚と、ロンドンの1枚は別に残る。
4. **時間帯の列が無い古い表は、中身を消さずに作り直す。**
5. **週は締まってからまとめる。** 平日に走らせたら先週。
"""
from __future__ import annotations

import importlib.util
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import board_log, sessions
from app.board import fx_day_start
from app.models import Candle

UTC = timezone.utc
JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]


def _jst(*a) -> datetime:
    return datetime(*a, tzinfo=JST).astimezone(UTC)


# ------------------------------------------------ 始まりの時刻


def test_london_and_new_york_open_in_summer_and_winter():
    summer = fx_day_start(_jst(2026, 10, 2, 9, 0))
    assert sessions.open_at("london", summer) == _jst(2026, 10, 2, 16, 0)
    assert sessions.open_at("newyork", summer) == _jst(2026, 10, 2, 21, 30)
    winter = fx_day_start(_jst(2026, 12, 15, 9, 0))
    assert sessions.open_at("london", winter) == _jst(2026, 12, 15, 17, 0)
    assert sessions.open_at("newyork", winter) == _jst(2026, 12, 15, 22, 30)


def test_uk_and_us_summer_time_change_on_different_days():
    """**10/26〜10/30 は英国だけ冬時間。** ロンドンは 17:00、ニューヨークは 21:30。"""
    day = fx_day_start(_jst(2026, 10, 27, 9, 0))
    assert sessions.open_at("london", day) == _jst(2026, 10, 27, 17, 0)
    assert sessions.open_at("newyork", day) == _jst(2026, 10, 27, 21, 30)


def test_current_session_is_only_the_first_hour():
    assert sessions.current(_jst(2026, 10, 2, 15, 59)) is None
    assert sessions.current(_jst(2026, 10, 2, 16, 0)) == "london"
    assert sessions.current(_jst(2026, 10, 2, 16, 59)) == "london"
    assert sessions.current(_jst(2026, 10, 2, 17, 0)) is None
    assert sessions.current(_jst(2026, 10, 2, 21, 35)) == "newyork"
    assert sessions.current(_jst(2026, 10, 2, 9, 0)) is None     # 東京は朝の処理が残す


def test_waiting_for_fresh_data_has_a_limit():
    assert sessions.may_wait_for_fresh_data("london", _jst(2026, 10, 2, 16, 40))
    assert not sessions.may_wait_for_fresh_data("london", _jst(2026, 10, 2, 16, 45))


# ------------------------------------------------ 区間


def test_segments_do_not_overlap():
    deadline = _jst(2026, 10, 3, 1, 0)
    assert sessions.segment_end("tokyo", _jst(2026, 10, 2, 8, 40), deadline) == \
        _jst(2026, 10, 2, 16, 0)
    assert sessions.segment_end("london", _jst(2026, 10, 2, 16, 5), deadline) == \
        _jst(2026, 10, 2, 21, 30)
    assert sessions.segment_end("newyork", _jst(2026, 10, 2, 21, 35), deadline) == deadline


# ------------------------------------------------ 記録


def _board(at: datetime, lean="down", state="aligned_down"):
    return {
        "pair": "USDJPY", "generated_at": at.isoformat(),
        "facts": {"price": 150.0, "adr": 1.0, "used_ratio": 0.3,
                  "position": 0.5, "market_open": True,
                  "price_at": (at - timedelta(minutes=15)).isoformat()},
        "factors": [{"key": "trend", "view": "down"}],
        "verdict": {"state": state, "lean": lean, "exclude": [], "cautions": []},
    }


@pytest.fixture
def url(tmp_path):
    return f"sqlite:///{(tmp_path / 'boards.sqlite3').as_posix()}"


def test_one_board_per_session(url):
    morning = _jst(2026, 10, 2, 8, 40)
    london = _jst(2026, 10, 2, 16, 5)
    assert board_log.record(_board(morning), database_url=url) is True
    assert board_log.record(_board(london), database_url=url, session="london") is True
    assert board_log.record(_board(london + timedelta(minutes=5), lean="up",
                                   state="aligned_up"),
                            database_url=url, session="london") is False
    rows = board_log.recent(url)
    assert sorted(r["session"] for r in rows) == ["london", "tokyo"]
    lon = next(r for r in rows if r["session"] == "london")
    assert lon["lean"] == "down"
    assert lon["segment_end"] == _jst(2026, 10, 2, 21, 30).isoformat()


def test_unknown_session_is_refused(url):
    with pytest.raises(ValueError):
        board_log.record(_board(_jst(2026, 10, 2, 8, 40)), database_url=url,
                         session="sydney")


def test_old_table_is_rebuilt_without_losing_rows(tmp_path):
    """2026-10-01 までの表（ブリーフの列も無い、いちばん古い形）から作り直す。"""
    db = tmp_path / "old.sqlite3"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE boards (
            id INTEGER PRIMARY KEY AUTOINCREMENT, pair TEXT NOT NULL,
            board_day TEXT NOT NULL, recorded_at TEXT NOT NULL,
            provider TEXT NOT NULL, price REAL, adr REAL, used_ratio REAL,
            position REAL, trend TEXT, momentum TEXT, fundamentals TEXT,
            state TEXT NOT NULL, lean TEXT, exclude TEXT, cautions TEXT,
            deadline TEXT, close_at_deadline REAL, move_adr REAL,
            best_adr REAL, worst_adr REAL, scored_at TEXT,
            UNIQUE(pair, board_day));
        CREATE INDEX idx_boards_open ON boards(scored_at, board_day);
    """)
    con.execute("INSERT INTO boards (pair, board_day, recorded_at, provider, state, "
                "move_adr, scored_at) VALUES ('USDJPY', 'd', 'r', 'csv', 'excluded', "
                "0.5, 's')")
    con.commit()
    con.close()
    rows = board_log.recent(f"sqlite:///{db.as_posix()}")
    assert len(rows) == 1
    assert rows[0]["session"] == "tokyo"
    assert rows[0]["move_adr"] == 0.5
    # 作り直した後は、同じ日でも時間帯が違えば残せる
    url = f"sqlite:///{db.as_posix()}"
    t = _jst(2026, 10, 2, 16, 5)
    assert board_log.record(_board(t), database_url=url, session="london") is True
    assert board_log.record(_board(t), database_url=url, session="tokyo") is True
    assert board_log.record(_board(t), database_url=url, session="tokyo") is False


# ------------------------------------------------ 採点


def _bars(start: datetime, n: int, base: float, step: float):
    return [Candle(timestamp=start + timedelta(minutes=15 * i),
                   open=base + step * i, high=base + step * i + 0.05,
                   low=base + step * i - 0.05, close=base + step * i, volume=0)
            for i in range(n)]


def test_segment_score_stops_at_the_next_session():
    """**東京の区間は、ロンドンの始まりより後の足を使わない。**"""
    rec = _jst(2026, 10, 2, 8, 40)
    seg = _jst(2026, 10, 2, 16, 0)
    deadline = _jst(2026, 10, 3, 1, 0)
    row = {"recorded_at": rec.isoformat(), "deadline": deadline.isoformat(),
           "segment_end": seg.isoformat(), "adr": 1.0, "price": 150.0,
           "lean": "down", "session": "tokyo"}
    before = _bars(rec + timedelta(minutes=5), 28, 149.95, -0.01)   # ロンドンまで下げる
    after = _bars(seg, 36, 151.0, 0.0)                              # ロンドン以降は上
    got = board_log.score_one(row, before + after, now=deadline + timedelta(hours=1))
    assert got["seg_move_adr"] > 0
    assert got["move_adr"] < 0


def test_old_rows_get_their_segment_from_the_recorded_time():
    rec = _jst(2026, 10, 2, 8, 40)
    deadline = _jst(2026, 10, 3, 1, 0)
    row = {"recorded_at": rec.isoformat(), "deadline": deadline.isoformat(),
           "adr": 1.0, "price": 150.0, "lean": None}
    bars = _bars(rec + timedelta(minutes=5), 70, 150.0, 0.01)
    got = board_log.score_one(row, bars, now=deadline + timedelta(hours=1))
    assert got["seg_move_adr"] is not None
    assert got["seg_close"] < got["close_at_deadline"]


# ------------------------------------------------ 週のまとめ


def _weekly():
    spec = importlib.util.spec_from_file_location(
        "weekly_report", ROOT / "scripts" / "weekly_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_week_is_summarised_only_after_it_closes():
    w = _weekly()
    assert w.default_monday(date(2026, 10, 3)) == date(2026, 9, 28)    # 土曜 → 今週
    assert w.default_monday(date(2026, 10, 2)) == date(2026, 9, 21)    # 金曜 → 先週
    assert w.default_monday(date(2026, 10, 5)) == date(2026, 9, 28)    # 月曜 → 先週


def test_weekly_report_never_names_a_rate_below_thirty():
    w = _weekly()
    day = fx_day_start(_jst(2026, 9, 29, 9, 0)).isoformat()
    rows = [{"pair": "USDJPY", "board_day": day, "session": s,
             "state": "aligned_up", "lean": "up", "scored_at": "x",
             "move_adr": 0.3, "best_adr": 0.5, "worst_adr": -0.2,
             "seg_move_adr": 0.1, "seg_best_adr": 0.2, "seg_worst_adr": -0.1,
             "brief_lean": "up", "brief_date": "2026-09-29"}
            for s in ("tokyo", "london", "newyork")]
    text = "\n".join(w.build(rows, date(2026, 9, 28)))
    assert "割合は名乗りません" in text
    assert "（—、" in text
    assert "100%（" not in text
    assert text.count("| 09/29 |") == 3
    assert "発注" not in text
