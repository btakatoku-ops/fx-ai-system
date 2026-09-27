# -*- coding: utf-8 -*-
"""日計り（その日のうちに閉じる）の決まり。

**翌日に持ち越さない。** そのためには2つ要る。

1. **手仕舞いの刻限**。そこまでに閉じる。
2. **建てない時間帯**。刻限まで時間が残っていない場面では、そもそも
   建てない。残り10分で損益比1.5を狙っても、届く前に刻限が来る。

## 刻限をどこに置くか

**ロールオーバー（日替わり）の手前。** 為替の1日はニューヨークの夕方で
替わり、そこを跨ぐとスワップが付く。跨がなければスワップは発生しない。

ニューヨーク17時は日本時間で

- 夏時間: 06:00
- 標準時: 07:00

**夏時間で1時間ずれる。** 固定で書くと半年ぶん間違える（週末の境目で
一度これを踏んだ）。``freshness._us_dst`` と同じ判定を使う。

## 01:00 に閉じる場合（2026-09-27 から）

FXモーニングブリーフに合わせて、刻限を **日本時間 01:00**（ロンドンの引け
ごろ）に置いている。このとき、1:00 から日替わり（06:00／07:00）までの
数時間は**新しく建てない**。そこで建てると、01:00 の刻限は明日になり、
日替わりを跨いでしまう。

そこで刻限は「**いまの取引日（直前の日替わり）が始まってから、最初に来る
設定時刻**」とする。01:00 を過ぎたら刻限は「過ぎた」扱いで、次の日替わり
まで建てない。設定を 06:00／07:00 にすれば、これまでと同じ動きになる。
**どんな設定でも日替わりは跨がない。**

## ここで決めないこと

**勝てるかどうかは何も言っていない。** 決めているのは「いつまでに
閉じるか」と「それに間に合わないなら建てない」だけ。
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Dict, Optional, Tuple

from .freshness import _us_dst

JST = timezone(timedelta(hours=9))


def _parse_hhmm(value: object, fallback: time) -> time:
    try:
        hh, mm = str(value).split(":")
        return time(int(hh), int(mm))
    except (AttributeError, TypeError, ValueError):
        return fallback


def settings(cfg: Dict) -> Dict:
    """日計りの設定。**無ければ「使わない」。勝手に有効にしない。**"""
    return (cfg or {}).get("day_trade") or {}


def enabled(cfg: Dict) -> bool:
    return bool(settings(cfg).get("enabled"))


def rollover_start(now: datetime) -> datetime:
    """いまの取引日が始まった時刻（直前のニューヨーク17時。UTC）。"""
    hour = 21 if _us_dst(now) else 22
    start = now.astimezone(timezone.utc).replace(hour=hour, minute=0,
                                                 second=0, microsecond=0)
    if start > now:
        start -= timedelta(days=1)
    return start


def exit_deadline(cfg: Dict, now: datetime) -> Optional[datetime]:
    """いまの取引日の手仕舞い刻限（UTC）。

    **取引日が始まってから最初に来る設定時刻。** 01:00 に設定していれば、
    01:00 を過ぎてから日替わりまでのあいだは、刻限は「もう過ぎた」時刻を
    返す（明日の 01:00 にはしない。それだと日替わりを跨ぐ）。

    夏時間かどうかで日替わりが1時間ずれる。**固定しない。**
    """
    s = settings(cfg)
    if not s.get("enabled"):
        return None
    key = "force_exit_jst_dst" if _us_dst(now) else "force_exit_jst"
    at = _parse_hhmm(s.get(key), time(6, 0) if _us_dst(now) else time(7, 0))

    start = rollover_start(now).astimezone(JST)
    deadline = start.replace(hour=at.hour, minute=at.minute,
                             second=0, microsecond=0)
    if deadline <= start:
        deadline += timedelta(days=1)
    return deadline.astimezone(timezone.utc)


def minutes_left(cfg: Dict, now: datetime) -> Optional[float]:
    """刻限まであと何分か。使わない設定なら ``None``。"""
    deadline = exit_deadline(cfg, now)
    if deadline is None:
        return None
    return (deadline - now).total_seconds() / 60.0


def can_open(cfg: Dict, now: datetime) -> Tuple[bool, Optional[str]]:
    """いま建ててよいか。だめなら理由も返す。

    **刻限まで時間が残っていない場面では建てない。** 残り10分で
    損益比1.5を狙っても、届く前に刻限が来る。届かないと分かっている
    賭けを、費用を払って始めることになる。
    """
    s = settings(cfg)
    if not s.get("enabled"):
        return True, None

    left = minutes_left(cfg, now)
    if left is None:
        return True, None

    need = float(s.get("min_minutes_to_exit", 60))
    if left <= 0:
        return False, (
            f"今日の手仕舞いの刻限（{exit_deadline(cfg, now).astimezone(JST):%H:%M}）"
            f"を過ぎています。**翌日に持ち越さないため、次の取引日"
            f"（日替わり後）まで建てません**")
    if left < need:
        return False, (
            f"手仕舞いの刻限まで {left:.0f} 分しかありません"
            f"（{need:.0f} 分以上が必要）。"
            f"**翌日に持ち越さないため、ここでは建てません**")

    # 建てる時間帯を絞っている場合（流動性の薄い時間を避けたいときなど）
    local = now.astimezone(JST)
    start = s.get("entry_from_jst")
    until = s.get("entry_until_jst")
    if start and until:
        a = _parse_hhmm(start, time(0, 0))
        b = _parse_hhmm(until, time(23, 59))
        cur = local.time()
        inside = (a <= cur <= b) if a <= b else (cur >= a or cur <= b)
        if not inside:
            return False, (
                f"建てる時間帯の外です（{start}〜{until} の内側だけ）。"
                f"いまは {local:%H:%M}")
    return True, None


def max_hold_bars(cfg: Dict, now: datetime, timeframe_minutes: int,
                  cap: int) -> int:
    """刻限までに収まる保有本数。

    **設定の上限と、刻限までの短いほうを採る。** 上限だけ見ていると、
    8時間保有の設定が日をまたぐ。
    """
    left = minutes_left(cfg, now)
    if left is None or timeframe_minutes <= 0:
        return cap
    return max(1, min(cap, int(left // timeframe_minutes)))
