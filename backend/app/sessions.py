# -*- coding: utf-8 -*-
"""時間帯（東京・ロンドン・ニューヨーク）ごとにボードを残すための区切り。

朝 8:40 の1枚だけでは、ロンドンやニューヨークが始まったときの場面が
採点に入らない。そこで、それぞれの始まりにも1枚ずつ残し、**重ならない
区間**で採点する。

| 時間帯 | 残す時刻 | 採点の区間 |
|---|---|---|
| 東京 | 朝の処理（8:40） | 記録 → ロンドンの始まり |
| ロンドン | ロンドン 08:00（夏 16:00／冬 17:00 JST） | 記録 → ニューヨークの始まり |
| ニューヨーク | ニューヨーク 08:30（夏 21:30／冬 22:30 JST） | 記録 → 手仕舞いの刻限（01:00） |

区間を重ねない理由: 東京の1枚を刻限まで測ると、ロンドンやニューヨークの
値動きまで含んでしまい、3枚が同じ値動きを数えることになる。件数が増えた
ように見えて、中身は増えていない。

**英国と米国の夏時間は、切り替わる日が違う**（3月と10〜11月に数週間ずれる）。
それぞれの規則で判定する。固定で書くと半年ぶん間違える。

**予測はしない。** ここで決めているのは「いつ残して、どこまで測るか」だけ。
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Dict, Optional

from .freshness import _us_dst

UTC = timezone.utc

TOKYO, LONDON, NEWYORK = "tokyo", "london", "newyork"
SESSIONS = (TOKYO, LONDON, NEWYORK)
LABEL = {TOKYO: "東京", LONDON: "ロンドン", NEWYORK: "ニューヨーク"}

_DEFAULTS = {
    "london_open_local": "08:00",
    "newyork_open_local": "08:30",
    "grace_minutes": 60,
    "stale_wait_minutes": 45,
}


def settings(board_cfg: Optional[Dict]) -> Dict:
    s = dict(_DEFAULTS)
    s.update((board_cfg or {}).get("session_boards") or {})
    return s


def _hhmm(value: object, fallback: str) -> time:
    try:
        hh, mm = str(value).split(":")
        return time(int(hh), int(mm))
    except (AttributeError, TypeError, ValueError):
        hh, mm = fallback.split(":")
        return time(int(hh), int(mm))


def uk_dst(t: datetime) -> bool:
    """英国の夏時間か。3月の最終日曜 01:00 UTC 〜 10月の最終日曜 01:00 UTC。"""
    year = t.year

    def last_sunday(month: int) -> datetime:
        nxt = datetime(year + (month == 12), month % 12 + 1, 1, 1, tzinfo=UTC)
        d = nxt - timedelta(days=1)
        return d - timedelta(days=(d.weekday() + 1) % 7)

    return last_sunday(3) <= t.astimezone(UTC) < last_sunday(10)


def _trading_date(day_start: datetime):
    """為替の1日（ニューヨーク17時始まり）が主に属する日付。"""
    return (day_start.astimezone(UTC) + timedelta(hours=12)).date()


def open_at(session: str, day_start: datetime,
            board_cfg: Optional[Dict] = None) -> Optional[datetime]:
    """その取引日に、その時間帯が始まる時刻（UTC）。"""
    s = settings(board_cfg)
    d = _trading_date(day_start)
    if session == TOKYO:
        return datetime(d.year, d.month, d.day, 0, 0, tzinfo=UTC)   # 09:00 JST
    if session == LONDON:
        at = _hhmm(s["london_open_local"], _DEFAULTS["london_open_local"])
        noon = datetime(d.year, d.month, d.day, 12, tzinfo=UTC)
        offset = 1 if uk_dst(noon) else 0
        return datetime(d.year, d.month, d.day, at.hour, at.minute,
                        tzinfo=UTC) - timedelta(hours=offset)
    if session == NEWYORK:
        at = _hhmm(s["newyork_open_local"], _DEFAULTS["newyork_open_local"])
        noon = datetime(d.year, d.month, d.day, 12, tzinfo=UTC)
        offset = 4 if _us_dst(noon) else 5
        return datetime(d.year, d.month, d.day, at.hour, at.minute,
                        tzinfo=UTC) + timedelta(hours=offset)
    return None


def segment_end(session: str, recorded_at: datetime,
                deadline: Optional[datetime],
                board_cfg: Optional[Dict] = None) -> Optional[datetime]:
    """その1枚を採点する区間の終わり。**次の時間帯の始まりか刻限の早いほう。**"""
    from .board import fx_day_start

    day = fx_day_start(recorded_at)
    nxt = {TOKYO: open_at(LONDON, day, board_cfg),
           LONDON: open_at(NEWYORK, day, board_cfg)}.get(session)
    ends = [x for x in (nxt, deadline) if x is not None]
    if not ends:
        return None
    end = min(ends)
    return end if end > recorded_at else None


def current(now: datetime, board_cfg: Optional[Dict] = None) -> Optional[str]:
    """いまがロンドン・ニューヨークの「残す時間」なら、その名前。

    始まりから ``grace_minutes`` 分のあいだだけ。東京は朝の処理が残すので
    ここでは返さない。
    """
    from .board import fx_day_start

    s = settings(board_cfg)
    day = fx_day_start(now)
    grace = timedelta(minutes=float(s["grace_minutes"]))
    for name in (LONDON, NEWYORK):
        at = open_at(name, day, board_cfg)
        if at is not None and at <= now < at + grace:
            return name
    return None


def may_wait_for_fresh_data(session: str, now: datetime,
                            board_cfg: Optional[Dict] = None) -> bool:
    """足が古くて除外になったとき、次の取り込みを待ってよいか。

    **配信元の遅れは相場の事実ではない。** 始まりから ``stale_wait_minutes``
    分までは次の回（5分後）を待つ。それを過ぎたら、古いなら古いまま残す
    （残さないと、その日はその時間帯だけ抜け落ちる）。
    """
    from .board import fx_day_start

    s = settings(board_cfg)
    at = open_at(session, fx_day_start(now), board_cfg)
    if at is None:
        return False
    return now < at + timedelta(minutes=float(s["stale_wait_minutes"]))
