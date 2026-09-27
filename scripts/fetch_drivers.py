# -*- coding: utf-8 -*-
"""相関の材料（ドル指数・米債利回り・金・VIX・S&P500）を取り寄せる。

**通貨ペアではないので、供給元の抽象には載せない。** 取り込み専用の道具。
書き出した CSV を correlation.py が読む。

使い方::

    python scripts/fetch_drivers.py --out data/drivers
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

UA = "fx-ai-system/0.1 (personal research; contact via repository owner)"
BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"


def fetch(symbol: str, interval: str, rng: str,
          retries: int = 4) -> Optional[List[Dict]]:
    url = (f"{BASE}{urllib.parse.quote(symbol, safe='')}"
           f"?interval={interval}&range={rng}&includePrePost=false")
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as r:
                payload = json.load(r)
            break
        except Exception as exc:
            last = exc
            time.sleep(2.0 * (attempt + 1))
    else:
        print(f"  取得できません: {last}", file=sys.stderr)
        return None

    try:
        res = payload["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        return None

    rows: List[Dict] = []
    for i in range(len(ts)):
        c = q["close"][i]
        if c is None or not (c == c) or c <= 0:      # None / NaN / 非正
            continue
        rows.append({"timestamp": datetime.fromtimestamp(ts[i], timezone.utc),
                     "close": float(c)})
    rows.sort(key=lambda r: r["timestamp"])
    dedup = {r["timestamp"]: r for r in rows}
    return [dedup[k] for k in sorted(dedup)]


def drop_unclosed(rows: List[Dict], minutes: int) -> List[Dict]:
    """進行中の足を落とす。通貨ペアと同じ扱いにそろえる。"""
    if len(rows) < 2:
        return rows
    now = datetime.now(timezone.utc)
    if rows[-1]["timestamp"] + timedelta(minutes=minutes) > now:
        return rows[:-1]
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    from app.config import get_trading_config

    cfg = get_trading_config()
    drivers = cfg.correlation["drivers"]

    ap = argparse.ArgumentParser(description="相関の材料を取り寄せる")
    ap.add_argument("--out", default="data/drivers")
    ap.add_argument("--range", default="60d", dest="rng")
    ap.add_argument("--pause", type=float, default=1.2)
    a = ap.parse_args(argv)

    tf = cfg.correlation["timeframe"]
    minutes = cfg.timeframe_minutes(tf)
    interval = {60: "1h", 15: "15m", 5: "5m"}.get(minutes)
    if not interval:
        raise SystemExit(f"{tf} に対応する Yahoo の interval がありません")

    out_dir = Path(a.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    ok, ng = [], []
    for name, spec in drivers.items():
        print(f"{name} ({spec['label']}) ...", end=" ")
        rows = fetch(spec["symbol"], interval, a.rng)
        time.sleep(a.pause)
        if not rows:
            print("失敗")
            ng.append(name)
            continue
        rows = drop_unclosed(rows, minutes)
        path = out_dir / f"{name}_{tf}.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "close"])
            for r in rows:
                w.writerow([r["timestamp"].isoformat(), f"{r['close']:.6f}"])
        print(f"{len(rows)}本 "
              f"{rows[0]['timestamp'].date()}〜{rows[-1]['timestamp'].date()}")
        ok.append(name)

    print()
    print(f"取得できた材料: {len(ok)} / {len(drivers)}")
    if ng:
        print(f"取得できなかった材料: {', '.join(ng)}")
    if ok:
        print()
        print("材料が揃ったので、config/signal_weights.json の")
        print('  data_available.correlation を true にすると配点に入ります。')
        print("（材料が足りない銘柄は、その銘柄だけ UNKNOWN のままになります）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
