# -*- coding: utf-8 -*-
"""公開ページの計画計算に渡すスナップショット。

**公開ページ（GitHub Pages）だけで計画を出せるようにする。** これまで計画は PC の
サーバーが計算していたので、PC が止まっている・家の Wi-Fi の外・IP が変わった、の
どれでも「サーバーにつながらない」になった（2026-10-01 に利用者が指摘）。

ここでは、PC で計算した**候補**（終値・ATR・転換点・帯・刻限など、向きと bid・ask に
よらない部分）を JSON にして公開ページに埋め込む。ページの中のスクリプト
（``static/plancalc.cjs``）が、MT4 の bid・ask と選んだ向きから計画を出す。

- 数値は**文字列**で渡す（小数を2進数に直さない。ページ側も10進のまま計算する）。
- **写しの古さで止める。** 作ってから ``valid_minutes`` を過ぎたら、ページは計画を
  出さない（M5 の足が「3本ぶん＝15分」で古すぎになるのと同じ考え方）。
- 刻限は作った時点の「いまの取引日の刻限」を渡す。写しの寿命は日替わりを跨がない
  長さなので、ページ側で作り直す必要はない。

``inputs_from_snapshot`` は、同じスナップショットから Python の ``plan_calc`` の入力を
作る。ページのスクリプトと結果が一致することを試験で確かめるため。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, Optional

from . import day_trade
from . import plan_calc as P
from . import plan_inputs as pi
from .config import TradingConfig

VALID_MINUTES = 20


def _s(x) -> Optional[str]:
    return None if x is None else str(x)


def build(cfg: TradingConfig, symbol: str, analysis, series_m15, board: Dict,
          now: Optional[datetime] = None,
          valid_minutes: int = VALID_MINUTES) -> Optional[Dict]:
    """1銘柄ぶん。候補を作れなければ None（ページは「計画を出せません」と出す）。"""
    now = now or datetime.now(timezone.utc)
    long_c = pi.candidate(symbol, P.LONG, analysis, series_m15, now)
    short_c = pi.candidate(symbol, P.SHORT, analysis, series_m15, now)
    if long_c is None or short_c is None:
        return None
    ins, acc, rules, costs = (pi.instrument(cfg, symbol), pi.account(cfg),
                              pi.rules(cfg), pi.costs(cfg, symbol))
    deadline = day_trade.exit_deadline(cfg.filters, now)
    v = (board or {}).get("verdict") or {}
    dt = day_trade.settings(cfg.filters)
    return {
        "pair": symbol,
        "generated_at": now.isoformat(),
        "valid_minutes": valid_minutes,
        "last_bar_at": long_c.last_bar_at.isoformat(),
        "reference_close": _s(long_c.reference_close),
        "atr": _s(long_c.atr),
        "long": {"swing": _s(long_c.swing), "barrier": _s(long_c.barrier)},
        "short": {"swing": _s(short_c.swing), "barrier": _s(short_c.barrier)},
        "instrument": {
            "symbol": ins.symbol, "base": ins.base, "quote": ins.quote,
            "pip": _s(ins.pip), "price_tick": _s(ins.price_tick),
            "contract_unit": ins.contract_unit, "min_qty": ins.min_qty,
            "qty_step": ins.qty_step, "max_qty": ins.max_qty,
            "course": ins.course, "verified_on": ins.verified_on,
        },
        "account": {
            "balance": _s(acc.balance), "risk_pct": _s(acc.risk_pct),
            "max_risk_pct": _s(acc.max_risk_pct), "max_margin_pct": _s(acc.max_margin_pct),
            "margin_rate": _s(acc.margin_rate), "course": acc.course,
            "currency": acc.currency,
        },
        "rules": {
            "atr_multiple": _s(rules.atr_multiple),
            "min_atr_multiple": _s(rules.min_atr_multiple),
            "structure_buffer_atr": _s(rules.structure_buffer_atr),
            "use_structure": rules.use_structure,
            "r_multiples": [_s(x) for x in rules.r_multiples],
            "min_rr": _s(rules.min_rr),
            "barrier_offset_atr": _s(rules.barrier_offset_atr),
        },
        "costs": {
            "slippage_pips_per_side": _s(costs.slippage_pips_per_side),
            "conversion_stress": _s(costs.conversion_stress),
            "wide_spread": _s(costs.wide_spread),
            "max_spread_x_wide": _s(costs.max_spread_x_wide),
            "max_gap_atr": _s(costs.max_gap_atr),
        },
        "board": {"excluded": v.get("state") == "excluded",
                  "reasons": [str(x).replace("**", "") for x in v.get("exclude") or []]},
        "deadline": deadline.isoformat() if deadline else None,
        "next_rollover": (day_trade.rollover_start(now) + timedelta(days=1)).isoformat(),
        "min_minutes_to_exit": dt.get("min_minutes_to_exit", 60),
    }


def _d(x) -> Optional[Decimal]:
    return None if x is None else P.dec(x)


def inputs_from_snapshot(snap: Dict, direction: str, bid, ask, now: datetime,
                         day_trade_cfg: Dict) -> P.Inputs:
    """ページのスクリプトと同じ前提で、Python 側の入力を作る（一致を確かめる試験用）。"""
    gen = datetime.fromisoformat(snap["generated_at"])
    side = snap["long" if direction == P.LONG else "short"]
    i, a, r, c = snap["instrument"], snap["account"], snap["rules"], snap["costs"]
    return P.Inputs(
        quote=P.Quote(bid=P.dec(bid), ask=P.dec(ask), observed_at=now,
                      valid_until=now + timedelta(minutes=5)),
        candidate=P.Candidate(
            pair=snap["pair"], direction=direction, decided_at=gen,
            valid_until=gen + timedelta(minutes=snap["valid_minutes"]),
            last_bar_at=datetime.fromisoformat(snap["last_bar_at"]),
            reference_close=P.dec(snap["reference_close"]), atr=P.dec(snap["atr"]),
            swing=_d(side["swing"]), barrier=_d(side["barrier"])),
        costs=P.Costs(
            slippage_pips_per_side=P.dec(c["slippage_pips_per_side"]),
            conversion_stress=P.dec(c["conversion_stress"]),
            wide_spread=_d(c["wide_spread"]),
            max_spread_x_wide=P.dec(c["max_spread_x_wide"]),
            max_gap_atr=P.dec(c["max_gap_atr"])),
        instrument=P.Instrument(
            symbol=i["symbol"], base=i["base"], quote=i["quote"], pip=P.dec(i["pip"]),
            price_tick=P.dec(i["price_tick"]), contract_unit=i["contract_unit"],
            min_qty=i["min_qty"], qty_step=i["qty_step"], max_qty=i["max_qty"],
            course=i["course"], verified_on=i["verified_on"]),
        account=P.Account(
            balance=P.dec(a["balance"]), risk_pct=P.dec(a["risk_pct"]),
            max_risk_pct=P.dec(a["max_risk_pct"]), max_margin_pct=P.dec(a["max_margin_pct"]),
            margin_rate=P.dec(a["margin_rate"]), course=a["course"], currency=a["currency"]),
        board=P.Board(excluded=snap["board"]["excluded"],
                      reasons=tuple(snap["board"]["reasons"])),
        rules=P.Rules(
            atr_multiple=P.dec(r["atr_multiple"]),
            min_atr_multiple=P.dec(r["min_atr_multiple"]),
            structure_buffer_atr=P.dec(r["structure_buffer_atr"]),
            use_structure=r["use_structure"],
            r_multiples=tuple(P.dec(x) for x in r["r_multiples"]),
            min_rr=P.dec(r["min_rr"]),
            barrier_offset_atr=P.dec(r["barrier_offset_atr"])),
        rates={}, day_trade_cfg=day_trade_cfg)
