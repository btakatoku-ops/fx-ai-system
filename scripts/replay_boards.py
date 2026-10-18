# -*- coding: utf-8 -*-
"""過去の足でボードを作り直し、当たり具合を数える（backend/app/board_replay.py）。

使い方::

    set FX_MARKET_DATA_PROVIDER=csv
    set FX_CSV_DATA_DIR=./data/real
    .venv/Scripts/python.exe scripts/replay_boards.py            # ダッシュボードの銘柄（円建て3本＋非円建ての候補）
    .venv/Scripts/python.exe scripts/replay_boards.py --pairs GBPJPY,USDJPY

結果は logs/replay/<日付>.md と .json に書く。**予測ではなく、過去の数え上げ。**
毎回の結果は logs/replay/archive.json に積み重ねる（土曜の週のまとめから毎週走らせる）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import board_replay as R                              # noqa: E402
from app import daily_pick                                     # noqa: E402
from app.config import get_settings, get_trading_config        # noqa: E402
from app.market_data import build_provider                     # noqa: E402

JST = timezone(timedelta(hours=9))


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _range(lo, hi) -> str:
    return "—" if lo is None else f"{lo * 100:.0f}〜{hi * 100:.0f}%"


def first_usable(provider, symbols: Sequence[str]) -> Optional[date]:
    """どの銘柄も全部の時間足がそろう最初の日（＋指標の立ち上がり3日）。"""
    starts = []
    for s in symbols:
        for tf in ("M5", "M15", "H1"):
            try:
                c = provider.get_candles(s, tf, limit=100000).candles
            except Exception:                      # noqa: BLE001
                continue
            if c:
                starts.append(c[0].timestamp)
    return (max(starts) + timedelta(days=3)).astimezone(JST).date() if starts else None


def report(summary: Dict, symbols: Sequence[str], first: date, last: date) -> str:
    g = summary["groups"]
    base = summary["baseline"]["tp1"]
    L: List[str] = [f"# ボードの再生（{first} 〜 {last}）", "",
                    f"銘柄: {', '.join(symbols)}　／　作り直したボード {summary['boards']} 枚"
                    f"（足が足りず飛ばした {summary['skipped']} 枚）", "",
                    "**ファンダ・ブリーフは過去の見立てが無いので「無し」。** 測っているのはテクニカル2つ"
                    "（トレンド・モメンタム）がそろった場面。経済指標の除外は効かず、スプレッドは一定なので、"
                    "**実際より少し甘い側に出る。**", "",
                    "## TP1（損切りの1.5倍）に、刻限までに先に届いたか", "",
                    "| 場面 | 件数 | TP1 | 損切り | 時間切れ | TP1 の割合（95%の幅） | 平均 R |",
                    "|---|---|---|---|---|---|---|"]
    for name, s in list(g.items()) + [("比べる相手：同じ時刻に向きを問わず（買い・売り両方）", {"tp1": base})]:
        t = s["tp1"]
        L.append(f"| {name} | {t['n']} | {t['win']} | {t['loss']} | {t['timeout']} | "
                 f"{_pct(t['win_rate'])}（{_range(t['win_lo'], t['win_hi'])}） | "
                 f"{'—' if t['avg_r'] is None else f'{t['avg_r']:+.2f}'} |")
    L += ["", "TP1 が損切りの1.5倍なので、時間切れを除けば **TP1 の割合が40%を超えて初めて** 費用を"
          "取り戻せる。平均 R が0より上なら、滑り込み込みでプラス。", "",
          "## 向き（そろった向きに動いたか）", "",
          "| 場面 | 区切りまで（件数・割合・幅） | 刻限まで（件数・割合・幅） |", "|---|---|---|"]
    for name, s in g.items():
        a, b = s["to_segment"], s["to_deadline"]
        L.append(f"| {name} | {a['n']}件 {_pct(a['rate'])}（{_range(a['lo'], a['hi'])}） | "
                 f"{b['n']}件 {_pct(b['rate'])}（{_range(b['lo'], b['hi'])}） |")
    L += ["", "コイン投げなら50%。95%の幅が50%をまたいでいれば、**偶然と区別できない。**", "",
          "## ボードが「そろった」（除外なし）の内訳", "", "| 銘柄 | 件数 | TP1 の割合 | 平均 R |", "|---|---|---|---|"]
    for p, t in summary["by_pair"].items():
        L.append(f"| {p} | {t['n']} | {_pct(t['win_rate'])} | "
                 f"{'—' if t['avg_r'] is None else f'{t['avg_r']:+.2f}'} |")
    L += ["", "| 時間帯 | 件数 | TP1 の割合 | 平均 R |", "|---|---|---|---|"]
    for s, t in summary["by_session"].items():
        L.append(f"| {s} | {t['n']} | {_pct(t['win_rate'])} | "
                 f"{'—' if t['avg_r'] is None else f'{t['avg_r']:+.2f}'} |")
    L += ["", f"## 除外（全体の {_pct(summary['excluded_share'])}）の理由の多い順", ""]
    for k, v in summary["exclude_reasons"]:
        L.append(f"- {v} 件：{k}")
    return "\n".join(L) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pairs", help="カンマ区切り（省略時はダッシュボードの銘柄）")
    ap.add_argument("--first", help="YYYY-MM-DD（省略時はデータのそろう最初の日）")
    ap.add_argument("--last", help="YYYY-MM-DD（省略時は昨日）")
    a = ap.parse_args(argv)

    cfg = get_trading_config()
    provider = build_provider(get_settings())
    symbols = (a.pairs.split(",") if a.pairs else
               daily_pick.settings(cfg)["jpy"] + daily_pick.candidates(cfg))
    first = date.fromisoformat(a.first) if a.first else first_usable(provider, symbols)
    last = date.fromisoformat(a.last) if a.last else datetime.now(JST).date() - timedelta(days=1)
    if first is None or first > last:
        print("再生できる期間がありません")
        return 1
    rows = R.replay(cfg, provider, symbols, first, last)
    out = ROOT / "logs" / "replay"
    out.mkdir(parents=True, exist_ok=True)
    # **毎週積み重ねる。** 手元の足は直近約60日しか無いので、測り直した分を足していく
    # （規則が変わったら、前の分は別名で残して新しく始める）
    merged = R.merge_archive(out / "archive.json", rows, R.rules_fingerprint(cfg))
    summary = R.summarize(merged)
    a_first = datetime.fromisoformat(merged[0]["at"]).astimezone(JST).date() if merged else first
    a_last = datetime.fromisoformat(merged[-1]["at"]).astimezone(JST).date() if merged else last
    stamp = datetime.now(JST).strftime("%Y-%m-%d")
    text = report(summary, sorted({r["pair"] for r in merged}) or symbols, a_first, a_last)
    text = text.replace("\n\n", f"\n\n今回の再生 {first}〜{last}（{len(rows)} 枚）を、これまでの分に積み重ねた結果。\n\n", 1)
    (out / f"{stamp}.md").write_text(text, encoding="utf-8")
    (out / f"{stamp}.json").write_text(json.dumps({"summary": summary, "rows": rows},
                                                  ensure_ascii=False, indent=1), encoding="utf-8")
    print(text)
    print(f"書きました: {out / (stamp + '.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
