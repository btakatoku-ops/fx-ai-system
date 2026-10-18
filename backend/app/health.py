# -*- coding: utf-8 -*-
"""仕組みの健康診断（2026-10-18）。**止まっていることに気づけるようにする。**

ブリーフの取り込みが1週間止まっていたのに、誰も気づかなかった（10/09〜10/16）。画面は
動いて見えていたので、止まっているのはログを読まないと分からなかった。そこで、自動の処理が
「動くはずの時刻に動いたか」を毎回確かめ、ページの一番上と FEED 欄に出す。

| 項目 | 動くはずの時 | 確かめ方 |
|---|---|---|
| ブリーフ | 平日 8:34・8:50 | data/briefs/latest.json の日付が今日か（平日 8:55 以降） |
| 朝の処理 | 平日 8:40 | logs/morning.log に今日の回があり、終了コードがすべて0か |
| 取引時間中の取り込み | 平日 9:05〜翌1:05、5分ごと | logs/refresh.log の最後の行が12分以内で、相場・ページが0か |
| 週のまとめ | 土曜 8:00 | logs/weekly.log の最後の回が8日以内で、終了コードがすべて0か |
| ドル指数 | 取引時間中は毎時 | data/drivers/DXY_H1.csv の最後の足が3時間以内か |

**動くはずの時刻でなければ「待ち」**（夜中や週末に「止まっている」とは言わない）。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .config import PROJECT_ROOT

JST = timezone(timedelta(hours=9))
OK, WARN, IDLE = "ok", "warn", "idle"


def _read(path: Path) -> List[str]:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return []


def _last_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def in_session(now: datetime) -> bool:
    """取引時間中の取り込みが動いているはずの時刻か（平日 9:10〜翌 1:10）。"""
    j = now.astimezone(JST)
    t, wd = j.time(), j.weekday()
    if wd < 5 and t >= time(9, 10):
        return True
    return 1 <= wd <= 5 and t <= time(1, 10)          # 火〜土の未明は、前の日の続き


def brief(now: datetime, root: Path) -> Dict:
    j = now.astimezone(JST)
    try:
        d = json.loads((root / "data" / "briefs" / "latest.json").read_text(encoding="utf-8"))
        got = date.fromisoformat(str(d.get("brief_date")))
    except (OSError, ValueError, TypeError):
        return {"key": "brief", "label": "ブリーフ", "state": WARN, "detail": "取り込んだブリーフが読めません"}
    want = j.date() if (j.weekday() < 5 and j.time() >= time(8, 55)) else _last_weekday(j.date() - timedelta(days=1)
                                                                                         if j.weekday() < 5 else j.date())
    if got >= want:
        return {"key": "brief", "label": "ブリーフ", "state": OK, "detail": f"{got:%m/%d} の分を取り込み済み"}
    return {"key": "brief", "label": "ブリーフ", "state": WARN,
            "detail": f"最新が {got:%m/%d} のまま（{want:%m/%d} の分が入っていない）"}


def morning(now: datetime, root: Path) -> Dict:
    j = now.astimezone(JST)
    lines = _read(root / "logs" / "morning.log")
    heads = [i for i, l in enumerate(lines) if l.startswith("=== ")]
    if not heads:
        return {"key": "morning", "label": "朝の処理", "state": WARN, "detail": "記録がありません"}
    last = heads[-1]
    m = re.match(r"=== (\d{4}-\d{2}-\d{2})", lines[last])
    day = date.fromisoformat(m.group(1)) if m else None
    bad = [l for l in lines[last:] if "終了コード=" in l and "終了コード=0" not in l]
    due = j.weekday() < 5 and j.time() >= time(8, 50)
    want = j.date() if due else _last_weekday(j.date() - timedelta(days=1))
    if day is None or day < want:
        return {"key": "morning", "label": "朝の処理", "state": WARN if due or day is None else IDLE,
                "detail": f"最後は {day:%m/%d}" if day else "日付が読めません"}
    if bad:
        return {"key": "morning", "label": "朝の処理", "state": WARN,
                "detail": f"{day:%m/%d} の回で失敗した手順あり（{len(bad)}件）"}
    return {"key": "morning", "label": "朝の処理", "state": OK, "detail": f"{day:%m/%d} の回はすべて成功"}


def refresh(now: datetime, root: Path) -> Dict:
    lines = [l for l in _read(root / "logs" / "refresh.log") if re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", l)]
    if not lines:
        return {"key": "refresh", "label": "取引時間中の取り込み", "state": WARN, "detail": "記録がありません"}
    last = lines[-1]
    at = datetime.strptime(last[:16], "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    age = (now - at).total_seconds() / 60
    failed = ("相場=0" not in last) or re.search(r"ページ=[1-9]", last) is not None
    if not in_session(now):
        return {"key": "refresh", "label": "取引時間中の取り込み", "state": IDLE,
                "detail": f"取引時間外（最後 {at:%m/%d %H:%M}）"}
    if age > 12:
        return {"key": "refresh", "label": "取引時間中の取り込み", "state": WARN,
                "detail": f"最後の回が {age:.0f} 分前（5分ごとのはず）"}
    if failed:
        return {"key": "refresh", "label": "取引時間中の取り込み", "state": WARN,
                "detail": f"{at:%H:%M} の回で失敗あり"}
    return {"key": "refresh", "label": "取引時間中の取り込み", "state": OK, "detail": f"{at:%H:%M} の回まで成功"}


def weekly(now: datetime, root: Path) -> Dict:
    lines = [l for l in _read(root / "logs" / "weekly.log") if re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", l)]
    runs = [l for l in lines if "週のまとめ" in l]
    if not runs:
        return {"key": "weekly", "label": "週のまとめ", "state": WARN, "detail": "記録がありません"}
    day = datetime.strptime(runs[-1][:10], "%Y-%m-%d").date()
    after = [l for l in lines if l[:10] >= runs[-1][:10]]
    bad = [l for l in after if "終了コード=" in l and "終了コード=0" not in l]
    if (now.astimezone(JST).date() - day).days > 8:
        return {"key": "weekly", "label": "週のまとめ", "state": WARN, "detail": f"最後が {day:%m/%d}（毎週土曜のはず）"}
    if bad:
        return {"key": "weekly", "label": "週のまとめ", "state": WARN, "detail": f"{day:%m/%d} の回で失敗あり（{len(bad)}件）"}
    return {"key": "weekly", "label": "週のまとめ", "state": OK, "detail": f"{day:%m/%d} の回はすべて成功"}


def dxy(now: datetime, root: Path) -> Dict:
    lines = _read(root / "data" / "drivers" / "DXY_H1.csv")
    try:
        last = datetime.fromisoformat(lines[-1].split(",")[0]) + timedelta(hours=1)
    except (IndexError, ValueError):
        return {"key": "dxy", "label": "ドル指数", "state": WARN, "detail": "読めません"}
    age = (now - last).total_seconds() / 3600
    if not in_session(now):
        return {"key": "dxy", "label": "ドル指数", "state": IDLE, "detail": f"取引時間外（最後 {last.astimezone(JST):%m/%d %H:%M}）"}
    if age > 3:
        return {"key": "dxy", "label": "ドル指数", "state": WARN, "detail": f"最後の足が {age:.1f} 時間前"}
    return {"key": "dxy", "label": "ドル指数", "state": OK, "detail": f"{last.astimezone(JST):%H:%M} の足まで"}


def check(now: Optional[datetime] = None, root: Path = PROJECT_ROOT) -> List[Dict]:
    now = now or datetime.now(timezone.utc)
    out = []
    for fn in (brief, morning, refresh, weekly, dxy):
        try:
            out.append(fn(now, root))
        except Exception as exc:                  # noqa: BLE001
            out.append({"key": fn.__name__, "label": fn.__name__, "state": WARN, "detail": f"確かめられません（{exc}）"})
    return out
