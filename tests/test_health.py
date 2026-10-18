# -*- coding: utf-8 -*-
"""仕組みの健康診断。止まっていたら「⚠」、動く時刻でなければ「待ち」。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app import health as H

J = timezone(timedelta(hours=9))


def _root(tmp_path, brief_date="2026-10-19", morning="2026-10-19", refresh="2026-10-19 09:25",
          weekly="2026-10-17 08:00", dxy="2026-10-19T00:00:00+00:00", morning_code=0):
    (tmp_path / "data" / "briefs").mkdir(parents=True)
    (tmp_path / "data" / "drivers").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    (tmp_path / "data" / "briefs" / "latest.json").write_text(json.dumps({"brief_date": brief_date}), encoding="utf-8")
    (tmp_path / "logs" / "morning.log").write_text(
        f"=== {morning} 08:40 朝の処理 ===\n08:40 相場の取り直し 終了コード={morning_code} 100秒\n", encoding="utf-8")
    (tmp_path / "logs" / "refresh.log").write_text(f"{refresh} 相場=0 相関=- ページ=- 30秒\n", encoding="utf-8")
    (tmp_path / "logs" / "weekly.log").write_text(f"\ufeff{weekly} 週のまとめ 終了コード=0 2秒 | x\n", encoding="utf-8")
    (tmp_path / "data" / "drivers" / "DXY_H1.csv").write_text(f"timestamp,close\n{dxy},100\n", encoding="utf-8")
    return tmp_path


def _states(items):
    return {x["key"]: x["state"] for x in items}


def test_all_ok_on_a_normal_morning(tmp_path):
    now = datetime(2026, 10, 19, 9, 30, tzinfo=J)                       # 月曜 9:30
    st = _states(H.check(now, _root(tmp_path)))
    assert st == {"brief": "ok", "morning": "ok", "refresh": "ok", "weekly": "ok", "dxy": "ok"}


def test_a_stale_brief_is_flagged_after_8_55(tmp_path):
    root = _root(tmp_path, brief_date="2026-10-09")
    assert _states(H.check(datetime(2026, 10, 19, 9, 30, tzinfo=J), root))["brief"] == "warn"


def test_nothing_is_flagged_at_night_or_on_weekends(tmp_path):
    root = _root(tmp_path, brief_date="2026-10-16", morning="2026-10-16",
                 refresh="2026-10-17 01:05", dxy="2026-10-16T15:00:00+00:00")
    st = _states(H.check(datetime(2026, 10, 18, 11, 0, tzinfo=J), root))   # 日曜
    assert st["brief"] == "ok" and st["morning"] == "ok"
    assert st["refresh"] == "idle" and st["dxy"] == "idle"


def test_a_stopped_refresh_and_a_failed_step_are_flagged(tmp_path):
    root = _root(tmp_path, refresh="2026-10-19 09:05", morning_code=1)
    st = _states(H.check(datetime(2026, 10, 19, 9, 40, tzinfo=J), root))
    assert st["refresh"] == "warn" and st["morning"] == "warn"


def test_session_window():
    assert H.in_session(datetime(2026, 10, 19, 9, 15, tzinfo=J))          # 月 9:15
    assert H.in_session(datetime(2026, 10, 17, 0, 50, tzinfo=J))          # 土 0:50（金曜の続き）
    assert not H.in_session(datetime(2026, 10, 19, 0, 50, tzinfo=J))      # 月 0:50（日曜の夜）
    assert not H.in_session(datetime(2026, 10, 18, 12, 0, tzinfo=J))      # 日曜


def test_the_page_shows_a_red_bar_only_when_something_is_wrong():
    from app import board_page as P
    ok = [{"key": "brief", "label": "ブリーフ", "state": "ok", "detail": "済み"}]
    bad = [{"key": "brief", "label": "ブリーフ", "state": "warn", "detail": "最新が 10/09 のまま"}]
    assert P._health_bar(ok) == ""
    assert "仕組みの不調" in P._health_bar(bad) and "10/09" in P._health_bar(bad)
