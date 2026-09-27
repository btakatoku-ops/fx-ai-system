# -*- coding: utf-8 -*-
"""建値・損切り・利確の組み立て（Phase 2）。

**この層がいちばん誤解を招きやすい。** 数字が具体的になるほど、根拠が
あるように見える。147.760〜147.810 と書けば、そこに意味があるように読める。

だから、ここで出すものと出さないものをはっきり分けてある。

出すもの
  - 建値の範囲（1点ではなく帯。1点で当てられるふりをしない）
  - 損切り（構造の外側か ATR の倍数。**どちらを採ったかを明示する**）
  - 利確 TP1/TP2/TP3（リスクの倍数。抵抗帯に当たるなら手前へ寄せる）
  - 当たったときの金額・外れたときの金額・リスクリワード比

出さないもの
  - **勝率を掛けた期待値。** 優位性が測れていないので、名乗れば嘘になる。
  - **「信頼度○○%」。** 点数は勝率ではない。
  - **保有時間の目安。** 検証していない。

Phase 1 の検証で分かっていること（実データ26銘柄・約2か月半）:
建てたもの 勝率31.6%・平均R −0.117、コイン投げ 31.3%・−0.115。
**つまり現時点のシグナルには測定可能な優位性が無い。** ここで作る計画は
「その方向に賭けるならこの幅・この枚数」という算術であって、
儲かる根拠ではない。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .config import PairSpec, TradingConfig
from .models import AnalysisResult, Direction, Signal
from .risk_engine import (
    Money,
    SizingResult,
    SwapInfo,
    loss_at_stop,
    profit_at,
    size_position,
    spread_cost,
    swap_estimate,
)

PHASE = 2


@dataclass
class Target:
    """利確の1段。"""

    label: str
    price: float
    r_multiple: float
    profit: Optional[Money] = None
    capped_by: Optional[str] = None      # 抵抗帯などで手前に寄せた場合

    def as_dict(self) -> Dict:
        return {
            "label": self.label, "price": round(self.price, 6),
            "r_multiple": round(self.r_multiple, 3),
            "profit": self.profit.as_dict() if self.profit else None,
            "capped_by": self.capped_by,
        }


@dataclass
class TradePlan:
    """1銘柄ぶんの建玉計画。

    ``ok`` が False のときは **建てない**。理由を必ず持たせる。
    """

    pair: str
    ok: bool = False
    direction: str = "NEUTRAL"
    signal: str = "NO_TRADE"
    reference_price: Optional[float] = None
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    stop: Optional[float] = None
    stop_basis: Optional[str] = None
    targets: List[Target] = field(default_factory=list)
    risk_reward: Optional[float] = None
    sizing: Optional[SizingResult] = None
    loss_if_stopped: Optional[Money] = None
    spread_cost: Optional[Money] = None
    swap: Optional[SwapInfo] = None
    swap_per_day: Optional[Money] = None
    swap_state: str = "UNKNOWN"
    valid_until: Optional[datetime] = None
    reasons: List[str] = field(default_factory=list)
    blocked_reasons: List[str] = field(default_factory=list)
    disclaimers: List[str] = field(default_factory=list)

    def block(self, reason: str) -> "TradePlan":
        self.ok = False
        self.blocked_reasons.append(reason)
        return self

    def as_dict(self) -> Dict:
        return {
            "pair": self.pair, "ok": self.ok,
            "direction": self.direction, "signal": self.signal,
            "reference_price": self.reference_price,
            "entry_low": (round(self.entry_low, 6)
                          if self.entry_low is not None else None),
            "entry_high": (round(self.entry_high, 6)
                           if self.entry_high is not None else None),
            "stop": round(self.stop, 6) if self.stop is not None else None,
            "stop_basis": self.stop_basis,
            "targets": [t.as_dict() for t in self.targets],
            "risk_reward": (round(self.risk_reward, 2)
                            if self.risk_reward is not None else None),
            "sizing": self.sizing.as_dict() if self.sizing else None,
            "loss_if_stopped": (self.loss_if_stopped.as_dict()
                                if self.loss_if_stopped else None),
            "spread_cost": (self.spread_cost.as_dict()
                            if self.spread_cost else None),
            "swap_per_day": (self.swap_per_day.as_dict()
                             if self.swap_per_day else None),
            "swap_state": self.swap_state,
            "swap": self.swap.as_dict() if self.swap else None,
            "valid_until": (self.valid_until.isoformat()
                            if self.valid_until else None),
            "reasons": self.reasons,
            "blocked_reasons": self.blocked_reasons,
            "disclaimers": self.disclaimers,
        }


_NO_EDGE_NOTE = (
    "実データの検証では、この仕組みの判断とコイン投げに差がありませんでした"
    "（勝率31.6% 対 31.3%、平均Rはどちらも −0.11）。"
    "**この計画は「その方向に賭けるならこの幅・この枚数」という算術であって、"
    "儲かる根拠ではありません。**")

_MARGIN_NOTE = (
    "必要証拠金は推定です。業者の適用レートで変わるため、発注前に取引画面で"
    "確認してください。ロスカット水準の判定もこのアプリでは行いません。")


def _pick_stop(analysis: AnalysisResult, is_long: bool, atr: float,
               cfg_stop: Dict) -> tuple[float, str]:
    """損切りをどこに置くか。構造の外側を優先する。

    ATR の倍数だけで置くと、直近の転換点の内側に入ることがある。そこは
    値動きが素直に届く場所なので、**構造の外側に置けるならそちらを採る**。
    どちらを採ったかは必ず返す（利用者が判断を追えるように）。
    """
    m15 = analysis.indicators.get("M15")
    close = (m15.close if m15 and m15.close else None)
    atr_stop = (close - cfg_stop["atr_multiple"] * atr if is_long
                else close + cfg_stop["atr_multiple"] * atr)

    if not cfg_stop.get("use_structure", True) or close is None:
        return atr_stop, f"ATR の {cfg_stop['atr_multiple']} 倍"

    swing = (analysis.market_structure.last_swing_low if is_long
             else analysis.market_structure.last_swing_high)
    if swing is None:
        return atr_stop, f"ATR の {cfg_stop['atr_multiple']} 倍（転換点なし）"

    buffer = cfg_stop.get("structure_buffer_atr", 0.25) * atr
    structure_stop = (swing - buffer) if is_long else (swing + buffer)

    # 構造側のほうが遠い（=安全側）なら、そちらを採る
    min_dist = cfg_stop.get("min_atr_multiple", 1.0) * atr
    if is_long:
        if structure_stop < atr_stop and (close - structure_stop) >= min_dist:
            return structure_stop, "直近の転換安値の外側"
    else:
        if structure_stop > atr_stop and (structure_stop - close) >= min_dist:
            return structure_stop, "直近の転換高値の外側"
    return atr_stop, f"ATR の {cfg_stop['atr_multiple']} 倍"


def build_plan(
    analysis: AnalysisResult,
    spec: PairSpec,
    cfg: TradingConfig,
    rates: Dict[str, float],
    balance: Optional[float] = None,
    risk_pct: Optional[float] = None,
    now: Optional[datetime] = None,
) -> TradePlan:
    """分析結果から建玉計画を組む。

    **分析が BUY/SELL でないなら、計画は作らない。** 見送りの場面に
    「もし買うなら」の数字を添えると、見送りの意味が薄れる。
    """
    now = now or datetime.now(timezone.utc)
    account = cfg.account
    defaults = account["defaults"]
    balance = float(balance if balance is not None else defaults["balance"])
    risk_pct = float(risk_pct if risk_pct is not None
                     else defaults["risk_per_trade_pct"])

    plan = TradePlan(pair=spec.symbol, direction=analysis.direction.value,
                     signal=analysis.signal.value,
                     valid_until=analysis.valid_until)
    plan.disclaimers = [_NO_EDGE_NOTE, _MARGIN_NOTE]

    # --- 期限を先に見る ---
    if analysis.is_expired(now):
        return plan.block("分析の有効期限が切れています。作り直してください")

    # --- 建てない場面には計画を作らない ---
    if analysis.signal not in (Signal.BUY, Signal.SELL):
        reason = (analysis.invalidation_reasons[0]
                  if analysis.invalidation_reasons else "売買の条件を満たしていません")
        return plan.block(f"{analysis.signal.value}: {reason}")
    if analysis.direction is Direction.NEUTRAL:
        return plan.block("向きが定まっていません")

    is_long = analysis.direction is Direction.LONG

    m15 = analysis.indicators.get("M15")
    atr = m15.atr14 if m15 else None
    close = m15.close if m15 else None
    if not atr or not math.isfinite(atr) or atr <= 0:
        return plan.block("ATR が計算できず、損切り幅を決められません")
    if not close or not math.isfinite(close) or close <= 0:
        return plan.block("現在値が取れません")
    plan.reference_price = round(close, 6)

    # --- 損切り ---
    stop, basis = _pick_stop(analysis, is_long, atr, account["stop"])
    if (is_long and stop >= close) or (not is_long and stop <= close):
        return plan.block("損切りが現在値の反対側に来ています")
    plan.stop, plan.stop_basis = round(stop, 6), basis
    risk_distance = abs(close - stop)

    # --- 建値の帯（1点で当てられるふりをしない）---
    half = 0.15 * atr
    plan.entry_low = round(close - half, 6)
    plan.entry_high = round(close + half, 6)

    # --- Lot と証拠金 ---
    sizing = size_position(spec, balance, risk_pct, close, stop, rates, account)
    plan.sizing = sizing
    if not sizing.ok:
        for r in sizing.reasons:
            plan.block(r)
        return plan

    # --- 利確（抵抗帯に当たるなら手前へ寄せる）---
    barrier = (m15.resistance if is_long else m15.support) if m15 else None
    for i, mult in enumerate(account["targets"]["r_multiples"], start=1):
        price = (close + mult * risk_distance if is_long
                 else close - mult * risk_distance)
        capped = None
        if barrier is not None:
            beyond = price > barrier if is_long else price < barrier
            if beyond:
                # 帯のわずか手前で止める。抜ける前提の数字を置かない。
                price = (barrier - 0.1 * atr) if is_long else (barrier + 0.1 * atr)
                capped = "抵抗帯" if is_long else "支持帯"
        move = (price - close) if is_long else (close - price)
        if move <= 0:
            continue
        # 同じ帯で止まった段は畳む。**3段あるように見せて実質1段は誤解を招く。**
        if plan.targets and abs(price - plan.targets[-1].price) < spec.pip * 0.5:
            plan.targets[-1].capped_by = capped or plan.targets[-1].capped_by
            continue
        t = Target(label=f"TP{len(plan.targets) + 1}", price=price,
                   r_multiple=move / risk_distance, capped_by=capped)
        t.profit = profit_at(price, close, is_long, sizing, spec)
        plan.targets.append(t)

    if not plan.targets:
        return plan.block("利確を置ける余地がありません（帯が近すぎます）")

    if len(plan.targets) < len(account["targets"]["r_multiples"]):
        plan.disclaimers.append(
            f"利確は {len(plan.targets)} 段しか置けませんでした"
            f"（帯が近く、以降の段が同じ値段になるため畳みました）")

    plan.risk_reward = plan.targets[0].r_multiple
    min_rr = float(account["targets"]["min_rr"])
    if plan.risk_reward < min_rr:
        return plan.block(
            f"TP1 までの比が {plan.risk_reward:.2f} で、下限 {min_rr:.2f} に届きません")

    # --- 金額 ---
    plan.loss_if_stopped = loss_at_stop(sizing)
    plan.spread_cost = spread_cost(spec, sizing, cfg)
    # スワップは向きで額も符号も変わる。取り違えると受け取るはずが払うことになる。
    sw = swap_estimate(spec, is_long, cfg.swap, lot=sizing.lot,
                       quote_currency=account.get("quote_currency", "JPY"))
    plan.swap = sw
    plan.swap_state = sw.state
    plan.swap_per_day = sw.per_position_per_day
    if sw.state != "KNOWN":
        plan.disclaimers.extend(sw.notes)
    elif sw.notes:
        plan.disclaimers.append(sw.notes[-1])       # 3日分の注記だけ載せる

    plan.reasons = list(analysis.reasons)
    plan.ok = True
    return plan
