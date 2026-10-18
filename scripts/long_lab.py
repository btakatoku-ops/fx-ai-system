# -*- coding: utf-8 -*-
"""約2年の1時間足で、決まりを前半で見つけ、後半で確かめる（backend/app/long_lab.py）。

    .venv/Scripts/python.exe scripts/fetch_long.py
    .venv/Scripts/python.exe scripts/fetch_drivers.py --out data/long --range 730d
    .venv/Scripts/python.exe scripts/long_lab.py

結果は logs/replay/long-<日付>.md。**過去の数え上げで、予測ではない。**
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import long_lab as LL                                 # noqa: E402
from app import signal_lab as S                                # noqa: E402
from app.config import get_trading_config                      # noqa: E402
from app.market_data import CSVMarketDataProvider              # noqa: E402

JST = timezone(timedelta(hours=9))
LONG = ROOT / "data" / "long"


def _r(x):
    return "—" if x is None else f"{x:+.2f}"


def _pct(x):
    return "—" if x is None else f"{x * 100:.0f}%"


def cell(e: Dict) -> str:
    if not e["n"]:
        return "—"
    ci = "" if e["r_lo"] is None else f"［{e['r_lo']:+.2f}〜{e['r_hi']:+.2f}］"
    return (f"{e['n']}件・{_r(e['avg_r'])}{ci}・差 {_r(e['edge'])}・的中 {_pct(e['dir'])}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    cfg = get_trading_config()
    prov = CSVMarketDataProvider(LONG)
    pairs = {}
    for p in sorted(x.name.split("_")[0] for x in LONG.glob("*_H1.csv")
                    if x.name.split("_")[0] not in ("DXY", "US10Y", "GOLD", "VIX", "SPX")):
        c = prov.get_candles(p, "H1", limit=10 ** 6).candles
        pairs[p] = S.from_candles(c, 60)
    drivers = {n: S.load_driver(n, LONG) for n in ("DXY", "US10Y", "VIX")}
    first = min(s.ts[0] for s in pairs.values()).date() + timedelta(days=20)
    last = max(s.ts[-1] for s in pairs.values()).date()
    rows = LL.run(cfg, pairs, drivers, LL.decision_times(cfg, first, last))

    L: List[str] = [f"# 約2年の1時間足で、前半で見つけ・後半で確かめる（{first}〜{last}）", "",
                    f"銘柄 {len(pairs)}・判断の時刻 {len(rows)} 件。前半＝〜{LL.SPLIT.date()}、後半＝それ以降。",
                    "各欄：件数・平均 R［95%の幅］・同じ時刻に向きを問わず建てた場合との差・向きの的中（刻限まで）。",
                    "**前半で差がプラスでも、後半でプラスでなければ偶然。** 平均 R の95%の幅が0より上なら、"
                    "費用込みでプラスと言える。1時間足での近似は不利な側に寄せてある（足の中で両方に触れたら損切り）。", ""]
    for m in (1.5, 3.0):
        b1 = LL.baseline([r for r in rows if datetime.fromisoformat(r["at"]) < LL.SPLIT], m)
        b2 = LL.baseline([r for r in rows if datetime.fromisoformat(r["at"]) >= LL.SPLIT], m)
        L += [f"## 利確＝損切りの {m:g} 倍{'（いまの計画）' if m == 1.5 else '（遠くに置いた場合）'}", "",
              f"向きを問わず：前半 {b1['n']}件・{_r(b1['avg_r'])}、後半 {b2['n']}件・{_r(b2['avg_r'])}（費用の重さ）", "",
              "| 決まり | 前半（見つける） | 後半（確かめる） | 判定 |", "|---|---|---|---|"]
        for key, (label, fn) in LL.RULES.items():
            e = LL.evaluate(rows, fn, m)
            a, b = e["前半"], e["後半"]
            ok = (a["edge"] or -1) > 0 and (b["edge"] or -1) > 0
            plus = b["r_lo"] is not None and b["r_lo"] > 0
            mark = "◎ 費用込みでプラス" if ok and plus else "○ 両方で上回る" if ok else ""
            L.append(f"| {label} | {cell(a)} | {cell(b)} | {mark} |")
        L.append("")
    L += ["## 値動きの大きさ（ATR(H1) ÷ 20日の中央値）と費用の重さ（向きを問わず、利確1.5倍）", "",
          "| 区分 | 前半 | 後半 |", "|---|---|---|"]
    for lab, cond in (("0.8倍未満", lambda r: r["vol"] is not None and r["vol"] < 0.8),
                      ("0.8〜1.2倍", lambda r: r["vol"] is not None and 0.8 <= r["vol"] < 1.2),
                      ("1.2倍以上", lambda r: r["vol"] is not None and r["vol"] >= 1.2)):
        a = LL.baseline([r for r in rows if datetime.fromisoformat(r["at"]) < LL.SPLIT], 1.5, cond)
        b = LL.baseline([r for r in rows if datetime.fromisoformat(r["at"]) >= LL.SPLIT], 1.5, cond)
        L.append(f"| {lab} | {a['n']}件・{_r(a['avg_r'])} | {b['n']}件・{_r(b['avg_r'])} |")
    out = ROOT / "logs" / "replay" / f"long-{datetime.now(JST):%Y-%m-%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    results = {key: {str(m): LL.evaluate(rows, fn, m) for m in (1.5, 3.0)}
               for key, (label, fn) in LL.RULES.items()}
    out.with_suffix(".json").write_text(json.dumps(
        {"first": str(first), "last": str(last), "split": LL.SPLIT.date().isoformat(),
         "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(L))
    print(f"\n書きました: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
