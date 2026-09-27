# -*- coding: utf-8 -*-
"""朝の確認を1枚で出す。

**今日この道具が使えるのか、から書く。** 使えないなら、その理由と
やることを先に出す。26銘柄の点数を眺めるのはその後。

毎朝これを走らせる想定。取り込みが止まっていれば、そのことが最初の
3行で分かる。

使い方::

    set FX_MARKET_DATA_PROVIDER=csv
    set FX_CSV_DATA_DIR=./data/real
    .venv/Scripts/python.exe scripts/morning_brief.py
    .venv/Scripts/python.exe scripts/morning_brief.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import trade_logger                                  # noqa: E402
from app.config import get_settings, get_trading_config       # noqa: E402
from app.focus import symbols as focus_symbols                # noqa: E402
from app.market_data import build_provider                    # noqa: E402
from app.morning_brief import as_text, build                  # noqa: E402
from app.pair_ranker import rank_pairs                        # noqa: E402


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="JSON で出す")
    ap.add_argument("--all-pairs", action="store_true",
                    help="支援する銘柄だけでなく全銘柄を見る")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    cfg = get_trading_config()
    settings = get_settings()
    provider = build_provider(settings)
    # **毎朝26銘柄を計算しない。** 支援する銘柄だけで足りる。
    targets = None if a.all_pairs else (focus_symbols(cfg, "focus") or None)
    seen: List = []
    rank_pairs(provider, cfg, symbols=targets, on_result=seen.append)

    # **毎朝の判断も残す。** 画面から見たときだけ残していたので、
    # 定期実行で出した判断はどこにも残らなかった。記録は「道具が何を
    # 出したか」を後から確かめるためにあるのに、いちばん確実に毎日動く
    # 経路が抜けていた。
    name = getattr(provider, "name", "unknown")
    url = settings.resolved_database_url
    for res in seen:
        plan = trade_logger.plan_for(res, cfg, provider)
        trade_logger.record(res, plan, database_url=url, provider=name)

    from app.board import build_board

    from app import board_log

    boards = []
    for res in seen:
        try:
            b = build_board(cfg, provider, res.pair, res)
        except Exception as exc:                  # noqa: BLE001
            print(f"  {res.pair}: ボードを作れません（{exc}）")
            continue
        boards.append(b)
        # **その日の最初の1枚だけ残す。** 朝に見たものが判断の材料だった。
        board_log.record(b, database_url=url, provider=name)
    brief = build(cfg, name, url, results=seen, boards=boards)
    if a.json:
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    else:
        print(as_text(brief))
    # 使えない朝は 1 を返す。**自動で回したときに気づけるように。**
    return 0 if brief["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
