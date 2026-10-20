# -*- coding: utf-8 -*-
"""勝率だけを測る検証（backend/app/win_lab.py）。利確＝損切り・費用なしで、根拠が無ければ 50%。

    .venv/Scripts/python.exe scripts/win_lab.py

結果は logs/replay/win-<日付>.md と .json。**過去の数え上げで、予測ではない。**
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
from app import win_lab as W                                   # noqa: E402
from app.config import get_trading_config                      # noqa: E402
from app.market_data import CSVMarketDataProvider              # noqa: E402

JST = timezone(timedelta(hours=9))
LONG = ROOT / "data" / "long"
SESSION_JA = {"tokyo": "東京", "london": "ロンドン", "newyork": "NY"}


def _p(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def cell(e: Dict) -> str:
    if not e["n"]:
        return "—"
    ci = "" if e["lo"] is None else f"［{e['lo'] * 100:.0f}〜{e['hi'] * 100:.0f}］"
    return (f"{e['n']}件・**{_p(e['win'])}**{ci}・買い {_p(e['long'])}／売り {_p(e['short'])}")


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
        pairs[p] = S.from_candles(prov.get_candles(p, "H1", limit=10 ** 6).candles, 60)
    first = min(s.ts[0] for s in pairs.values()).date() + timedelta(days=20)
    last = max(s.ts[-1] for s in pairs.values()).date()
    times = LL.decision_times(cfg, first, last)
    rows: List[Dict] = []
    for p, s in pairs.items():
        rows += W.rows_for(cfg, p, s, times)

    allp = W.evaluate(rows, lambda r: "up")
    L: List[str] = [f"# 勝率だけを測る（{first}〜{last}・1時間足）", "",
                    f"銘柄 {len(pairs)}・判断の時刻 {len(rows)} 件（東京 9時・ロンドン・NY の始まり）。"
                    f"前半＝〜{W.SPLIT.date()}、後半＝それ以降。",
                    "**利確＝損切り（ATR(H1)×1）・費用なし。根拠が無ければ 50%。** 足の中で両方に触れたら 0.5、"
                    "刻限までに触れなければ刻限の終値の向き。",
                    "各欄：件数・勝率［95%の幅・日ごとに数えたもの］・買いだけ／売りだけの勝率。",
                    f"参考：いつも買い＝前半 {_p(allp['前半']['win'])}、後半 {_p(allp['後半']['win'])}"
                    "（相場全体の上げ下げで 50% からずれる分）。",
                    f"**本物の候補＝前半・後半とも {W.PASS * 100:.0f}% 以上で、後半の幅の下側が 50% より上。**", "",
                    "| 種類 | 決まり | 前半（見つける） | 後半（確かめる） | 判定 |", "|---|---|---|---|---|"]
    results = {}
    for key, (kind, label, fn) in W.RULES.items():
        e = W.evaluate(rows, fn)
        results[key] = e
        L.append(f"| {kind} | {label} | {cell(e['前半'])} | {cell(e['後半'])} | {W.verdict(e)} |")

    # 押し目・戻りを円がらみ／円以外に分ける（ボードの表示が読む。円がらみでは効いていない）
    fn_pb = W.RULES["d_trend_pullback"][2]
    groups = {"jpy": W.evaluate([r for r in rows if "JPY" in r["pair"]], fn_pb),
              "other": W.evaluate([r for r in rows if "JPY" not in r["pair"]], fn_pb)}
    L += ["", "## 押し目・戻り：円がらみ／円以外", "", "| 区分 | 前半 | 後半 |", "|---|---|---|",
          f"| 円がらみ | {cell(groups['jpy']['前半'])} | {cell(groups['jpy']['後半'])} |",
          f"| 円以外 | {cell(groups['other']['前半'])} | {cell(groups['other']['後半'])} |"]

    L += ["", "## 時間帯ごと（各決まり・後半）", "",
          "| 決まり | 東京 | ロンドン | NY |", "|---|---|---|---|"]
    for key, (kind, label, fn) in W.RULES.items():
        cells = []
        for ses in ("tokyo", "london", "newyork"):
            e = W.evaluate([r for r in rows if r["session"] == ses], fn)
            a, b = e["前半"], e["後半"]
            cells.append("—" if not b["n"] else f"{_p(a['win'])} → {_p(b['win'])}（{b['n']}件）")
        L.append(f"| {label} | " + " | ".join(cells) + " |")

    L += ["", "## 「一部だけ良い」を前半で選ぶと、後半はどうなるか（銘柄×時間帯）", "",
          f"前半で {W.PASS * 100:.0f}% 以上・60件以上だった組み合わせだけを並べる。**選んだ後の後半の数字が本当の実力。**", "",
          "| 決まり | 銘柄・時間帯 | 前半 | 後半 |", "|---|---|---|---|"]
    picked, held = 0, 0
    for key, (kind, label, fn) in W.RULES.items():
        for g, e in W.subgroups(rows, fn, lambda r: f'{r["pair"]}・{SESSION_JA.get(r["session"], r["session"])}'):
            picked += 1
            b = e["後半"]
            if (b["win"] or 0) >= W.PASS:
                held += 1
            L.append(f"| {label} | {g} | {_p(e['前半']['win'])}（{e['前半']['n']}件） | "
                     f"{_p(b['win'])}（{b['n']}件） |")
    L += ["", f"前半で選ばれた {picked} 組のうち、後半も {W.PASS * 100:.0f}% 以上だったのは {held} 組。", ""]

    out = ROOT / "logs" / "replay" / f"win-{datetime.now(JST):%Y-%m-%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(
        {"first": str(first), "last": str(last), "split": W.SPLIT.date().isoformat(),
         "rows": len(rows), "results": results, "pullback_groups": groups}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(L))
    print(f"\n書きました: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
