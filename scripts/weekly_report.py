# -*- coding: utf-8 -*-
"""1週間ぶんのボードの採点をまとめる（土曜の朝、Windows タスク「FX 週のまとめ」から）。

週は日本の月曜〜金曜の取引日（為替の1日はニューヨーク17時で替わる）。
まず採点できる行を採点し、その週の分と、これまでの累計を時間帯ごとに並べる。

- **予測はしない。** 並べるのは、残したボードのあとに相場がどう動いたかだけ。
- **30件に届かない区分は割合を名乗らない。** 1週間で届くことはまず無いので、
  週の欄は件数と値動きの幅を見るためのもの。判断は累計の欄で。
- 書き出し先は ``logs/weekly/<年>-W<週>.md``（版管理しない。ブリーフの判定を
  含むので、公開ページにも載せない）。

使い方::

    set FX_CSV_DATA_DIR=./data/real
    .venv/Scripts/python.exe scripts/weekly_report.py               # 直近の締まった週
    .venv/Scripts/python.exe scripts/weekly_report.py --week 2026-09-28   # その日を含む週
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import board_log                                     # noqa: E402
from app.board_report import SEGMENT, counts, summary         # noqa: E402
from app.config import get_settings                           # noqa: E402
from app.market_data import CSVMarketDataProvider             # noqa: E402
from app.sessions import LABEL, SESSIONS                      # noqa: E402

JST = timezone(timedelta(hours=9))
MARK = {"up": "▲", "down": "▼", "neutral": "■", "conflict": "■対立", None: "—"}
STATE = {"aligned_up": "上にそろった", "aligned_down": "下にそろった",
         "mixed": "まちまち", "excluded": "除外"}


def trading_date(row: Dict) -> date:
    """その行の取引日（ニューヨーク17時始まりの1日が主に属する日付）。"""
    start = datetime.fromisoformat(str(row["board_day"]))
    return (start.astimezone(timezone.utc) + timedelta(hours=12)).date()


def default_monday(today: date) -> date:
    """土日なら今週、平日なら先週の月曜（**締まっていない週はまとめない**）。"""
    monday = today - timedelta(days=today.weekday())
    return monday if today.weekday() >= 5 else monday - timedelta(days=7)


def _f(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:+.2f}"


def build(rows: Sequence[Dict], monday: date) -> List[str]:
    friday = monday + timedelta(days=4)
    week = [r for r in rows if monday <= trading_date(r) <= friday]
    iso = monday.isocalendar()
    out = [f"# FX ボードの週のまとめ　{iso[0]}-W{iso[1]:02d}"
           f"（{monday:%m/%d}〜{friday:%m/%d}）", "",
           f"作成 {datetime.now(JST):%Y-%m-%d %H:%M} JST。**予測ではありません。**"
           "残したボードのあとに、相場がどう動いたかを並べています。",
           "値動きは普段の1日の値幅（ADR）で割った値。そろった向き（▼なら下げ）を正にしています。",
           ""]

    out.append("## この週に残したボード")
    if not week:
        out.append("この週のボードはありません。")
    for s in SESSIONS:
        rs = [r for r in week if (r.get("session") or "tokyo") == s]
        out.append(f"- {LABEL[s]}: {counts(rs)}")
    out.append("")

    if week:
        out.append("## 1枚ずつ（時間帯の区間で測った値）")
        out.append("| 日 | 時間帯 | 銘柄 | ボード | ブリーフ | 区間の動き | 有利な振れ | 不利な振れ |")
        out.append("|---|---|---|---|---|---:|---:|---:|")
        order = {s: i for i, s in enumerate(SESSIONS)}
        for r in sorted(week, key=lambda r: (trading_date(r),
                                             order.get(r.get("session") or "tokyo", 9),
                                             r["pair"])):
            board = STATE.get(r.get("state"), r.get("state") or "—")
            if r.get("lean") in ("up", "down") and r.get("state") != "excluded":
                board += MARK[r["lean"]]
            brief = MARK.get(r.get("brief_lean"), "—") if r.get("brief_date") else "—"
            if r.get("brief_excluded"):
                brief += "（除外）"
            pending = "" if r.get("scored_at") else "（未採点）"
            out.append(f"| {trading_date(r):%m/%d} | {LABEL.get(r.get('session') or 'tokyo')} "
                       f"| {r['pair']} | {board} | {brief} | {_f(r.get('seg_move_adr'))}{pending} "
                       f"| {_f(r.get('seg_best_adr'))} | {_f(r.get('seg_worst_adr'))} |")
        out.append("")

    done_week = [r for r in week if r.get("scored_at")]
    out.append("## この週の採点")
    out.append("```")
    out += summary(done_week) if done_week else ["採点済みはまだありません。"]
    out.append("```")
    out.append("")

    done_all = [r for r in rows if r.get("scored_at") and trading_date(r) <= friday]
    out.append(f"## これまでの累計（{friday:%m/%d} まで）")
    out.append("**割合を読むのはこちら。** 30件に届いた区分だけ割合を出します。")
    out.append("```")
    out += summary(done_all) if done_all else ["採点済みはまだありません。"]
    out.append("```")
    out.append("")
    out.append("区間: " + " ／ ".join(f"{LABEL[s]} {SEGMENT[s]}" for s in SESSIONS))
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--week", help="この日（YYYY-MM-DD）を含む週をまとめる")
    ap.add_argument("--out-dir", default=str(ROOT / "logs" / "weekly"))
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    if a.week:
        d = date.fromisoformat(a.week)
        monday = d - timedelta(days=d.weekday())
    else:
        monday = default_monday(datetime.now(JST).date())

    settings = get_settings()
    url = settings.resolved_database_url
    got = board_log.score_pending(url, CSVMarketDataProvider(settings.csv_dir))
    rows = board_log.recent(url, limit=5000)

    lines = build(rows, monday)
    lines.insert(1, "")
    lines.insert(2, f"（今回採点 {got['scored']} 件 / 刻限前か足が足りない {got['pending']} 件）")
    text = "\n".join(lines) + "\n"
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    iso = monday.isocalendar()
    path = out_dir / f"{iso[0]}-W{iso[1]:02d}.md"
    path.write_text(text, encoding="utf-8")
    print(text)
    print(f"書き出し: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
