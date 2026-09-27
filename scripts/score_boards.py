# -*- coding: utf-8 -*-
"""残した朝のボードを採点して、方式が役に立っているかを見る。

確かめたいのは2つだけ。

1. **材料がそろった日（上・下）は、その向きに動いたか。** コイン投げなら
   半分。半分を大きく超えなければ、そろったことに意味は無い。
2. **除外した日は、荒れた日だったか。** 除外した日の値動きが、そろった日と
   変わらないなら、除外は見送りの言い訳になっているだけかもしれない。

**件数が少ないうちは結論を出さない。** 30件に届かない区分は、勝率を名乗らない。

使い方::

    set FX_CSV_DATA_DIR=./data/real
    .venv/Scripts/python.exe scripts/score_boards.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import board_log                                     # noqa: E402
from app.config import get_settings, get_trading_config       # noqa: E402
from app.market_data import CSVMarketDataProvider, MarketDataError  # noqa: E402
from app.performance import wilson                            # noqa: E402

MIN_SAMPLES = 30


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    cfg = get_trading_config()
    settings = get_settings()
    url = settings.resolved_database_url
    provider = CSVMarketDataProvider(settings.csv_dir)

    rows = board_log.recent(url, limit=5000)
    if not rows:
        print("残したボードがまだありません（毎朝の確認で1銘柄1日1枚ずつ溜まります）。")
        return 0

    bars: Dict[str, List] = {}
    scored = pending = 0
    for r in rows:
        if r.get("scored_at"):
            continue
        pair = r["pair"]
        if pair not in bars:
            try:
                bars[pair] = list(provider.get_candles(pair, "M15", limit=5000).candles)
            except MarketDataError:
                bars[pair] = []
        got = board_log.score_one(r, bars[pair])
        if got is None:
            pending += 1
            continue
        board_log.save_score(url, r["id"], got)
        scored += 1
    print(f"今回採点 {scored} 件 / まだ刻限前か足が足りない {pending} 件")

    done = [r for r in board_log.recent(url, limit=5000) if r.get("scored_at")]
    if not done:
        print("採点済みのボードはまだありません。")
        return 0

    print("\n=== 材料がそろった日（その向きに動いたか）===")
    for lean, label in (("up", "上にそろった"), ("down", "下にそろった")):
        rs = [r for r in done if r["state"] == f"aligned_{lean}"]
        if not rs:
            print(f"  {label}: まだありません")
            continue
        hits = sum(1 for r in rs if (r["move_adr"] or 0) > 0)
        lo, hi = wilson(hits, len(rs))
        avg = sum(r["move_adr"] or 0 for r in rs) / len(rs)
        rate = f"{hits / len(rs) * 100:.0f}%" if len(rs) >= MIN_SAMPLES else "—"
        print(f"  {label}: {len(rs)} 日 / その向きに動いた {hits} 日（{rate}、"
              f"95%区間 {lo * 100:.0f}〜{hi * 100:.0f}%）/ 平均 {avg:+.2f} ADR")
        if len(rs) < MIN_SAMPLES:
            print(f"    **{MIN_SAMPLES} 日に届くまで、割合は名乗りません。**"
                  f"コイン投げなら50%です")

    # FXモーニングブリーフの方向判定も、同じ足・同じ刻限で測る。
    # ブリーフは自分で答え合わせをしているが、実際の高安が取れていない日が
    # ある。ここでは取り込んだ足で機械的に測る。
    print("\n=== FXモーニングブリーフの方向判定（その向きに動いたか）===")
    for lean, label in (("up", "▲ 上"), ("down", "▼ 下")):
        rs = [r for r in done if r.get("brief_lean") == lean]
        if not rs:
            print(f"  {label}: まだありません")
            continue
        def raw(r):
            m = r["move_adr"] or 0
            return -m if r.get("lean") == "down" else m
        hits = sum(1 for r in rs if (raw(r) > 0) == (lean == "up") and raw(r) != 0)
        lo, hi = wilson(hits, len(rs))
        rate = f"{hits / len(rs) * 100:.0f}%" if len(rs) >= MIN_SAMPLES else "—"
        print(f"  {label}: {len(rs)} 日 / その向きに動いた {hits} 日（{rate}、"
              f"95%区間 {lo * 100:.0f}〜{hi * 100:.0f}%）")
    if not any(r.get("brief_lean") in ("up", "down") for r in done):
        print(f"  **{MIN_SAMPLES} 日に届くまで、割合は名乗りません。**")

    print("\n=== 除外した日は荒れていたか ===")
    def spread(rs):
        vals = [abs(r["best_adr"] or 0) + abs(r["worst_adr"] or 0) for r in rs]
        return sum(vals) / len(vals) if vals else None
    ex = [r for r in done if r["state"] == "excluded"]
    ok = [r for r in done if r["state"] != "excluded"]
    a, b = spread(ex), spread(ok)
    if a is not None:
        print(f"  除外した日: {len(ex)} 日 / 刻限までの上下の振れ 平均 {a:.2f} ADR")
    if b is not None:
        print(f"  それ以外  : {len(ok)} 日 / 刻限までの上下の振れ 平均 {b:.2f} ADR")
    if a is not None and b is not None and min(len(ex), len(ok)) < MIN_SAMPLES:
        print(f"  **どちらかが {MIN_SAMPLES} 日に届くまで、差は読みません。**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
