# -*- coding: utf-8 -*-
"""戦略を1つずつ、実データで測る。

**戦略を増やせば強くなる、ということは無い。** 順に試して最初に出た向きを
採るので、増やすほど「どれかが偶然当てはまる」回数が増える。1件あたりの
期待値が負なら、件数が増えるぶんだけ負けが増える。

だから、まとめた成績では判断しない。**戦略ごとに分けて、同じ時刻の
コイン投げ（対照群）と比べる。**

使い方::

    set FX_CSV_DATA_DIR=./data/real
    set FX_MARKET_DATA_PROVIDER=csv
    .venv/Scripts/python.exe scripts/measure_strategies.py --out out.json

``--out`` に書いた記録から、あとで集計だけやり直せる（走行は重い）。
``--from`` を渡すと、走行せずにその記録を読むだけにする。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.backtester import run_pair                                # noqa: E402
from app.config import get_settings, get_trading_config            # noqa: E402
from app.market_data import build_provider                         # noqa: E402
from app.performance import (                                      # noqa: E402
    two_proportion_z,
    two_sided_p,
    wilson,
)

MIN_SAMPLES = 30


def collect(out: Path) -> Dict:
    """全銘柄を走らせ、1件ずつの記録を書き出す。"""
    cfg = get_trading_config()
    provider = build_provider(get_settings())
    bt = dict(cfg.backtest)
    rows: List[Dict] = []
    decisions = 0
    for spec in cfg.enabled_pairs():
        try:
            pr = run_pair(spec.symbol, provider, cfg, bt)
        except Exception as exc:                      # noqa: BLE001
            print(f"{spec.symbol}: 検証できません（{exc}）", flush=True)
            continue
        decisions += pr.decisions
        for group, items in (("taken", pr.trades),
                             ("observed", pr.observations),
                             ("control", pr.controls)):
            for t in items:
                rows.append({
                    "group": group, "pair": t.pair, "regime": t.regime,
                    "strategy": t.strategy, "signal": t.signal,
                    "direction": t.direction, "score": t.score,
                    "outcome": t.outcome, "r": t.r_multiple,
                    "hard_blocked": t.hard_blocked,
                    "decided_at": t.decided_at.isoformat(),
                })
        print(f"{spec.symbol}: 判断 {pr.decisions} / 建玉 {len(pr.trades)}",
              flush=True)
    payload = {"decisions": decisions, "rows": rows}
    out.write_text(json.dumps(payload), encoding="utf-8")
    print(f"\n書き出しました: {out}（{len(rows)} 行）")
    return payload


class Stats:
    def __init__(self, label: str, rows: List[Dict]):
        self.label = label
        self.n = len(rows)
        self.wins = sum(1 for r in rows if r["outcome"] == "WIN")
        self.losses = sum(1 for r in rows if r["outcome"] == "LOSS")
        self.decided = self.wins + self.losses
        self.avg_r = sum(r["r"] for r in rows) / self.n if self.n else 0.0
        self.lo, self.hi = (wilson(self.wins, self.decided)
                            if self.decided else (0.0, 1.0))

    def line(self) -> str:
        rate = (f"{self.wins / self.decided * 100:5.1f}%"
                if self.decided >= MIN_SAMPLES else "  —  ")
        return (f"{self.label:<22} n={self.n:<5} 決着={self.decided:<5} "
                f"勝率={rate} [{self.lo * 100:4.1f}, {self.hi * 100:4.1f}] "
                f"平均R={self.avg_r:+.3f}")


def versus(a: Stats, b: Stats) -> str:
    """2つの勝率を比べる。**件数が足りなければ数字を出さない。**"""
    z = two_proportion_z(a.wins, a.decided, b.wins, b.decided)
    if z is None:
        return (f"    {a.label} vs {b.label}: 件数が足りず、検定はしません"
                f"（{a.decided} / {b.decided} 件）")
    diff = a.wins / a.decided - b.wins / b.decided
    return (f"    {a.label} vs {b.label}: z={z:+.2f} p={two_sided_p(z):.3f} "
            f"勝率差 {diff * 100:+.1f}pt / 平均R差 {a.avg_r - b.avg_r:+.3f}")


def report(payload: Dict) -> None:
    rows = payload["rows"]
    taken = [r for r in rows if r["group"] == "taken"]
    obs = [r for r in rows if r["group"] == "observed"]
    ctrl = [r for r in rows if r["group"] == "control"]

    print(f"\n判断 {payload['decisions']} 件 / 建玉 {len(taken)} 件 "
          f"/ 観察 {len(obs)} 件")
    print("\n===== 戦略ごと（実際に建てた玉）=====")
    names = sorted({r["strategy"] for r in taken if r["strategy"]})
    if not names:
        print("  建てた玉がありません")
    for name in names:
        mine = [r for r in taken if r["strategy"] == name]
        same = [r for r in ctrl
                if r["strategy"] == name and not r["hard_blocked"]]
        print("  " + Stats(name, mine).line())
        print("  " + Stats(f"（同時刻のコイン投げ）", same).line())
        print(versus(Stats(name, mine), Stats("コイン投げ", same)))

    print("\n===== 戦略ごと（建てなかったぶんも含む・向きが出た全場面）=====")
    print(" **建てた玉だけ見ると、点数で絞った後の姿しか分からない。**")
    everything = taken + obs
    for name in sorted({r["strategy"] for r in everything if r["strategy"]}):
        mine = [r for r in everything
                if r["strategy"] == name and not r["hard_blocked"]]
        same = [r for r in ctrl
                if r["strategy"] == name and not r["hard_blocked"]]
        print("  " + Stats(name, mine).line())
        print("  " + Stats("（同時刻のコイン投げ）", same).line())
        print(versus(Stats(name, mine), Stats("コイン投げ", same)))

    print("\n===== 向きが出た場面のうち、強制条件で消えた割合 =====")
    for name in sorted({r["strategy"] for r in everything if r["strategy"]}):
        mine = [r for r in everything if r["strategy"] == name]
        blocked = sum(1 for r in mine if r["hard_blocked"])
        pct = blocked / len(mine) * 100 if mine else 0.0
        print(f"  {name:<18} {blocked:>5} / {len(mine):<5}（{pct:.0f}%）")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="strategy_trades.json",
                    help="1件ずつの記録の書き出し先")
    ap.add_argument("--from", dest="src", default=None,
                    help="走行せずに、この記録を読んで集計だけやり直す")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    if a.src:
        payload = json.loads(Path(a.src).read_text(encoding="utf-8"))
    else:
        payload = collect(Path(a.out))
    report(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
