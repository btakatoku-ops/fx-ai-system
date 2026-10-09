# -*- coding: utf-8 -*-
"""ロンドン・ニューヨークの始まりに、ボードを1銘柄1枚ずつ残す。

取引時間中の取り込み（``refresh_intraday.ps1``、5分ごと）が毎回呼ぶ。
始まりから1時間のあいだ（``config/board.json`` の ``session_boards``）だけ
動き、それ以外の回は何もしないで終わる。東京の1枚は朝の処理が残す。

- **その日・その時間帯の最初の1枚だけ。** 残っていれば何もしない。
- **配信元の遅れで「古すぎ」になったボードは、始まりから45分までは残さず
  次の回を待つ。** 遅れは相場の事実ではないので。それを過ぎたら、そのまま残す。
- 売買の判断の記録（``trade_logger``）や公開ページは触らない。

使い方::

    set FX_MARKET_DATA_PROVIDER=csv
    set FX_CSV_DATA_DIR=./data/real
    .venv/Scripts/python.exe scripts/record_session_board.py
    .venv/Scripts/python.exe scripts/record_session_board.py --session london   # 時刻を問わず試す
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import board_log, sessions                          # noqa: E402
from app.board import fx_day_start                            # noqa: E402
from app.config import get_settings, get_trading_config       # noqa: E402
from app.focus import symbols as focus_symbols                # noqa: E402

STALE = "古すぎ"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", choices=[sessions.LONDON, sessions.NEWYORK],
                    help="時刻を問わずこの時間帯として残す（試すとき用）")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    cfg = get_trading_config()
    now = datetime.now(timezone.utc)
    session = a.session or sessions.current(now, cfg.board)
    if session is None:
        return 0                                  # 残す時間ではない

    settings = get_settings()
    url = settings.resolved_database_url
    targets: List[str] = focus_symbols(cfg, "focus") or []
    day = fx_day_start(now).isoformat()
    have = {(r["pair"], r.get("session")) for r in board_log.recent(url, limit=50)
            if r["board_day"] == day}
    todo = [p for p in targets if (p, session) not in have]
    if not todo:
        return 0

    from app.board import build_board
    from app.market_data import build_provider
    from app.pair_ranker import rank_pairs

    provider = build_provider(settings)
    name = getattr(provider, "name", "unknown")
    seen: List = []
    rank_pairs(provider, cfg, symbols=todo, on_result=seen.append)
    label = sessions.LABEL[session]
    for res in seen:
        try:
            b = build_board(cfg, provider, res.pair, res)
        except Exception as exc:                  # noqa: BLE001
            print(f"{label} {res.pair}: ボードを作れません（{exc}）")
            continue
        stale = any(STALE in x for x in (b.get("verdict") or {}).get("exclude", []))
        if stale and not a.session and sessions.may_wait_for_fresh_data(
                session, now, cfg.board):
            print(f"{label} {res.pair}: 足が古いので次の回を待ちます")
            continue
        if board_log.record(b, database_url=url, provider=name, session=session):
            print(f"{label} {res.pair}: 残しました（{b['verdict'].get('label')}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
