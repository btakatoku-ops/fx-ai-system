# -*- coding: utf-8 -*-
"""ダッシュボードの 1 枚を作る。**公開ページ（GitHub Pages）と PC の画面で同じものを使う。**

- 公開ページ … scripts/publish_board_page.py が 15 分ごとに作って送る
- PC の画面 … バックエンドの ``GET /dashboard`` が開いた時に作る（いつも新しい）

並べる銘柄は**円建ての3本（固定）と、非円建ての3本（取引日ごとに選ぶ。daily_pick.py）**
（2026-10-09 利用者の指定）。6本とも計画を出す。ただしボードが除外の日（費用が重い・
鮮度が足りないなど）は、計画の側で止まる（安全の条件は弱めない）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import daily_pick, dashboard_data, plan_snapshot
from . import focus as focus_mod
from .analysis import analyze_pair
from .board import build_board
from .board_page import render
from .config import TradingConfig

log = logging.getLogger(__name__)


def targets(cfg: TradingConfig, provider=None, now: Optional[datetime] = None
            ) -> Tuple[List[str], List[str], Dict]:
    """載せる銘柄（円建て、非円建て）と、今日の選択の記録。"""
    st = daily_pick.settings(cfg)
    known = {p.symbol for p in cfg.enabled_pairs()}
    jpy = [s for s in st["jpy"] if s in known]
    pick: Dict = {"status": "none", "pairs": []}
    if provider is not None:
        try:
            pick = daily_pick.today(cfg, provider, now)
        except Exception as exc:                  # noqa: BLE001
            log.warning("今日の非円建てを選べません: %s", exc)
    other = [s for s in pick.get("pairs") or [] if s in known and s not in jpy]
    return jpy, other, pick


def status(boards: List[Dict], extras: Dict, now: datetime) -> Dict:
    """携帯のショートカットが読む小さな状態（公開ページの status.json）。**見る価値がある時だけ知らせる。**

    知らせる条件（どちらか）：
    1. ボードが「そろった」（除外なし）で、そろった向きの利確の目安が**計画と同じ計算で出せる**銘柄がある
    2. 仕組みの不調（health の ⚠）がある
    公開ページと同じ中身しか入れない（誰でも見られる）。"""
    dec = extras.get("decision")
    if dec is not None:
        names = {c["pair"] for c in dec.get("candidates") or []}
        ready = [b for b in boards if b["pair"] in names]
    else:
        ready = [b for b in boards
                 if (b.get("verdict") or {}).get("state") in ("aligned_up", "aligned_down")
                 and (b.get("tp_preview") or {}).get("status") == "PLAN_OK"]
    warns = [x for x in extras.get("health") or [] if x.get("state") == "warn"]
    parts = []
    if ready:
        parts.append("計画が出せる：" + "、".join(
            f'{b["pair"]}{"▲" if b["verdict"]["state"] == "aligned_up" else "▼"}' for b in ready))
    if warns:
        parts.append("仕組みの不調：" + "、".join(x["label"] for x in warns))
    alert = bool(parts)
    jst = now.astimezone(timezone(timedelta(hours=9)))
    return {"generated_at": now.isoformat(), "generated_jst": f"{jst:%m/%d %H:%M}",
            "alert": "あり" if alert else "なし",
            "message": (f"FXボード {jst:%H:%M}　" + "／".join(parts)) if alert else
                       f"FXボード {jst:%H:%M}　見る価値のある場面はありません",
            "ready": [{"pair": b["pair"], "state": b["verdict"]["state"]} for b in ready],
            "health_warn": [x["label"] for x in warns],
            # Market Radar の FX タブが読んで「FX ボードの今日の判断」として出す（2026-10-19）
            "decision": ({k: dec.get(k) for k in ("verdict", "title", "lead", "reasons", "next_look_label")}
                         | {"candidates": [{"pair": c["pair"], "lean": c["lean"]}
                                           for c in dec.get("candidates") or []]}) if dec else None}


def build(cfg: TradingConfig, provider, database_url: Optional[str],
          now: Optional[datetime] = None, home: Optional[str] = None) -> str:
    return build_with_status(cfg, provider, database_url, now, home)[0]


def build_with_status(cfg: TradingConfig, provider, database_url: Optional[str],
                      now: Optional[datetime] = None, home: Optional[str] = None) -> Tuple[str, Dict]:
    now = now or datetime.now(timezone.utc)
    jpy, other, pick = targets(cfg, provider, now)
    syms = jpy + other
    rates = daily_pick.conversion_rates(cfg, provider, other, now)
    boards, snaps = [], {}
    for sym in syms:
        try:
            a = analyze_pair(sym, provider, cfg, now=now)
            b = build_board(cfg, provider, sym, a, now)
            b["tier"] = focus_mod.tier_of(cfg, sym)
            b["group"] = "jpy" if sym in jpy else "pick"
            boards.append(b)
            m15 = provider.get_candles(sym, "M15", limit=2)
            need = {k: v for k, v in rates.items()
                    if k in daily_pick.conversion_symbols([sym])}
            snap = plan_snapshot.build(cfg, sym, a, m15, b, now, rates=need)
            if snap:
                snaps[sym] = snap
                # そろった向きがあれば、その向きで見た利確の目安（計画と同じ計算）
                lean = {"aligned_up": "LONG", "aligned_down": "SHORT"}.get(
                    (b.get("verdict") or {}).get("state"))
                if lean:
                    b["tp_preview"] = plan_snapshot.preview(
                        snap, lean, cfg.spread_price(sym), now, cfg.filters)
        except Exception as exc:                  # noqa: BLE001
            log.warning("ボードを作れません %s: %s", sym, exc)
    note = "" if getattr(provider, "name", "") != "mock" else "（合成データです。実勢ではありません）"
    extras = dashboard_data.collect(cfg, provider, boards, now, database_url)
    extras["pick"] = pick
    from . import decision
    try:
        extras["decision"] = decision.decide(boards, extras, now, cfg)
    except Exception as exc:                      # noqa: BLE001
        log.warning("今日の判断を出せません: %s", exc)
    html = render(boards, now, note=note, snapshots=snaps,
                  valid_minutes=plan_snapshot.VALID_MINUTES, extras=extras, home=home)
    return html, status(boards, extras, now)
