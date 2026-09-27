# -*- coding: utf-8 -*-
"""経済指標（配点10点 ＋ 強制条件）。

**発表前後の停止は強制条件で表す。点数では表さない。**
点数にすると、他の項目が高ければ上書きできてしまう。「指標の15分前」は、
どんなに場面が整っていても建てない条件。

分けているもの。

| 出るもの | 何を表すか |
|---|---|
| 強制条件 | 指標の前後（blackout）に入っているか。入っていれば NO_TRADE |
| 配点10点 | 次の重要指標までどれだけ余裕があるか |

状態は4つ。

- ``QUIET``    : 予定表があり、当面は重要指標が無い
- ``UPCOMING`` : 予定表があり、重要指標が近い（blackout の外）
- ``ACTIVE``   : blackout の中。**建てない**
- ``UNKNOWN``  : 予定表が古い、または読めない。**建てない**

予定表がそもそも無い場合は「確かめようがない」ので配点から外す
（``available=False``）。一方、**予定表があるのに古い場合は建てない**。
古い予定表で「指標なし」と判断するのが、いちばん危ない。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import PROJECT_ROOT, PairSpec
from .models import NewsState

log = logging.getLogger(__name__)

PHASE = 2
CALENDAR_PATH = PROJECT_ROOT / "data" / "calendar" / "events.json"


@dataclass
class Event:
    title: str
    currency: Optional[str]
    impact: str
    at: datetime

    def relevant_to(self, spec: PairSpec) -> bool:
        """この指標がその銘柄に関係するか。

        **通貨が読めない指標を「関係なし」として捨てない。** 捨てると、
        停止すべき場面を見落とす。読めないものは全銘柄に関係ありとする。
        """
        if self.currency is None:
            return True
        return self.currency in (spec.base.upper(), spec.quote.upper())


@dataclass
class NewsView:
    """指標から見た状況。"""

    state: NewsState = NewsState.UNKNOWN
    available: bool = False
    blocked: bool = False
    ratio: float = 0.0
    minutes_to_next: Optional[float] = None
    next_event: Optional[str] = None
    covers_from: Optional[str] = None
    covers_to: Optional[str] = None
    source: Optional[str] = None
    fetched_at: Optional[str] = None
    age_hours: Optional[float] = None
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict:
        return {
            "state": self.state.value, "available": self.available,
            "blocked": self.blocked, "ratio": round(self.ratio, 4),
            "minutes_to_next": (round(self.minutes_to_next, 1)
                                if self.minutes_to_next is not None else None),
            "next_event": self.next_event,
            "covers_from": self.covers_from, "covers_to": self.covers_to,
            "source": self.source, "fetched_at": self.fetched_at,
            "age_hours": (round(self.age_hours, 1)
                          if self.age_hours is not None else None),
            "reasons": self.reasons,
        }


@lru_cache(maxsize=4)
def load_calendar(path: Optional[str] = None
                  ) -> Tuple[Optional[datetime], str, Tuple[Event, ...]]:
    """予定表を読む。無ければ空を返す（例外にしない）。"""
    p = Path(path) if path else CALENDAR_PATH
    if not p.exists():
        return None, "", ()
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("予定表を読めません: %s", exc)
        return None, "", ()

    fetched: Optional[datetime] = None
    raw_fetched = payload.get("fetched_at")
    if raw_fetched:
        try:
            fetched = datetime.fromisoformat(str(raw_fetched))
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
        except ValueError:
            fetched = None

    events: List[Event] = []
    for e in payload.get("events") or []:
        try:
            at = datetime.fromisoformat(str(e["at"]))
        except (KeyError, ValueError, TypeError):
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        cur = e.get("currency")
        events.append(Event(
            title=str(e.get("title") or ""),
            currency=str(cur).upper() if cur else None,
            impact=str(e.get("impact") or "Unknown"),
            at=at.astimezone(timezone.utc)))
    events.sort(key=lambda x: x.at)
    return fetched, str(payload.get("source") or ""), tuple(events)


def clear_cache() -> None:
    load_calendar.cache_clear()


def evaluate(spec: PairSpec, now: datetime, cfg: Dict,
             path: Optional[str] = None) -> NewsView:
    """その銘柄・その時刻の指標の状況。"""
    view = NewsView()
    fetched, source, events = load_calendar(path)

    if not events:
        view.reasons.append(
            "指標カレンダーを取り込んでいません（推測では埋めません）")
        return view                        # available=False のまま

    view.source = source or None
    view.fetched_at = fetched.isoformat() if fetched else None
    view.covers_from = events[0].at.isoformat()
    view.covers_to = events[-1].at.isoformat()

    # --- 収録範囲の外は「確かめようがない」---
    #
    # **範囲外を「指標なし」と読んではいけない。** 予定表が9月の1週間しか
    # 収録していないのに7月の時刻を渡すと、「次の指標まで1500時間」＝
    # 満点、という架空の加点になる。過去を検証するときに全バーへ10点が
    # 乗り、比較が丸ごと壊れる。実際そうなっていた。
    margin = timedelta(hours=float(cfg.get("coverage_margin_hours", 24)))
    covers_from = events[0].at - margin
    covers_to = events[-1].at + margin
    if now < covers_from or now > covers_to:
        view.reasons.append(
            f"予定表はこの時刻を含んでいません"
            f"（収録 {covers_from:%Y-%m-%d} 〜 {covers_to:%Y-%m-%d}）。"
            f"指標の有無を判断できません")
        return view                        # available=False のまま

    view.available = True

    # --- 予定表の鮮度。**古い予定表で「指標なし」と言わない。** ---
    max_age = float(cfg.get("source_max_age_hours", 36))
    if fetched is None:
        view.state = NewsState.UNKNOWN
        view.blocked = True
        view.reasons.append("予定表に取得日時がありません。建てません")
        return view
    view.age_hours = (now - fetched).total_seconds() / 3600.0
    if view.age_hours > max_age:
        view.state = NewsState.UNKNOWN
        view.blocked = True
        view.reasons.append(
            f"予定表が {view.age_hours:.0f} 時間前のもので古すぎます"
            f"（{max_age:.0f} 時間まで）。取り込み直してください")
        return view

    relevant_impacts = set(cfg.get("relevant_impacts", ["High", "Medium"]))
    blackout = cfg.get("blackout", {})

    # --- blackout に入っていないか（強制条件） ---
    for e in events:
        if e.impact not in relevant_impacts or not e.relevant_to(spec):
            continue
        window = blackout.get(e.impact)
        if not window:
            continue
        start = e.at - timedelta(minutes=float(window["before_minutes"]))
        end = e.at + timedelta(minutes=float(window["after_minutes"]))
        if start <= now <= end:
            view.state = NewsState.ACTIVE
            view.blocked = True
            view.minutes_to_next = (e.at - now).total_seconds() / 60.0
            view.next_event = f"{e.currency or '—'} {e.title}"
            view.reasons.append(
                f"{e.impact} 指標「{e.title}」（{e.currency or '通貨不明'}）の"
                f"前後です。建てません")
            return view

    # --- 次の重要指標までの余裕（配点） ---
    upcoming = [e for e in events
                if e.at > now and e.impact in relevant_impacts
                and e.relevant_to(spec)]
    comfortable = float(cfg.get("comfortable_minutes", 240))
    if not upcoming:
        view.state = NewsState.QUIET
        view.ratio = 1.0
        view.reasons.append("当面、関係する重要指標の予定はありません")
        return view

    nxt = upcoming[0]
    minutes = (nxt.at - now).total_seconds() / 60.0
    view.minutes_to_next = minutes
    view.next_event = f"{nxt.currency or '—'} {nxt.title}"
    view.ratio = max(0.0, min(1.0, minutes / comfortable)) if comfortable > 0 else 1.0
    view.state = NewsState.QUIET if minutes >= comfortable else NewsState.UPCOMING
    view.reasons.append(
        f"次の{nxt.impact}指標「{nxt.title}」（{nxt.currency or '通貨不明'}）まで "
        f"{minutes / 60:.1f} 時間")
    return view
