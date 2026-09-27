# -*- coding: utf-8 -*-
"""経済指標カレンダーを取り寄せる。

出所は Forex Factory の公開 JSON（``nfs.faireconomy.media``）。
robots.txt は ``Disallow:`` が空＝全面許可であることを確認済み。

**予定を作らない。** 取り込めたものだけを書き、取得日時を必ず残す。
古い予定表で「指標なし」と判断するのが、いちばん危ない。

使い方::

    python scripts/fetch_calendar.py --out data/calendar/events.json
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

UA = "fx-ai-system/0.1 (personal research; contact via repository owner)"
FEEDS = {
    "this_week": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "next_week": "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
}


def fetch(url: str) -> Optional[List[Dict]]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.load(r)
    except Exception as exc:
        print(f"  取得できません: {exc}", file=sys.stderr)
        return None
    return data if isinstance(data, list) else None


def normalise(raw: List[Dict]) -> tuple[List[Dict], List[str]]:
    """必要な項目だけ取り出し、読めない行は理由つきで落とす。

    **読めない行を「関係なし」として捨てない。** 重要度が High なのに
    通貨が読めない、というような行は、捨てると停止すべき場面を
    見落とす。ここでは currency を null のまま残し、判定側で
    「全銘柄に関係あり」として扱う。
    """
    out: List[Dict] = []
    problems: List[str] = []
    for i, e in enumerate(raw):
        title = (e.get("title") or "").strip()
        impact = (e.get("impact") or "").strip()
        raw_date = e.get("date")
        if not title or not raw_date:
            problems.append(f"{i}: title か date がありません")
            continue
        try:
            at = datetime.fromisoformat(str(raw_date))
        except ValueError:
            problems.append(f"{i}: 日時を読めません（{raw_date!r}）")
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        cur = (e.get("country") or "").strip().upper() or None
        if cur is not None and (len(cur) != 3 or not cur.isalpha()):
            problems.append(f"{i}: 通貨コードが不正（{cur!r}）→ 全銘柄扱いにします")
            cur = None
        out.append({
            "title": title,
            "currency": cur,
            "impact": impact or "Unknown",
            "at": at.astimezone(timezone.utc).isoformat(),
        })
    out.sort(key=lambda x: x["at"])
    return out, problems


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(description="経済指標カレンダーを取り寄せる")
    ap.add_argument("--out", default="data/calendar/events.json")
    a = ap.parse_args(argv)

    events: List[Dict] = []
    problems: List[str] = []
    got: List[str] = []
    for name, url in FEEDS.items():
        print(f"{name} ...", end=" ")
        raw = fetch(url)
        if raw is None:
            print("失敗")
            continue
        rows, probs = normalise(raw)
        events.extend(rows)
        problems.extend(f"{name} {p}" for p in probs)
        got.append(name)
        print(f"{len(rows)}件")

    if not events:
        print("取り込める指標がありませんでした。書き込みません。")
        return 1

    seen = set()
    unique: List[Dict] = []
    for e in sorted(events, key=lambda x: x["at"]):
        key = (e["at"], e["currency"], e["title"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(e)

    out = Path(a.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "Forex Factory calendar (nfs.faireconomy.media)",
        "feeds": got,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(unique),
        "events": unique,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")

    from collections import Counter
    imp = Counter(e["impact"] for e in unique)
    print()
    print(f"書き出し: {out}（{len(unique)}件）")
    print(f"重要度: {dict(imp)}")
    print(f"期間: {unique[0]['at'][:16]} 〜 {unique[-1]['at'][:16]}")
    if problems:
        print()
        print(f"読めなかった行 {len(problems)} 件:")
        for p in problems[:10]:
            print(f"  - {p}")
    print()
    print("指標は毎日変わります。取り込みは毎日行ってください。")
    print(f"（{__import__('json').loads(out.read_text(encoding='utf-8'))['fetched_at'][:16]} "
          f"より古くなると UNKNOWN として扱い、建てません）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
