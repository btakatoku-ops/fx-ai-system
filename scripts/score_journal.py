# -*- coding: utf-8 -*-
"""残した判断を、後から結果と突き合わせる。

**これが無いと、良くなったかどうかを測れない。** これまで優位性を測れて
いたのは過去データの再現だけで、実際に画面へ出た判断は誰も確かめて
いなかった。

## 決まり

1. **判断した時刻より後の足だけ使う。** 同じ足や手前の足を混ぜると、
   結果を知ってから採点することになる。
2. **合成データで出した判断は採点しない。** mock の判断を実勢の足と
   突き合わせても、数字は何も意味しない。飛ばして、飛ばした件数を出す。
3. **決着していないものは空のままにする。** 足がまだ足りないなら
   「まだ分からない」であって、時間切れではない。
4. **建てたことにしない。** ここで測るのは「そのとき出していた
   Entry / SL / TP のとおりに動いたか」だけ。実際に建てたかどうかは
   この道具は知らない。

使い方::

    set FX_CSV_DATA_DIR=./data/real
    .venv/Scripts/python.exe scripts/score_journal.py
    .venv/Scripts/python.exe scripts/score_journal.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import text                                   # noqa: E402

from app.backtester import simulate_bracket                   # noqa: E402
from app.config import get_settings, get_trading_config       # noqa: E402
from app.db import get_engine                                 # noqa: E402
from app.market_data import CSVMarketDataProvider, MarketDataError  # noqa: E402
from app.trade_logger import TABLE, init                      # noqa: E402


def _parse(value: object) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def score_one(row: Dict, candles: Sequence, entry_tf_minutes: int,
              max_hold: int, ambiguous: str) -> Optional[Dict]:
    """1件を突き合わせる。決着していなければ ``None``。"""
    decided = _parse(row["decided_at"])
    if decided is None:
        return None
    is_long = row["direction"] == "LONG"
    entry = row["entry_low"] if not is_long else row["entry_high"]
    if entry is None:
        entry = row["entry_low"] if row["entry_low"] is not None else row["entry_high"]
    stop, target = row["stop"], row["target"]
    if entry is None or stop is None or target is None:
        return None

    # **判断した時刻より後に始まる足だけ。** 同じ足を含めると、
    # 判断の元になった足で決着させることになる。
    start = None
    for i, c in enumerate(candles):
        if c.timestamp > decided:
            start = i
            break
    if start is None or start >= len(candles):
        return None

    # 決着に必要なだけ足が溜まっているか。**足りなければ採点しない。**
    if len(candles) - start < max_hold + 1:
        return None

    sim = simulate_bracket(list(candles), start, is_long, float(entry),
                           float(stop), float(target), max_hold, ambiguous)
    exit_bar = candles[sim["index"]]
    return {
        "outcome": sim["outcome"],
        "outcome_at": exit_bar.timestamp.astimezone(timezone.utc).isoformat(),
        "r_multiple": round(float(sim["r"]), 4),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="書き込まずに、何件採点できるかだけ出す")
    ap.add_argument("--limit", type=int, default=2000)
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    cfg = get_trading_config()
    settings = get_settings()
    bt = cfg.backtest
    entry_tf = bt["entry_timeframe"]
    max_hold = int(bt["exit"]["max_hold_bars"])
    ambiguous = bt["exit"]["ambiguous_bar"]

    engine = get_engine(settings.resolved_database_url)
    init(engine)
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            text(f"SELECT * FROM {TABLE} WHERE outcome IS NULL "
                 f"AND signal IN ('BUY','SELL') "
                 f"ORDER BY decided_at LIMIT :n"),
            {"n": a.limit}).mappings()]

    if not rows:
        print("採点できる判断がありません（BUY/SELL の記録が0件です）。")
        print("いまは全銘柄が見送りなので、これは想定どおりの状態です。")
        return 0

    synthetic = [r for r in rows if r["provider"] == "mock"]
    real = [r for r in rows if r["provider"] != "mock"]
    if synthetic:
        print(f"合成データの判断 {len(synthetic)} 件は採点しません"
              f"（実勢の足と突き合わせても意味がないため）")

    provider = CSVMarketDataProvider(settings.csv_dir)
    by_pair: Dict[str, List] = {}
    scored = 0
    pending = 0
    failed: Dict[str, str] = {}

    for row in real:
        pair = row["pair"]
        if pair not in by_pair:
            try:
                series = provider.get_candles(pair, entry_tf, limit=5000)
                by_pair[pair] = list(series.candles)
            except MarketDataError as exc:
                failed[pair] = str(exc)
                by_pair[pair] = []
        candles = by_pair[pair]
        if not candles:
            continue
        got = score_one(row, candles, cfg.timeframe_minutes(entry_tf),
                        max_hold, ambiguous)
        if got is None:
            pending += 1
            continue
        scored += 1
        if not a.dry_run:
            with engine.begin() as conn:
                conn.execute(
                    text(f"UPDATE {TABLE} SET outcome = :outcome, "
                         f"outcome_at = :outcome_at, r_multiple = :r, "
                         f"scored_at = :now WHERE id = :id"),
                    {"outcome": got["outcome"], "outcome_at": got["outcome_at"],
                     "r": got["r_multiple"],
                     "now": datetime.now(timezone.utc).isoformat(),
                     "id": row["id"]})

    print(f"\n採点 {scored} 件 / まだ決着していない {pending} 件"
          f"{'（書き込みなし）' if a.dry_run else ''}")
    for pair, why in failed.items():
        print(f"  {pair}: 足を読めません（{why}）")

    if scored and not a.dry_run:
        with engine.connect() as conn:
            got = conn.execute(
                text(f"SELECT outcome, COUNT(*) AS n, AVG(r_multiple) AS r "
                     f"FROM {TABLE} WHERE outcome IS NOT NULL "
                     f"GROUP BY outcome")).mappings().all()
        print("\n=== これまでの結果 ===")
        total = sum(g["n"] for g in got)
        for g in got:
            print(f"  {g['outcome']:<8} {g['n']:>4} 件　平均R {g['r']:+.3f}")
        decided_n = sum(g["n"] for g in got if g["outcome"] in ("WIN", "LOSS"))
        wins = sum(g["n"] for g in got if g["outcome"] == "WIN")
        if decided_n >= 30:
            print(f"  勝率 {wins / decided_n * 100:.1f}%（決着 {decided_n} 件）")
        else:
            print(f"  決着が {decided_n} 件しかありません。"
                  f"**勝率は名乗りません**（30件必要）")
        print(f"  合計 {total} 件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
