# -*- coding: utf-8 -*-
"""残したボードの採点を、時間帯ごとに文字で並べる。

``scripts/score_boards.py``（これまでの全部）と ``scripts/weekly_report.py``
（1週間ぶん）が同じ書き方で出すためのもの。

確かめたいのは3つだけ。

1. **材料がそろった（▲か▼）ときは、その向きに動いたか。** コイン投げなら半分。
2. **ブリーフの方向判定は、その向きに動いたか。**
3. **除外したときは、荒れていたか。**

**件数が少ないうちは結論を出さない。** 30件に届かない区分は割合を名乗らない。
**予測はしない。** 並べるのは、残したボードのあとに相場がどう動いたかだけ。
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence

from .performance import wilson
from .sessions import LABEL, SESSIONS

MIN_SAMPLES = 30

SEGMENT = {"tokyo": "記録 → ロンドンの始まり",
           "london": "記録 → ニューヨークの始まり",
           "newyork": "記録 → 刻限（01:00）"}


def _raw(r: Dict, key: str) -> float:
    """向きをそろえる前の値動き（上が正）。"""
    m = r.get(key) or 0
    return -m if r.get("lean") == "down" else m


def _hit_line(label: str, rs: Sequence[Dict], hit: Callable[[Dict], bool],
              key: str, unit: str = "件") -> List[str]:
    if not rs:
        return [f"  {label}: まだありません"]
    hits = sum(1 for r in rs if hit(r))
    lo, hi = wilson(hits, len(rs))
    avg = sum(r.get(key) or 0 for r in rs) / len(rs)
    rate = f"{hits / len(rs) * 100:.0f}%" if len(rs) >= MIN_SAMPLES else "—"
    out = [f"  {label}: {len(rs)} {unit} / その向きに動いた {hits} {unit}（{rate}、"
           f"95%区間 {lo * 100:.0f}〜{hi * 100:.0f}%）/ 平均 {avg:+.2f} ADR"]
    if len(rs) < MIN_SAMPLES:
        out.append(f"    **{MIN_SAMPLES} {unit}に届くまで、割合は名乗りません。**"
                   f"コイン投げなら50%です")
    return out


def _swing(rs: Sequence[Dict], best: str, worst: str) -> Optional[float]:
    vals = [abs(r.get(best) or 0) + abs(r.get(worst) or 0) for r in rs]
    return sum(vals) / len(vals) if vals else None


def block(rs: Sequence[Dict], *, move: str, best: str, worst: str,
          unit: str = "件") -> List[str]:
    """1つの区分（時間帯など）の3つの問い。``move`` などは使う列の名前。"""
    done = [r for r in rs if r.get(move) is not None]
    out: List[str] = []
    if not done:
        return ["  採点済みはまだありません"]

    out.append("  材料がそろったとき:")
    for lean, label in (("up", "上にそろった"), ("down", "下にそろった")):
        sub = [r for r in done if r.get("state") == f"aligned_{lean}"]
        out += ["  " + x for x in _hit_line(label, sub, lambda r: (r.get(move) or 0) > 0,
                                            move, unit)]

    out.append("  FXモーニングブリーフの方向判定:")
    for lean, label in (("up", "▲ 上"), ("down", "▼ 下")):
        sub = [r for r in done if r.get("brief_lean") == lean]
        want_up = lean == "up"
        out += ["  " + x for x in _hit_line(
            label, sub, lambda r, u=want_up: _raw(r, move) != 0 and (_raw(r, move) > 0) == u,
            move, unit)]

    ex = [r for r in done if r.get("state") == "excluded"]
    ok = [r for r in done if r.get("state") != "excluded"]
    a, b = _swing(ex, best, worst), _swing(ok, best, worst)
    out.append("  除外したときは荒れていたか（上下の振れの平均）:")
    out.append(f"    除外    : {len(ex)} {unit}" + (f" / {a:.2f} ADR" if a is not None else ""))
    out.append(f"    それ以外: {len(ok)} {unit}" + (f" / {b:.2f} ADR" if b is not None else ""))
    if min(len(ex), len(ok)) < MIN_SAMPLES:
        out.append(f"    **どちらかが {MIN_SAMPLES} {unit}に届くまで、差は読みません。**")
    return out


def counts(rs: Sequence[Dict]) -> str:
    c: Dict[str, int] = {}
    for r in rs:
        c[r.get("state") or "unknown"] = c.get(r.get("state") or "unknown", 0) + 1
    return (f"{len(rs)} 件（上にそろった {c.get('aligned_up', 0)}・"
            f"下にそろった {c.get('aligned_down', 0)}・まちまち {c.get('mixed', 0)}・"
            f"除外 {c.get('excluded', 0)}）")


def summary(rows: Sequence[Dict]) -> List[str]:
    """時間帯ごとの採点（重ならない区間）と、東京の1枚を刻限まで測ったもの。"""
    out: List[str] = []
    for s in SESSIONS:
        rs = [r for r in rows if (r.get("session") or "tokyo") == s]
        out.append(f"=== {LABEL[s]}（{SEGMENT[s]}）===")
        out.append(f"  残したボード: {counts(rs)}")
        if rs:
            out += block(rs, move="seg_move_adr", best="seg_best_adr",
                         worst="seg_worst_adr")
        out.append("")
    tokyo = [r for r in rows if (r.get("session") or "tokyo") == "tokyo"]
    out.append("=== 東京の1枚を刻限まで（2026-10-01 までと同じ測り方）===")
    out += block(tokyo, move="move_adr", best="best_adr", worst="worst_adr", unit="日")
    return out
