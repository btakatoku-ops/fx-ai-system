# -*- coding: utf-8 -*-
"""計画（plan_calc）に渡すスナップショットを作る（Phase 2・P2-2）。

ここだけが I/O をする（足・分析・ボード・設定を読む）。計算は plan_calc。

- **基準価格は MT4 から手で入れた bid・ask。** 足の終値は構造・ATR と、打ち間違いの
  照合にだけ使う。
- **円以外が決済通貨の銘柄**は、円換算レートを手で入れない限り止まる
  （取り込んだ足は数時間遅れることがあり、5分の有効期限を満たせない）。
- 呼値は ``account.json`` の ``price_tick``（推定）。計画に「推定」と出す。
- 数量の条件は取引要綱（``account.json`` の ``verified_source``）の転記。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, Optional

from . import plan_calc as P
from .config import TradingConfig

D = Decimal


def _verified_on(account: Dict) -> Optional[str]:
    """取引要綱を確かめた日（``verified_source`` の「（YYYY-MM-DD 取得）」）。"""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", str(account.get("verified_source") or ""))
    return m.group(1) if m else None


def instrument(cfg: TradingConfig, symbol: str) -> P.Instrument:
    spec = cfg.pair(symbol)
    acc = cfg.account
    course = acc["courses"][acc["course"]]
    unit = int((course.get("unit_overrides") or {}).get(spec.symbol, course["unit"]))
    ticks = acc.get("price_tick") or {}
    tick = ticks.get("JPY") if spec.quote == "JPY" else ticks.get("other")
    return P.Instrument(
        symbol=spec.symbol, base=spec.base, quote=spec.quote,
        pip=P.dec(spec.pip), price_tick=P.dec(tick) if tick else D("NaN"),
        contract_unit=unit, min_qty=1, qty_step=1,
        max_qty=int(course.get("max_qty", 100)), course=acc["course"],
        verified_on=_verified_on(acc), source=str(acc.get("verified_source", "")))


def account(cfg: TradingConfig, balance: Optional[float] = None,
            risk_pct: Optional[float] = None) -> P.Account:
    acc = cfg.account
    d = acc["defaults"]
    return P.Account(
        balance=P.dec(balance if balance is not None else d["balance"]),
        risk_pct=P.dec(risk_pct if risk_pct is not None else d["risk_per_trade_pct"]),
        max_risk_pct=P.dec(d["max_risk_per_trade_pct"]),
        max_margin_pct=P.dec(d["max_margin_use_pct"]),
        margin_rate=P.dec(acc["margin_rate"]), course=acc["course"],
        currency=acc.get("quote_currency", "JPY"))


def rules(cfg: TradingConfig) -> P.Rules:
    s, t = cfg.account["stop"], cfg.account["targets"]
    return P.Rules(
        atr_multiple=P.dec(s["atr_multiple"]),
        min_atr_multiple=P.dec(s.get("min_atr_multiple", 1.0)),
        structure_buffer_atr=P.dec(s.get("structure_buffer_atr", 0.25)),
        use_structure=bool(s.get("use_structure", True)),
        r_multiples=tuple(P.dec(x) for x in t["r_multiples"]),
        min_rr=P.dec(t["min_rr"]))


def costs(cfg: TradingConfig, symbol: str) -> P.Costs:
    pc = cfg.account.get("plan_calc") or {}
    wide = (cfg.spreads.get("wide") or {}).get(symbol) if hasattr(cfg, "spreads") else None
    return P.Costs(
        slippage_pips_per_side=P.dec(pc.get("slippage_pips_per_side", 0.2)),
        conversion_stress=P.dec(pc.get("conversion_stress", 0.01)),
        wide_spread=P.dec(wide) if wide is not None else None,
        max_spread_x_wide=P.dec(pc.get("quote_max_spread_x_wide", 3)),
        max_gap_atr=P.dec(pc.get("quote_max_gap_atr", 3)),
        source="config/account.json plan_calc（研究上の仮定）・config/spreads.json wide")


def candidate(symbol: str, direction: str, analysis, series_m15,
              now: datetime) -> Optional[P.Candidate]:
    """分析から候補を作る。**向きは使う人が選んだもの。** 必要な値が無ければ None。"""
    m15 = analysis.indicators.get("M15")
    if m15 is None or not m15.close or not m15.atr14:
        return None
    long = direction == P.LONG
    ms = analysis.market_structure
    swing = (ms.last_swing_low if long else ms.last_swing_high) if ms else None
    barrier = m15.resistance if long else m15.support
    last = series_m15.last.timestamp if series_m15 is not None and series_m15.last else now
    return P.Candidate(
        pair=symbol, direction=direction, decided_at=now,
        valid_until=now + timedelta(minutes=5), last_bar_at=last,
        reference_close=P.dec(m15.close), atr=P.dec(m15.atr14),
        swing=P.dec(swing) if swing else None,
        barrier=P.dec(barrier) if barrier else None)


def quote(bid, ask, now: datetime, minutes: float) -> P.Quote:
    return P.Quote(bid=P.dec(bid), ask=P.dec(ask), observed_at=now,
                   valid_until=now + timedelta(minutes=minutes))


def build(cfg: TradingConfig, symbol: str, direction: str, bid, ask,
          analysis, series_m15, board: Dict, now: Optional[datetime] = None,
          balance: Optional[float] = None, risk_pct: Optional[float] = None,
          rates: Optional[Dict[str, float]] = None) -> Optional[P.Inputs]:
    """計算に渡す入力一式。候補を作れなければ None。"""
    now = now or datetime.now(timezone.utc)
    cand = candidate(symbol, direction, analysis, series_m15, now)
    if cand is None:
        return None
    minutes = float((cfg.account.get("plan_calc") or {}).get("quote_valid_minutes", 5))
    v = (board or {}).get("verdict") or {}
    manual_rates = {
        sym: P.Rate(value=P.dec(val), observed_at=now,
                    valid_until=now + timedelta(minutes=minutes), source="manual_mt4")
        for sym, val in (rates or {}).items()}
    return P.Inputs(
        quote=quote(bid, ask, now, minutes), candidate=cand,
        costs=costs(cfg, symbol), instrument=instrument(cfg, symbol),
        account=account(cfg, balance, risk_pct),
        board=P.Board(excluded=v.get("state") == "excluded",
                      reasons=tuple(v.get("exclude") or ())),
        rules=rules(cfg), rates=manual_rates, day_trade_cfg=cfg.filters)


def decision_dict(d: P.PlanDecision, i: Optional[P.Inputs]) -> Dict:
    """画面に返す形。**途中の案（PlanDraft）は PLAN_OK のときだけ。**"""
    out: Dict = {
        "status": d.status,
        "stops": [{"code": s.code, "message": s.message} for s in d.stops],
        "disclaimers": list(d.disclaimers) + [
            "呼値は公表スプレッドの桁からの推定です（業者の呼値としては未確認）"],
        "plan": None,
    }
    if i is not None:
        out["quote"] = {"bid": str(i.quote.bid), "ask": str(i.quote.ask),
                        "observed_at": i.quote.observed_at.isoformat(),
                        "valid_until": i.quote.valid_until.isoformat()}
        out["assumptions"] = {
            "slippage_pips_per_side": str(i.costs.slippage_pips_per_side),
            "conversion_stress": str(i.costs.conversion_stress)}
    p = d.plan
    if p is not None:
        out["plan"] = {
            "direction": p.direction, "entry": str(p.entry), "stop": str(p.stop),
            "stop_basis": p.stop_basis, "qty": p.qty, "units": p.units,
            "loss_jpy": p.loss_jpy, "risk_amount_jpy": int(p.risk_amount_jpy),
            "margin_jpy": p.margin_jpy, "margin_pct": f"{p.margin_pct:.1f}",
            "targets": [{"label": t.label, "price": str(t.price), "net_rr": str(t.net_rr),
                         "profit_jpy": t.profit_jpy, "capped_by": t.capped_by}
                        for t in p.targets],
            "notes": p.notes,
        }
    return out
