# -*- coding: utf-8 -*-
"""この先どれだけ動きうるかを、実データから測る。

**向きは予想しない。** 優位性が測れていない道具で「この先上がる」と線を
引けば、それは作り話になる。測れるのは**幅**のほうだけ。

やること。

1. 実データの足から、判断時点の ATR に対して「h 本先までに終値がどれだけ
   動いたか」の比を全部集める。
2. 期間の**前半**でその比の分位（既定80%）を出す。これが倍率になる。
3. **後半で、その倍率が本当に8割を覆うかを確かめる。** 同じ期間で決めて
   同じ期間で確かめると、当たって当然なので意味がない。
4. 倍率と、確かめた結果を ``config/forecast.json`` に書く。

画面はこの倍率を使って幅を描き、**測った覆い率をそのまま併記する**。
「80%のはず」ではなく「実際に測ったら◯%だった」と出すため。

使い方::

    .venv/Scripts/python.exe scripts/measure_forecast.py
    .venv/Scripts/python.exe scripts/measure_forecast.py --timeframe H1
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_trading_config          # noqa: E402
from app.indicator_engine import atr               # noqa: E402

HORIZONS = [1, 2, 4, 8, 16, 32]
WARMUP = 60


def read_csv(path: Path) -> Tuple[List[datetime], List[float], List[float],
                                  List[float]]:
    """時刻・高値・安値・終値。**engine と同じ ATR を使うために生の足で返す。**"""
    times: List[datetime] = []
    highs: List[float] = []
    lows: List[float] = []
    closes: List[float] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                times.append(datetime.fromisoformat(row["timestamp"]))
                highs.append(float(row["high"]))
                lows.append(float(row["low"]))
                closes.append(float(row["close"]))
            except (KeyError, ValueError):
                continue
    return times, highs, lows, closes


def ratios_for(path: Path, period: int
               ) -> Dict[int, List[Tuple[datetime, float]]]:
    """h 本先までの値動きを、その時点の ATR で割った比。"""
    times, highs, lows, closes = read_csv(path)
    if len(closes) < WARMUP + max(HORIZONS) + period:
        return {}
    atr_series = atr(highs, lows, closes, period)
    out: Dict[int, List[Tuple[datetime, float]]] = {h: [] for h in HORIZONS}
    for i in range(WARMUP, len(closes) - max(HORIZONS)):
        a = atr_series[i]
        if a is None or a <= 0:
            continue
        for h in HORIZONS:
            out[h].append((times[i], abs(closes[i + h] - closes[i]) / a))
    return out


def quantile(values: Sequence[float], q: float) -> Optional[float]:
    """素朴な分位。**件数が少なければ返さない。**"""
    xs = sorted(values)
    if len(xs) < 100:
        return None
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--timeframe", default="M15")
    ap.add_argument("--data", default="data/real")
    ap.add_argument("--quantile", type=float, default=0.8)
    ap.add_argument("--out", default="config/forecast.json")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    cfg = get_trading_config()
    period = int(cfg.indicators["atr"]["period"])
    directory = ROOT / a.data
    if not directory.is_dir():
        print(f"足の置き場がありません: {directory}")
        return 1

    # 銘柄をまたいで集める。**銘柄ごとに別の倍率は作らない。**
    # 26銘柄ぶんの倍率を別々に決めると、それぞれの件数が減って、
    # たまたまの値をそのまま倍率にしてしまう。
    pooled: Dict[int, List[Tuple[datetime, float]]] = {h: [] for h in HORIZONS}
    pairs = 0
    for spec in cfg.enabled_pairs():
        path = directory / f"{spec.symbol}_{a.timeframe}.csv"
        if not path.exists():
            continue
        got = ratios_for(path, period)
        if not got:
            print(f"  {spec.symbol}: 足が足りません")
            continue
        pairs += 1
        for h in HORIZONS:
            pooled[h].extend(got[h])
    if not pairs:
        print("使える銘柄がありません")
        return 1

    # 期間で前半・後半に分ける。**同じ期間で決めて同じ期間で確かめない。**
    all_times = sorted(t for h in HORIZONS for t, _ in pooled[h])
    split = all_times[len(all_times) // 2]
    print(f"銘柄 {pairs} 件 / {a.timeframe} / 分割点 {split:%Y-%m-%d %H:%M}")

    result: Dict[str, Dict] = {}
    for h in HORIZONS:
        first = [v for t, v in pooled[h] if t < split]
        second = [v for t, v in pooled[h] if t >= split]
        mult = quantile(first, a.quantile)
        if mult is None:
            print(f"  {h:>2}本先: 件数が足りず、倍率を決めません")
            continue
        covered = sum(1 for v in second if v <= mult)
        rate = covered / len(second) if second else None
        result[str(h)] = {
            "atr_multiple": round(mult, 3),
            "fitted_on": len(first),
            "checked_on": len(second),
            "measured_coverage": round(rate, 4) if rate is not None else None,
            "median_ratio": round(statistics.median(first), 3),
        }
        print(f"  {h:>2}本先: ATR×{mult:5.2f}  "
              f"（前半 {len(first):,} 件で決定 / "
              f"後半 {len(second):,} 件のうち {rate * 100:.1f}% を覆った）")

    payload = {
        "_comment": [
            "この先どれだけ動きうるかの幅。**向きは予想していない。**",
            "実データから測った値で、scripts/measure_forecast.py が書き出す。",
            "atr_multiple は『判断時点の ATR の何倍まで動いたか』の分位。",
            "measured_coverage は、**決めた期間とは別の期間で確かめた**覆い率。",
            "画面にはこの覆い率をそのまま出す。「80%のはず」ではなく",
            "「測ったら◯%だった」と言うため。",
        ],
        "_not_a_forecast": "向きの予想ではない。上下どちらに動くかは何も言っていない。優位性は測れていない（docs/backtesting.md）。",
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "timeframe": a.timeframe,
        "quantile": a.quantile,
        "atr_period": period,
        "pairs": pairs,
        "source": str(a.data),
        "horizons": result,
    }
    out = ROOT / a.out
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n書き出しました: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
