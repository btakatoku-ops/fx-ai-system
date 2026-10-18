# -*- coding: utf-8 -*-
"""数日〜数週間持つ型を、約2年の1時間足で測る（backend/app/swing_lab.py）。**測るだけ。**

    .venv/Scripts/python.exe scripts/swing_lab.py

結果は logs/replay/swing-<日付>.md。先に scripts/fetch_long.py で data/long を用意する。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import signal_lab as S                                # noqa: E402
from app import swing_lab as W                                 # noqa: E402
from app.config import get_trading_config                      # noqa: E402
from app.market_data import CSVMarketDataProvider              # noqa: E402

JST = timezone(timedelta(hours=9))
LONG = ROOT / "data" / "long"


def _r(x):
    return "—" if x is None else f"{x:+.2f}"


def cell(s):
    if not s.get("n"):
        return "—"
    return (f"{s['n']}件・{_r(s['avg_r'])}［{_r(s['r_lo'])}〜{_r(s['r_hi'])}］・"
            f"プラス {s['pos'] * 100:.0f}%・スワップ {_r(s['swap_r'])}")


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
    # 円以外が決済通貨の銘柄のスワップを円から直すための換算（手元の最新の終値）
    real = CSVMarketDataProvider(ROOT / "data" / "real")
    qj = {}
    for ccy in ("USD", "EUR", "GBP", "CHF", "CAD", "AUD", "NZD"):
        try:
            qj[ccy] = float(real.get_candles(f"{ccy}JPY", "H1", limit=1).candles[-1].close)
        except Exception:                          # noqa: BLE001
            pass
    res = W.run(cfg, pairs, qj)

    L: List[str] = ["# 数日〜数週間持つ型（約2年の1時間足・前半で見つけ後半で確かめる）", "",
                    f"銘柄 {len(pairs)}。損切り＝日足 ATR×2。各欄：件数・平均 R［95%の幅］・プラスで終わった割合・"
                    "平均のスワップ（R）。**スワップは今の値を過去に当てている。** 同じ銘柄で玉を持つ間は次を見送る。",
                    "**後半の95%の幅が0より上**なら、費用・スワップ込みでプラスと言える。", ""]
    labels = ["基準：向きを問わず"] + [lab for lab, _ in W.RULES.values()]
    for tgt in (None, 3.0):
        L += [f"## {'利確なし（N日後の終値で閉じる）' if tgt is None else '利確＝損切りの3倍'}", ""]
        for hold in W.HOLDS:
            L += [f"### {hold}日持つ", "", "| 決まり | 前半（〜2025-06） | 後半（2025-07〜） | 判定 |", "|---|---|---|---|"]
            for lab in labels:
                d = res.get((lab, hold, tgt), {})
                a, b = W.stats(d.get("前半", [])), W.stats(d.get("後半", []))
                ok = a.get("n") and b.get("n") and a["avg_r"] > 0 and b["avg_r"] > 0
                strong = ok and b["r_lo"] is not None and b["r_lo"] > 0 and a["r_lo"] > 0
                mark = "◎ 前後とも費用込みでプラス（95%）" if strong else "○ 前後ともプラス" if ok else ""
                L.append(f"| {lab} | {cell(a)} | {cell(b)} | {mark} |")
            L.append("")
    out = ROOT / "logs" / "replay" / f"swing-{datetime.now(JST):%Y-%m-%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    import json
    summary = {f"{lab}|{hold}|{tgt}": {h: W.stats(res.get((lab, hold, tgt), {}).get(h, []))
                                       for h in ("前半", "後半")}
               for lab in labels for hold in W.HOLDS for tgt in (None, 3.0)}
    out.with_suffix(".json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(L))
    print(f"\n書きました: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
