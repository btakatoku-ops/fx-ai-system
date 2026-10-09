# -*- coding: utf-8 -*-
"""ダッシュボードの 1 枚を作る。**公開ページ（GitHub Pages）と PC の画面で同じものを使う。**

- 公開ページ … scripts/publish_board_page.py が 15 分ごとに作って送る
- PC の画面 … バックエンドの ``GET /dashboard`` が開いた時に作る（いつも新しい）

並べる銘柄は「支援する」と「様子を見る」。様子を見る銘柄は**並べるが計画は出さない**
（focus.py の「前には出さない」）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from . import dashboard_data, plan_snapshot
from . import focus as focus_mod
from .analysis import analyze_pair
from .board import build_board
from .board_page import render
from .config import TradingConfig

log = logging.getLogger(__name__)


def targets(cfg: TradingConfig) -> Tuple[List[str], List[str]]:
    """載せる銘柄（支援する、様子を見る）。"""
    focus = focus_mod.symbols(cfg, "focus") or [p.symbol for p in cfg.enabled_pairs()[:3]]
    watch = [s for s in focus_mod.symbols(cfg, "watch") if s not in focus]
    return focus, watch


def build(cfg: TradingConfig, provider, database_url: Optional[str],
          now: Optional[datetime] = None, home: Optional[str] = None) -> str:
    now = now or datetime.now(timezone.utc)
    focus, watch = targets(cfg)
    boards, snaps = [], {}
    for sym in focus + watch:
        try:
            a = analyze_pair(sym, provider, cfg, now=now)
            b = build_board(cfg, provider, sym, a, now)
            b["tier"] = "watch" if sym in watch else "focus"
            boards.append(b)
            if sym in watch:
                continue
            m15 = provider.get_candles(sym, "M15", limit=2)
            snap = plan_snapshot.build(cfg, sym, a, m15, b, now)
            if snap:
                snaps[sym] = snap
        except Exception as exc:                  # noqa: BLE001
            log.warning("ボードを作れません %s: %s", sym, exc)
    note = "" if getattr(provider, "name", "") != "mock" else "（合成データです。実勢ではありません）"
    extras = dashboard_data.collect(cfg, provider, boards, now, database_url)
    return render(boards, now, note=note, snapshots=snaps,
                  valid_minutes=plan_snapshot.VALID_MINUTES, extras=extras, home=home)
