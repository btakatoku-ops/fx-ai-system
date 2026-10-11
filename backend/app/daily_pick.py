# -*- coding: utf-8 -*-
"""ダッシュボードに並べる銘柄。円建ては固定の3本、**非円建ては時間帯ごとに3本を選ぶ。**

利用者の指定（2026-10-09）：「利確できそうな3本を毎日選ぶ」→ 時間帯（東京・ロンドン・
ニューヨーク）の始まりごとに選び直す（同日、利用者が選んだ）。朝 8:30 の足で費用の条件を
満たす非円建ては、過去53日のうち46日で0本だった。条件を満たす銘柄は夕方〜深夜に多い。

**このアプリは向きを予測しない。** なので「儲かりそうな銘柄」は選ばない。選ぶのは、
**利確（TP1）まで届く余地が大きい銘柄**。測れる数字だけで決める::

    TP1 までの距離 = 損切り幅 × 最小の比（min_rr） ＋ 滑り ＋ スプレッド
                    （損切り幅 = ATR × 倍率 ＋ 往復の滑り。計画の規則と同じ）
    刻限までの幅   = M15 の ATR × 実測の倍率（config/forecast.json。約8割が収まった幅）
    届きやすさ     = 刻限までの幅 ÷ TP1 までの距離

1 を超えるほど、TP1 が「ふだん約8割が収まる幅」の内側にある。**当たる確率ではない。**
**エンジンの費用の条件（スプレッドが M15 の ATR の 25% 以下など）を満たす銘柄を先にする。**
満たさない銘柄はボードが除外になり計画も出ないので、3本に足りない時だけ、印を付けて埋める。
幅と損切りはどちらも ATR に比例するので、実際には「今日の値動きに比べて費用が軽い順」
とほぼ同じになる（それで構わない。費用が重いと TP1 が遠くなる）。

- **時間帯ごとに1回**選び、``data/daily_pick.json`` に残す。時間帯の中では選び直さない
  （15分ごとに並びが変わると、見ていた銘柄が消える）。時間帯の区切りは sessions.py と同じ
  （東京＝取引日の始まり〜ロンドンの始まり、ロンドン〜ニューヨークの始まり、ニューヨーク〜）。
- 選ぶ時に足が古い銘柄は候補にしない。**候補の8割以上の足が新しい時だけ選んで残す**
  （前の3本だけ足が新しい、という状態で選ぶと、同じ3本が選ばれ続ける）。時間帯の始まりは
  scripts/dashboard_pairs.py が全銘柄の取り込みを頼む。足りなければ残さず、前の選択を使う。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .board import fx_day_start
from .board_page import JST
from .config import PROJECT_ROOT, TradingConfig
from .indicator_engine import atr as atr_series

log = logging.getLogger(__name__)

PICK_FILE = PROJECT_ROOT / "data" / "daily_pick.json"


def settings(cfg: TradingConfig) -> Dict:
    d = (getattr(cfg, "board", None) or {}).get("dashboard") or {}
    return {"jpy": [s.upper() for s in d.get("jpy") or ["USDJPY", "GBPJPY", "EURJPY"]],
            "n": int(d.get("non_jpy_pick", 3)),
            "max_age": int(d.get("pick_max_bar_age_minutes", 90)),
            "rate_max_age": int(d.get("rate_max_age_minutes", 15))}


def candidates(cfg: TradingConfig) -> List[str]:
    """非円建ての候補。有効な銘柄のうち円を含まず、**費用の条件を一度でも満たした実績がある**もの。

    実績は config/focus.json の pass_rate（実データの M15 で、スプレッド÷ATR が上限以下だった
    足の割合。measure_pair_value.py が測る）。0 の銘柄は、選んでもボードが除外になるだけで、
    取り込みの回数（Yahoo の制限。2026-10-10 に 429 で止まった）を増やすだけなので外す。
    測っていなければ、円を含まない銘柄すべて。
    """
    rows = ((getattr(cfg, "focus", None) or {}).get("pairs") or {})
    out = []
    for p in cfg.enabled_pairs():
        if "JPY" in (p.base, p.quote):
            continue
        r = rows.get(p.symbol)
        if rows and (not r or float(r.get("pass_rate") or 0) <= 0):
            continue
        out.append(p.symbol)
    return out


def _day(now: datetime) -> str:
    """取引日の名前。**日本時間の日付**（ニューヨーク17時＝日本の朝6〜7時に替わる）。"""
    return fx_day_start(now).astimezone(JST).date().isoformat()


def _slot(cfg: TradingConfig, now: datetime) -> str:
    """いまの時間帯（tokyo / london / newyork）。区切りは sessions.open_at と同じ。"""
    from . import sessions

    day = fx_day_start(now)
    name = sessions.TOKYO
    for s in (sessions.LONDON, sessions.NEWYORK):
        at = sessions.open_at(s, day, getattr(cfg, "board", None))
        if at is not None and now >= at:
            name = s
    return name


def slot_key(cfg: TradingConfig, now: datetime) -> str:
    return f"{_day(now)}/{_slot(cfg, now)}"


def _horizon(cfg: TradingConfig, now: datetime) -> Optional[Dict]:
    """刻限までに収まる、測った中でいちばん長い本数の倍率。"""
    from . import day_trade

    hz = ((getattr(cfg, "forecast", None) or {}).get("horizons") or {})
    if not hz:
        return None
    keys = sorted(hz, key=int)
    deadline = day_trade.exit_deadline(cfg.filters, now)
    left = int((deadline - now).total_seconds() // 900) if deadline else int(keys[-1])
    fit = [k for k in keys if int(k) <= left] or keys[:1]
    k = fit[-1]
    return {"bars": int(k), "multiple": float(hz[k]["atr_multiple"])}


def reach(cfg: TradingConfig, provider, symbol: str, now: datetime) -> Dict:
    """1銘柄の届きやすさ。使えなければ ``reason`` を付けて返す。"""
    st = settings(cfg)
    row: Dict = {"pair": symbol}
    try:
        bars = provider.get_candles(symbol, "M15", limit=200).candles
    except Exception as exc:                      # noqa: BLE001
        return {**row, "reason": f"足を読めません（{exc}）"}
    if len(bars) < 30:
        return {**row, "reason": "足が足りません"}
    age = (now - bars[-1].timestamp).total_seconds() / 60
    if age > st["max_age"]:
        return {**row, "reason": f"足が古い（{age:.0f}分前）"}
    hz = _horizon(cfg, now)
    if hz is None:
        return {**row, "reason": "値動きの幅を測っていません"}
    period = int((cfg.forecast or {}).get("atr_period", 14))
    a = atr_series([float(b.high) for b in bars], [float(b.low) for b in bars],
                   [float(b.close) for b in bars], period)
    atr = a[-1] if a else None
    if not atr or atr <= 0:
        return {**row, "reason": "ATR を出せません"}
    spec = cfg.pair(symbol)
    from . import plan_inputs                     # 滑り込みの仮定は計画と同じところから読む

    slip = float(plan_inputs.costs(cfg, symbol).slippage_pips_per_side) * spec.pip
    stop = cfg.account["stop"]
    min_rr = float(cfg.account["targets"]["min_rr"])
    spread = cfg.spread_price(symbol)
    loss = float(stop["atr_multiple"]) * atr + 2 * slip
    tp1 = min_rr * loss + 2 * slip + spread
    room = hz["multiple"] * atr
    # **エンジンの費用の条件（filters.json spread）を満たすか。** 満たさなければボードは
    # 除外になり、計画も出ない。選ぶ時はこれを満たすものを先にする
    sp = cfg.filters["spread"]
    max_ratio = float(sp["max_ratio_of_atr"])
    max_pips = float(sp["max_pips"].get(symbol, sp["max_pips"]["default"]))
    ratio = spread / atr
    cost_ok = ratio <= max_ratio and spread / spec.pip <= max_pips
    return {**row, "reach": round(room / tp1, 3), "cost_ok": cost_ok,
            "spread_atr": round(ratio, 3),
            "tp1_pips": round(tp1 / spec.pip, 1), "room_pips": round(room / spec.pip, 1),
            "spread_pips": round(spread / spec.pip, 2), "horizon_bars": hz["bars"],
            "last_bar_at": bars[-1].timestamp.isoformat()}


def compute(cfg: TradingConfig, provider, now: Optional[datetime] = None) -> Dict:
    now = now or datetime.now(timezone.utc)
    rows = [reach(cfg, provider, s, now) for s in candidates(cfg)]
    # 費用の条件を満たすものを先に、その中で届きやすい順
    ok = sorted([r for r in rows if "reach" in r],
                key=lambda r: (not r["cost_ok"], -r["reach"], r["pair"]))
    n = settings(cfg)["n"]
    need = max(n, -(-len(rows) * 8 // 10))          # 候補の8割以上（切り上げ）
    return {"fx_day": _day(now), "session": _slot(cfg, now), "key": slot_key(cfg, now),
            "picked_at": now.isoformat(),
            "pairs": [r["pair"] for r in ok[:n]], "table": ok + [r for r in rows if "reach" not in r],
            "complete": len(ok) >= need}


def _read(path: Path) -> Optional[Dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def today(cfg: TradingConfig, provider, now: Optional[datetime] = None,
          path: Optional[Path] = None) -> Dict:
    """いまの時間帯の3本。**時間帯ごとに1度だけ選び、その中では同じものを返す。**

    返り値の ``status``: ``today``（この時間帯に選んだ）／ ``previous``（候補の足がそろわず、
    前の選択のまま）／ ``none``（選べない）。
    """
    now = now or datetime.now(timezone.utc)
    path = path or PICK_FILE
    key = slot_key(cfg, now)
    saved = _read(path)
    if saved and saved.get("key") == key and saved.get("pairs"):
        return {**saved, "status": "today"}
    got = compute(cfg, provider, now)
    if got["complete"]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(got, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.warning("今日の選択を残せません: %s", exc)
        return {**got, "status": "today"}
    if saved and saved.get("pairs"):
        return {**saved, "status": "previous"}
    return {**got, "status": "none"}


def conversion_symbols(symbols: List[str]) -> List[str]:
    """円換算に要る銘柄（例: EURUSD → USDJPY と EURJPY）。"""
    out: List[str] = []
    for s in symbols:
        for ccy in (s[3:], s[:3]):
            if ccy != "JPY":
                sym = f"{ccy}JPY"
                if sym not in out:
                    out.append(sym)
    return out


def conversion_rates(cfg: TradingConfig, provider, symbols: List[str],
                     now: Optional[datetime] = None) -> Dict[str, Dict]:
    """円換算レート（取り込んだ5分足の終値）。**古いものは入れない**（入れなければ計画は止まる）。"""
    now = now or datetime.now(timezone.utc)
    limit = timedelta(minutes=settings(cfg)["rate_max_age"])
    out: Dict[str, Dict] = {}
    for sym in conversion_symbols(symbols):
        try:
            bars = provider.get_candles(sym, "M5", limit=1).candles
        except Exception as exc:                  # noqa: BLE001
            log.warning("円換算レートを読めません %s: %s", sym, exc)
            continue
        if not bars or now - bars[-1].timestamp > limit:
            continue
        out[sym] = {"value": str(bars[-1].close), "observed_at": bars[-1].timestamp.isoformat()}
    return out
