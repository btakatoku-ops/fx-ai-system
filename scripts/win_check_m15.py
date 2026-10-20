# -*- coding: utf-8 -*-
"""勝率の検証（win_lab）の 1時間足での近似を、手元の M15（直近約12週）で確かめる。

- 判断の材料（日足の向き・1時間足の RSI など）は同じ。判断の時刻は**時間帯の始まりちょうど**
  （1時間足版は次の正時に丸めていた）。
- 結果は M15 の足で、**計画と同じ幅（利確＝損切り＝ATR(M15)×2）**、費用なし。根拠が無ければ 50%。
- 同じ期間の 1時間足版も並べ、近似がずれていないかを見る。

    .venv/Scripts/python.exe scripts/win_check_m15.py

結果は logs/replay/win-m15-<日付>.md。**期間が短いので幅は広い。** 2年の結果と矛盾しないかを見る確認。
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import long_lab as LL                                 # noqa: E402
from app import sessions                                       # noqa: E402
from app import signal_lab as S                                # noqa: E402
from app import win_lab as W                                   # noqa: E402
from app.board import fx_day_start                             # noqa: E402
from app.config import get_trading_config                      # noqa: E402
from app.market_data import CSVMarketDataProvider              # noqa: E402

JST = timezone(timedelta(hours=9))
REAL = ROOT / "data" / "real"
CHECK = ("lean", "d_trend_pullback", "d_trend", "rsi_rev", "bb_rev", "asia_fade")


def exact_times(cfg, first, last):
    out = []
    d = first
    while d <= last:
        if d.weekday() < 5:
            tokyo = datetime(d.year, d.month, d.day, 0, 0, tzinfo=timezone.utc)
            day = fx_day_start(tokyo)
            out.append((tokyo, sessions.TOKYO))
            for name in (sessions.LONDON, sessions.NEWYORK):
                at = sessions.open_at(name, day, getattr(cfg, "board", None))
                if at is not None:
                    out.append((at, name))
        d += timedelta(days=1)
    return out


def summarize(rows: List[Dict], pick) -> Dict:
    pts, longs, shorts, by_day = [], [], [], defaultdict(list)
    for r in rows:
        d = pick(r)
        if d not in ("up", "down"):
            continue
        p = r["score"] if d == "up" else 1.0 - r["score"]
        pts.append(p)
        (longs if d == "up" else shorts).append(p)
        by_day[fx_day_start(r["at"])].append(p)
    return W.summarize(pts, longs, shorts, by_day)


def _p(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def cell(e: Dict) -> str:
    if not e["n"]:
        return "—"
    ci = "" if e["lo"] is None else f"［{e['lo'] * 100:.0f}〜{e['hi'] * 100:.0f}］"
    return f"{e['n']}件・**{_p(e['win'])}**{ci}・買い {_p(e['long'])}／売り {_p(e['short'])}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    cfg = get_trading_config()
    prov = CSVMarketDataProvider(REAL)
    names = sorted({p.name.split("_")[0] for p in REAL.glob("*_M15.csv")}
                   & {p.name.split("_")[0] for p in REAL.glob("*_H1.csv")})
    h1, m15 = {}, {}
    for n in names:
        h1[n] = S.from_candles(prov.get_candles(n, "H1", limit=10 ** 6).candles, 60)
        m15[n] = S.from_candles(prov.get_candles(n, "M15", limit=10 ** 6).candles, 15)
    first = max(s.ts[0] for s in m15.values()).date() + timedelta(days=1)
    last = max(s.ts[-1] for s in m15.values()).date()
    exact = exact_times(cfg, first, last)
    rounded = LL.decision_times(cfg, first, last)
    rows_m15, rows_h1 = [], []
    for n in names:
        rows_m15 += W.rows_for(cfg, n, h1[n], exact, exec_s=m15[n], exec_mult=2.0)
        rows_h1 += W.rows_for(cfg, n, h1[n], rounded)
    up15, uph1 = summarize(rows_m15, lambda r: "up"), summarize(rows_h1, lambda r: "up")

    L = [f"# 勝率の検証を M15 で確かめる（{first}〜{last}）", "",
         f"銘柄 {len(names)}（手元の M15 があるもの）・判断 M15 版 {len(rows_m15)} 件／1時間足版 {len(rows_h1)} 件。",
         "**利確＝損切り・費用なし。根拠が無ければ 50%。** M15 版＝時間帯の始まりちょうどに判断し、"
         "幅は計画と同じ ATR(M15)×2。1時間足版＝2年の検証と同じ作り（次の正時・ATR(H1)×1）。",
         f"参考：いつも買い＝M15 版 {_p(up15['win'])}、1時間足版 {_p(uph1['win'])}。",
         "**約12週しかないので幅は広い。** 2年の結果（押し目・戻り 前半 53.2%／後半 54.2%）と矛盾しないかを見る。", "",
         "| 決まり | M15 版（計画と同じ幅） | 1時間足版（同じ期間） |", "|---|---|---|"]
    out = {}
    for key in CHECK:
        kind, label, fn = W.RULES[key]
        a, b = summarize(rows_m15, fn), summarize(rows_h1, fn)
        out[key] = (a, b)
        L.append(f"| {label} | {cell(a)} | {cell(b)} |")
    fn = W.RULES["d_trend_pullback"][2]
    groups = {"jpy": summarize([r for r in rows_m15 if "JPY" in r["pair"]], fn),
              "other": summarize([r for r in rows_m15 if "JPY" not in r["pair"]], fn)}
    L += ["", "## 押し目・戻り：円がらみ／円以外（M15 版）", "", "| 区分 | 結果 |", "|---|---|",
          f"| 円がらみ | {cell(groups['jpy'])} |", f"| 円以外 | {cell(groups['other'])} |"]
    L += ["", "## 押し目・戻り：時間帯ごと（M15 版）", "", "| 時間帯 | 結果 |", "|---|---|"]
    for ses, ja in ((sessions.TOKYO, "東京"), (sessions.LONDON, "ロンドン"), (sessions.NEWYORK, "NY")):
        L.append(f"| {ja} | {cell(summarize([r for r in rows_m15 if r['session'] == ses], fn))} |")
    L += ["", "## 押し目・戻り：銘柄ごと（M15 版・件数の多い順）", "", "| 銘柄 | 結果 |", "|---|---|"]
    per = [(n, summarize([r for r in rows_m15 if r["pair"] == n], fn)) for n in names]
    for n, e in sorted(per, key=lambda x: -x[1]["n"]):
        if e["n"]:
            L.append(f"| {n} | {e['n']}件・{_p(e['win'])} |")
    path = ROOT / "logs" / "replay" / f"win-m15-{datetime.now(JST):%Y-%m-%d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps(
        {"first": str(first), "last": str(last), "pullback_groups": groups,
         "pullback": out["d_trend_pullback"][0]}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(L))
    print(f"\n書きました: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
