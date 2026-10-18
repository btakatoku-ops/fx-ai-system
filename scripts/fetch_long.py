# -*- coding: utf-8 -*-
"""検証用に、1時間足を約2年ぶん取り寄せる（data/long/。**画面や判断では使わない**）。

手元の取り込み（data/real）は M5・M15 が約60日しかなく、再生の件数が足りない。Yahoo の
1時間足は約730日まで取れるので、検証（scripts/long_lab.py）はこちらで行う。

    .venv/Scripts/python.exe scripts/fetch_long.py                 # ダッシュボードの銘柄
    .venv/Scripts/python.exe scripts/fetch_drivers.py --out data/long --range 730d   # 相関の材料

取り込みの作法は fetch_yahoo.py と同じ（名乗る・間を空ける・未確定の足を落とす）。
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.parse
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_yahoo as FY                                       # noqa: E402
from app import daily_pick                                     # noqa: E402
from app.config import get_trading_config                      # noqa: E402


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pairs", help="カンマ区切り（省略時はダッシュボードの銘柄）")
    ap.add_argument("--out", default="data/long")
    ap.add_argument("--range", default="730d", dest="rng")
    ap.add_argument("--pause", type=float, default=1.5)
    a = ap.parse_args(argv)
    cfg = get_trading_config()
    pairs = (a.pairs.split(",") if a.pairs else
             daily_pick.settings(cfg)["jpy"] + daily_pick.candidates(cfg))
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    ok = 0
    for p in pairs:
        sym = urllib.parse.quote(FY.yahoo_symbol(p), safe="")
        payload = FY.get_json(f"{FY.BASE}{sym}?interval=1h&range={a.rng}&includePrePost=false")
        time.sleep(a.pause)
        if payload is None:
            print(f"  {p}: 取得失敗")
            continue
        rows = FY.parse_chart(payload)
        rows, _ = FY.drop_misaligned(rows, 60)
        rows, _ = FY.drop_incomplete(rows, 60)
        if not rows:
            print(f"  {p}: 足が0本")
            continue
        FY.write_csv(out / f"{p}_H1.csv", rows)
        ok += 1
        print(f"  {p}: {len(rows)}本 {rows[0]['timestamp'].date()}〜{rows[-1]['timestamp'].date()}")
    print(f"揃った銘柄 {ok} / {len(pairs)} 件")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
