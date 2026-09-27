# -*- coding: utf-8 -*-
"""どの銘柄なら支援する値打ちがあるかを、実データから決める。

**26銘柄すべてを同じ顔で並べるのをやめる。** 実測では、向きが出た場面の
77〜100% がスプレッドの強制条件で消えていた。費用が見合わない銘柄を
並べておくと、画面は賑やかだが**出るのは見送りばかり**になる。

## 何で決めるか

ひとつだけ。**費用が、狙う値幅に対してどれだけ重いか。**

    比 = スプレッド(pips) ÷ M15 の ATR(pips)

この比が大きいほど、当たっても費用で持っていかれる。強制条件の上限は
0.25（``config/filters.json`` の ``spread.max_ratio_of_atr``）なので、
**普段からそこに張り付いている銘柄は、そもそも出番が来ない。**

勝てるかどうかは見ていない。**それは測れていない**（docs/backtesting.md）。
ここで決めるのは「費用の面で土俵に乗るか」だけ。

## 出すもの

``config/focus.json``。銘柄ごとに

- ``ratio_median`` : 比の中央値
- ``pass_rate``    : 強制条件の上限を通る足の割合
- ``tier``         : ``focus``（支援する）/ ``watch`` / ``off``

使い方::

    .venv/Scripts/python.exe scripts/measure_pair_value.py
    .venv/Scripts/python.exe scripts/measure_pair_value.py --min-pass-rate 0.7
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_trading_config          # noqa: E402
from app.indicator_engine import atr               # noqa: E402

WARMUP = 60


def read_bars(path: Path):
    highs: List[float] = []
    lows: List[float] = []
    closes: List[float] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                highs.append(float(row["high"]))
                lows.append(float(row["low"]))
                closes.append(float(row["close"]))
            except (KeyError, ValueError):
                continue
    return highs, lows, closes


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="data/real")
    ap.add_argument("--timeframe", default="M15")
    ap.add_argument("--min-pass-rate", type=float, default=0.70,
                    help="focus にする下限。強制条件を通る足の割合")
    ap.add_argument("--watch-pass-rate", type=float, default=0.40,
                    help="watch にする下限。これ未満は off")
    ap.add_argument("--out", default="config/focus.json")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    cfg = get_trading_config()
    period = int(cfg.indicators["atr"]["period"])
    limit = float(cfg.filters["spread"]["max_ratio_of_atr"])
    directory = ROOT / a.data
    if not directory.is_dir():
        print(f"足の置き場がありません: {directory}")
        return 1

    rows: List[Dict] = []
    for spec in cfg.enabled_pairs():
        path = directory / f"{spec.symbol}_{a.timeframe}.csv"
        if not path.exists():
            print(f"  {spec.symbol}: 足がありません")
            continue
        highs, lows, closes = read_bars(path)
        if len(closes) < WARMUP + period:
            print(f"  {spec.symbol}: 足が足りません（{len(closes)} 本）")
            continue

        # **業者の表どおりの値を使う。** ここで平均などを作ると、
        # engine が見ているスプレッドと画面の判断がずれる。
        #
        # 区分を決めるのは**普段のスプレッド（narrow）**。理由は単純で、
        # engine が普段見ているのがそれだから（CSV の供給元は既定で
        # narrow）。広がったときの値で区分を決めると、engine は通すのに
        # 画面は「対象外」と言う、という食い違いになる。
        #
        # 広がったとき（wide）は別の列で出す。**指標発表前後にどれだけ
        # 重くなるかは、知っておく価値がある。**
        narrow_pips = cfg.spread_price(spec.symbol, wide=False) / spec.pip
        wide_pips = cfg.spread_price(spec.symbol, wide=True) / spec.pip
        spread_pips = narrow_pips

        atr_series = atr(highs, lows, closes, period)
        ratios: List[float] = []
        for i in range(WARMUP, len(closes)):
            a_val = atr_series[i]
            if a_val is None or a_val <= 0:
                continue
            atr_pips = a_val / spec.pip
            if atr_pips <= 0:
                continue
            ratios.append(spread_pips / atr_pips)
        if len(ratios) < 100:
            print(f"  {spec.symbol}: 測れる足が少なすぎます")
            continue

        passed = sum(1 for r in ratios if r <= limit)
        atr_median = statistics.median([spread_pips / r for r in ratios])
        rows.append({
            "pair": spec.symbol,
            "spread_pips": round(narrow_pips, 2),
            "spread_pips_wide": round(wide_pips, 2),
            "atr_pips_median": round(atr_median, 2),
            "ratio_median": round(statistics.median(ratios), 4),
            "ratio_median_wide": round(wide_pips / atr_median, 4),
            "pass_rate": round(passed / len(ratios), 4),
            "bars": len(ratios),
        })

    if not rows:
        print("測れた銘柄がありません")
        return 1

    rows.sort(key=lambda r: (-r["pass_rate"], r["ratio_median"]))
    for r in rows:
        if r["pass_rate"] >= a.min_pass_rate:
            r["tier"] = "focus"
        elif r["pass_rate"] >= a.watch_pass_rate:
            r["tier"] = "watch"
        else:
            r["tier"] = "off"

    print(f"\n{'銘柄':<9}{'普段':>7}{'広がり':>8}{'ATR':>7}"
          f"{'比(普段)':>10}{'比(広)':>9}{'通る割合':>10}  区分")
    for r in rows:
        mark = {"focus": "◎ 支援する", "watch": "○ 様子を見る",
                "off": "× 対象外"}[r["tier"]]
        print(f"{r['pair']:<9}{r['spread_pips']:>7.1f}"
              f"{r['spread_pips_wide']:>8.1f}{r['atr_pips_median']:>7.1f}"
              f"{r['ratio_median']:>10.2f}{r['ratio_median_wide']:>9.2f}"
              f"{r['pass_rate'] * 100:>9.1f}%  {mark}")

    counts = {t: sum(1 for r in rows if r["tier"] == t)
              for t in ("focus", "watch", "off")}
    print(f"\n支援する {counts['focus']} / 様子を見る {counts['watch']} "
          f"/ 対象外 {counts['off']}")

    payload = {
        "_comment": [
            "どの銘柄を支援するか。**実データから測って決める。**",
            "決め手は費用だけ: スプレッド ÷ M15 の ATR。",
            "強制条件の上限（filters.json の spread.max_ratio_of_atr）を",
            "普段から超えている銘柄は、そもそも出番が来ない。",
            "**勝てるかどうかは見ていない。それは測れていない。**",
            "scripts/measure_pair_value.py が書き出す。手で編集しない。",
        ],
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "timeframe": a.timeframe,
        "source": str(a.data),
        "spread_limit_ratio": limit,
        "spread_basis": "narrow",
        "_spread_basis": "区分は普段のスプレッドで決める。engine が普段見ているのがそれだから。広がったときの比は ratio_median_wide に別で出してある。",
        "thresholds": {"focus": a.min_pass_rate, "watch": a.watch_pass_rate},
        "pairs": {r["pair"]: {k: v for k, v in r.items() if k != "pair"}
                  for r in rows},
    }
    out = ROOT / a.out
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"書き出しました: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
