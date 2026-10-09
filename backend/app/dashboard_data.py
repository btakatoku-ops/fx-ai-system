# -*- coding: utf-8 -*-
"""公開ページ（ダッシュボード）の各欄に載せる材料を集める。I/O はここだけ。

どの欄も**実際のデータ**だけを出す。飾りの図は作らない。

- chart … その取引日の M15 の値動き、近い水準、**この先の値動きの幅**（向きは示さない）。
  幅は ``config/forecast.json``（scripts/measure_forecast.py が実データで測り、別の期間で
  約8割を覆うことを確かめた ATR の倍率）から引く。**測った範囲（32本＝8時間）より先は描かない。**
  （2026-10-09 まで、始値からの √時間 の近似を描いていた。測っていない形だったので置き換えた）
- tape  … 残したボード（朝・時間帯）と、その後の値動き（採点済みなら）。
- score … 採点の件数と、そろった日・ブリーフの向きが当たった件数。**30件に届くまで
  割合は名乗らない**（board_report と同じ決まり）。
- events … これからの重要指標（支援する銘柄の通貨だけ）。
- feed  … 銘柄ごとの最後の足と、その古さ。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from . import board_log, day_trade, news
from .indicator_engine import atr as atr_series
from .board import fx_day_start
from .config import TradingConfig

log = logging.getLogger(__name__)

MIN_SAMPLES = 30


def forecast_band(cfg: TradingConfig, bars) -> Optional[Dict]:
    """最後の足から先の「約8割が収まった幅」。測っていなければ None（推測で描かない）。"""
    fc = getattr(cfg, "forecast", None) or {}
    horizons = fc.get("horizons") or {}
    if not horizons or fc.get("timeframe") != "M15" or len(bars) < 30:
        return None
    period = int(fc.get("atr_period", 14))
    a = atr_series([float(b.high) for b in bars], [float(b.low) for b in bars],
                   [float(b.close) for b in bars], period)
    last_atr = a[-1] if a else None
    if not last_atr or last_atr <= 0:
        return None
    close = float(bars[-1].close)
    levels = []
    for key in sorted(horizons, key=lambda k: int(k)):
        mult = horizons[key].get("atr_multiple")
        if mult is None:
            continue
        w = float(mult) * float(last_atr)
        levels.append({"bars": int(key), "low": close - w, "high": close + w,
                       "coverage": horizons[key].get("measured_coverage")})
    if not levels:
        return None
    covs = [lv["coverage"] for lv in levels if lv["coverage"] is not None]
    return {"close": close, "atr": float(last_atr), "levels": levels,
            "quantile": fc.get("quantile"),
            "coverage_min": min(covs) if covs else None,
            "coverage_max": max(covs) if covs else None,
            "measured_at": fc.get("measured_at")}


def chart(cfg: TradingConfig, provider, symbol: str, facts: Dict) -> Optional[Dict]:
    """その取引日（閉場中なら直近の取引日）の M15 の動き。"""
    try:
        bars = provider.get_candles(symbol, "M15", limit=200).candles
    except Exception as exc:                     # noqa: BLE001
        log.warning("チャートの足を取れません %s: %s", symbol, exc)
        return None
    if not bars:
        return None
    start = fx_day_start(bars[-1].timestamp)
    day = [b for b in bars if b.timestamp >= start]
    if not day:
        return None
    deadline = day_trade.exit_deadline(cfg.filters, start + timedelta(minutes=1))
    return {
        "day_start": start.isoformat(),
        "deadline": deadline.isoformat() if deadline else None,
        "open": float(day[0].open),
        "adr": facts.get("adr"),
        "bars": [[int((b.timestamp - start).total_seconds() // 60),
                  float(b.high), float(b.low), float(b.close), float(b.open)] for b in day],
        "last_bar_at": day[-1].timestamp.isoformat(),
        "band": forecast_band(cfg, bars),
    }


def _move(r: Dict, key: str = "move_adr") -> Optional[float]:
    """向きをそろえる前の値動き（上が正、ADR 単位）。"""
    m = r.get(key)
    if m is None:
        return None
    return -m if r.get("lean") == "down" else m


def tape(database_url: str, limit: int = 24) -> List[Dict]:
    rows = board_log.recent(database_url, limit=limit)
    out = []
    for r in rows:
        out.append({
            "recorded_at": r.get("recorded_at"), "session": r.get("session") or "tokyo",
            "pair": r.get("pair"), "state": r.get("state"), "lean": r.get("lean"),
            "brief_lean": r.get("brief_lean"), "price": r.get("price"),
            "scored": bool(r.get("scored_at")), "move": _move(r),
            "seg_move": _move(r, "seg_move_adr"),
        })
    out.sort(key=lambda r: r["recorded_at"] or "", reverse=True)
    return out


def score(database_url: str) -> Dict:
    rows = board_log.recent(database_url, limit=5000)
    done = [r for r in rows if r.get("scored_at")]

    def hits(rs, lean_key):
        n = h = 0
        for r in rs:
            lean, mv = r.get(lean_key), _move(r)
            if lean not in ("up", "down") or mv is None or mv == 0:
                continue
            n += 1
            h += 1 if (mv > 0) == (lean == "up") else 0
        return {"n": n, "hits": h, "rate": (h / n if n >= MIN_SAMPLES else None)}

    excluded = [r for r in done if r.get("state") == "excluded"]
    swing = [abs(r.get("best_adr") or 0) + abs(r.get("worst_adr") or 0) for r in excluded]
    return {
        "recorded": len(rows), "scored": len(done),
        "aligned": hits(done, "lean"), "brief": hits(done, "brief_lean"),
        "excluded": len(excluded),
        "excluded_swing": (sum(swing) / len(swing)) if swing else None,
        "min_samples": MIN_SAMPLES,
    }


def events(symbols: Sequence[str], now: datetime, hours: int = 36, n: int = 8) -> List[Dict]:
    cur = {s[:3] for s in symbols} | {s[3:] for s in symbols}
    _, _, evs = news.load_calendar()
    out = []
    for e in evs:
        if e.at < now or e.at > now + timedelta(hours=hours):
            continue
        if e.currency and e.currency not in cur:
            continue
        if e.impact not in ("High", "Medium"):
            continue
        out.append({"at": e.at.isoformat(), "title": e.title,
                    "currency": e.currency, "impact": e.impact})
        if len(out) >= n:
            break
    return out


def collect(cfg: TradingConfig, provider, boards: List[Dict], now: datetime,
            database_url: Optional[str]) -> Dict:
    """各欄の材料をまとめて集める。1つ失敗しても他は出す。"""
    out: Dict = {"charts": {}, "tape": [], "score": None, "events": []}
    for b in boards:
        try:
            ch = chart(cfg, provider, b["pair"], b.get("facts") or {})
            if ch:
                out["charts"][b["pair"]] = ch
        except Exception as exc:                 # noqa: BLE001
            log.warning("チャートを作れません %s: %s", b.get("pair"), exc)
    if database_url:
        try:
            out["tape"] = tape(database_url)
            out["score"] = score(database_url)
        except Exception as exc:                 # noqa: BLE001
            log.warning("記録を読めません: %s", exc)
    try:
        out["events"] = events([b["pair"] for b in boards], now)
    except Exception as exc:                     # noqa: BLE001
        log.warning("指標を読めません: %s", exc)
    return out
