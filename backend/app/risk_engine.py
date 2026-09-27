# -*- coding: utf-8 -*-
"""Lot・証拠金・損益の計算（Phase 2）。

**ここを間違えると、決めたリスクそのものが狂う。** 方向が当たっていても
Lot が2倍なら損失も2倍になる。だからこの層では、分からない値を推測で
埋めない。埋められないときは ``UNKNOWN`` を返して建てさせない。

守っていること。

1. **円換算のレートが無ければ計算しない。** EURGBP の損益は GBP で出るので、
   円に直すには GBPJPY が要る。無いなら「いくら損するか」が分からない。
   分からないまま Lot を出すのは、リスク管理をしているふりになる。
2. **Lot は刻みに切り捨てる。** 切り上げると、決めたリスクを超える。
3. **証拠金は推定。** 業者の適用レートで変わるので、実際の取引画面が正。
4. **勝率を掛けた期待値は出さない。** 検証で優位性が測れていない以上、
   期待値を名乗れば嘘になる。出すのは「当たったとき」「外れたとき」の額だけ。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional

from .config import PairSpec, TradingConfig

PHASE = 2


class RiskError(ValueError):
    """計算できない・条件を満たさない。**推測で続けない。**"""


@dataclass
class Money:
    """金額。通貨を必ず持たせる。単位なしの数字を回さない。"""

    amount: float
    currency: str = "JPY"

    def as_dict(self) -> Dict:
        return {"amount": round(self.amount, 2), "currency": self.currency}


@dataclass
class SizingResult:
    """Lot と証拠金の計算結果。

    ``ok`` が False のときは **建てない**。理由が ``reasons`` に入る。
    """

    pair: str
    ok: bool = True
    qty: int = 0                  # 発注する「数量」（整数）
    contract_unit: int = 0        # 数量1あたりの通貨量
    lot: float = 0.0              # 1万通貨を1とした換算（損益・スワップ用）
    units: int = 0
    risk_amount: Optional[Money] = None
    pip_value_per_lot: Optional[Money] = None
    stop_distance_price: Optional[float] = None
    stop_distance_pips: Optional[float] = None
    required_margin: Optional[Money] = None
    margin_use_pct: Optional[float] = None
    conversion_pair: Optional[str] = None
    conversion_rate: Optional[float] = None
    reasons: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def block(self, reason: str) -> None:
        self.ok = False
        self.qty = 0
        self.lot = 0.0
        self.units = 0
        self.reasons.append(reason)

    def as_dict(self) -> Dict:
        return {
            "pair": self.pair, "ok": self.ok,
            "qty": self.qty, "contract_unit": self.contract_unit,
            "lot": self.lot, "units": self.units,
            "risk_amount": self.risk_amount.as_dict() if self.risk_amount else None,
            "pip_value_per_lot": (self.pip_value_per_lot.as_dict()
                                  if self.pip_value_per_lot else None),
            "stop_distance_price": self.stop_distance_price,
            "stop_distance_pips": (round(self.stop_distance_pips, 1)
                                   if self.stop_distance_pips is not None else None),
            "required_margin": (self.required_margin.as_dict()
                                if self.required_margin else None),
            "margin_use_pct": (round(self.margin_use_pct, 2)
                               if self.margin_use_pct is not None else None),
            "conversion_pair": self.conversion_pair,
            "conversion_rate": self.conversion_rate,
            "reasons": self.reasons, "notes": self.notes,
        }


# ------------------------------------------------------------ 換算


def conversion_symbol(currency: str, quote_currency: str = "JPY") -> Optional[str]:
    """``currency`` を ``quote_currency`` に直すためのペア名。

    同じ通貨なら換算は要らない（None を返す）。
    """
    currency = currency.upper()
    quote_currency = quote_currency.upper()
    if currency == quote_currency:
        return None
    return f"{currency}{quote_currency}"


def to_account_currency(amount: float, currency: str,
                        rates: Dict[str, float],
                        quote_currency: str = "JPY") -> float:
    """外貨の金額を口座通貨に直す。

    レートが無ければ ``RiskError``。**1.0 で代用しない。**
    代用すれば、EURGBP の損失が 1/180 に見える。
    """
    sym = conversion_symbol(currency, quote_currency)
    if sym is None:
        return amount
    rate = rates.get(sym)
    if rate is None or not math.isfinite(rate) or rate <= 0:
        raise RiskError(
            f"{currency} を {quote_currency} に直すレート（{sym}）がありません。"
            f"いくらの損益になるか分からないため、Lot を出しません")
    return amount * rate


# ------------------------------------------------------------ pip の価値


def pip_value_per_lot(spec: PairSpec, units_per_lot: int,
                      rates: Dict[str, float],
                      quote_currency: str = "JPY") -> Money:
    """1Lot・1pip あたりの金額（口座通貨）。

    pip の価値は**決済通貨（quote）**で出る。USDJPY なら JPY、EURUSD なら
    USD、EURGBP なら GBP。円口座ならそこから円に直す必要がある。
    """
    raw = spec.pip * units_per_lot            # quote 通貨での額
    converted = to_account_currency(raw, spec.quote, rates, quote_currency)
    return Money(converted, quote_currency)


def notional_in_account_currency(spec: PairSpec, units: float,
                                 rates: Dict[str, float],
                                 quote_currency: str = "JPY") -> float:
    """取引額（口座通貨）。証拠金の基になる。

    取引額は**基軸通貨（base）**の量。USDJPY 1万通貨なら 1万 USD で、
    円では USDJPY レート × 1万。
    """
    return to_account_currency(float(units), spec.base, rates, quote_currency)


# ------------------------------------------------------------ Lot


def contract_unit(spec: PairSpec, account: Dict) -> int:
    """数量1あたりの通貨量。

    **銘柄で違う。** 外為オンラインの1万通貨コースでは、南アフリカランド／円と
    メキシコペソ／円だけ 10万通貨（数量1）で、他の銘柄の10倍。揃えて扱うと、
    その2銘柄だけ建玉が10倍になり、決めたリスクが10倍ずれる。
    """
    course = account["courses"][account["course"]]
    overrides = course.get("unit_overrides") or {}
    return int(overrides.get(spec.symbol.upper(), course["unit"]))


def max_quantity(account: Dict) -> int:
    course = account["courses"][account["course"]]
    return int(course.get("max_qty", 100))


def _floor_to_step(value: float, step: float) -> float:
    """刻みに**切り捨てる**。切り上げると決めたリスクを超える。"""
    if step <= 0:
        return value
    return math.floor(value / step + 1e-9) * step


def size_position(
    spec: PairSpec,
    balance: float,
    risk_pct: float,
    entry: float,
    stop: float,
    rates: Dict[str, float],
    account: Dict,
) -> SizingResult:
    """1回の取引で失う額を決めてから、Lot を逆算する。

    順序が大事。「何Lot買えるか」から始めると、損失額が後追いになる。
    **先に「いくらまで失ってよいか」を決め、そこから Lot を出す。**
    """
    res = SizingResult(pair=spec.symbol)
    quote_currency = account.get("quote_currency", "JPY")
    accounting_unit = int(account["accounting_unit"])
    unit = contract_unit(spec, account)
    defaults = account["defaults"]

    # --- 入力の妥当性 ---
    if not math.isfinite(balance) or balance <= 0:
        res.block("資金が正の数ではありません")
        return res
    if not math.isfinite(risk_pct) or risk_pct <= 0:
        res.block("1回のリスク割合が正の数ではありません")
        return res
    max_risk = float(defaults["max_risk_per_trade_pct"])
    if risk_pct > max_risk:
        res.block(f"1回のリスク {risk_pct:.2f}% は上限 {max_risk:.2f}% を超えています")
        return res
    for name, v in (("建値", entry), ("損切り", stop)):
        if not math.isfinite(v) or v <= 0:
            res.block(f"{name}が正の有限の数ではありません")
            return res

    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        res.block("建値と損切りが同じです。損切り幅が決まりません")
        return res
    res.stop_distance_price = round(stop_distance, 6)
    res.stop_distance_pips = stop_distance / spec.pip

    # --- pip の価値（換算できないなら建てない）---
    try:
        pv = pip_value_per_lot(spec, accounting_unit, rates, quote_currency)
    except RiskError as exc:
        res.block(str(exc))
        return res
    res.pip_value_per_lot = pv
    sym = conversion_symbol(spec.quote, quote_currency)
    if sym:
        res.conversion_pair = sym
        res.conversion_rate = rates.get(sym)

    # --- 失ってよい額から Lot を逆算 ---
    risk_amount = balance * risk_pct / 100.0
    res.risk_amount = Money(risk_amount, quote_currency)
    loss_per_lot = res.stop_distance_pips * pv.amount
    if loss_per_lot <= 0:
        res.block("1Lot あたりの損失額が計算できません")
        return res

    # **発注は「数量」（整数）。** 0.1Lot のような刻みは1万通貨コースに無い。
    loss_per_qty = loss_per_lot * unit / accounting_unit
    raw_qty = risk_amount / loss_per_qty
    qty = int(math.floor(raw_qty + 1e-9))        # 切り上げない
    res.contract_unit = unit

    if qty < 1:
        res.block(
            f"許容損失 {risk_amount:,.0f}{quote_currency} では数量1"
            f"（{unit:,}通貨）も建てられません"
            f"（数量1で約 {loss_per_qty:,.0f}{quote_currency} の損失）。"
            f"損切り幅が資金に対して広すぎます")
        return res

    cap = max_quantity(account)
    if qty > cap:
        qty = cap
        res.notes.append(f"数量は上限 {cap} までです")

    res.qty = qty
    res.units = qty * unit
    res.lot = round(res.units / accounting_unit, 4)

    # --- 証拠金（推定）---
    try:
        notional = notional_in_account_currency(spec, res.units, rates,
                                                quote_currency)
    except RiskError as exc:
        res.block(str(exc))
        return res
    margin = notional * float(account["margin_rate"])
    res.required_margin = Money(margin, quote_currency)
    res.margin_use_pct = margin / balance * 100.0
    res.notes.append(
        "必要証拠金は推定です。業者の適用レートで変わるため、"
        "発注前に取引画面の値を確認してください")

    max_margin = float(defaults["max_margin_use_pct"])
    if res.margin_use_pct > max_margin:
        res.block(
            f"必要証拠金が資金の {res.margin_use_pct:.1f}% で、"
            f"上限 {max_margin:.1f}% を超えます")
        return res

    # 実際の損失額（切り捨て後の数量で再計算）
    actual_loss = res.stop_distance_pips * pv.amount * res.lot
    res.notes.append(
        f"損切りに当たったときの損失は約 {actual_loss:,.0f}{quote_currency}"
        f"（許容 {risk_amount:,.0f}{quote_currency}）")
    return res


def loss_at_stop(sizing: SizingResult) -> Optional[Money]:
    """損切りに当たったときの金額。"""
    if not sizing.ok or sizing.pip_value_per_lot is None:
        return None
    if sizing.stop_distance_pips is None:
        return None
    return Money(sizing.stop_distance_pips * sizing.pip_value_per_lot.amount
                 * sizing.lot, sizing.pip_value_per_lot.currency)


def profit_at(target_price: float, entry: float, is_long: bool,
              sizing: SizingResult, spec: PairSpec) -> Optional[Money]:
    """利確に届いたときの金額。**確率は掛けない。**

    「当たったらいくら」であって、期待値ではない。優位性が測れていない
    段階で期待値を名乗れば、それは嘘になる。
    """
    if not sizing.ok or sizing.pip_value_per_lot is None:
        return None
    move = (target_price - entry) if is_long else (entry - target_price)
    pips = move / spec.pip
    return Money(pips * sizing.pip_value_per_lot.amount * sizing.lot,
                 sizing.pip_value_per_lot.currency)


def spread_cost(spec: PairSpec, sizing: SizingResult,
                cfg: TradingConfig, wide: bool = False) -> Optional[Money]:
    """スプレッドで先に失う額。**費用を引かない損益は取引できない損益。**"""
    if not sizing.ok or sizing.pip_value_per_lot is None:
        return None
    pips = cfg.spread_price(spec.symbol, wide) / spec.pip
    return Money(-pips * sizing.pip_value_per_lot.amount * sizing.lot,
                 sizing.pip_value_per_lot.currency)


@dataclass
class SwapInfo:
    """スワップの見立て。

    ``state`` は ``KNOWN`` / ``UNKNOWN`` / ``STALE``。
    **UNKNOWN を 0 円として扱ってはいけない。** 0 円と書けば「費用ゼロ」に
    見えるが、実際は分かっていないだけ。
    """

    state: str = "UNKNOWN"
    per_lot_per_day: Optional[Money] = None
    per_position_per_day: Optional[Money] = None
    source: Optional[str] = None
    as_of: Optional[str] = None
    age_days: Optional[int] = None
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict:
        return {
            "state": self.state,
            "per_lot_per_day": (self.per_lot_per_day.as_dict()
                                if self.per_lot_per_day else None),
            "per_position_per_day": (self.per_position_per_day.as_dict()
                                     if self.per_position_per_day else None),
            "source": self.source, "as_of": self.as_of,
            "age_days": self.age_days, "notes": self.notes,
        }


def swap_estimate(spec: PairSpec, is_long: bool, swap_cfg: Dict,
                  lot: float = 1.0, quote_currency: str = "JPY",
                  today: Optional[date] = None) -> SwapInfo:
    """買い持ち／売り持ちのスワップ。

    **向きで額も符号も変わる。** 取り違えると、受け取るはずが払うことに
    なる。だから ``is_long`` を必ず受け取る。

    表が古ければ ``STALE`` にする。スワップは毎日変わるので、日付の無い
    数字や古い数字を現在の値として出せない。
    """
    info = SwapInfo()
    if not swap_cfg.get("available", False):
        info.notes.append(
            "スワップ表を取り込んでいないため不明です。0円ではありません。"
            "scripts/import_swap.py で業者の表を取り込めます")
        return info

    row = (swap_cfg.get("table") or {}).get(spec.symbol)
    if not isinstance(row, dict):
        info.notes.append(
            f"{spec.symbol} は取り込んだ表に含まれていないため不明です")
        return info

    key = "buy" if is_long else "sell"
    value = row.get(key)
    if value is None or not math.isfinite(float(value)):
        info.notes.append(f"{spec.symbol} の{key}側の値がありません")
        return info

    info.source = swap_cfg.get("source") or None
    info.as_of = swap_cfg.get("as_of") or None
    info.per_lot_per_day = Money(float(value), quote_currency)
    info.per_position_per_day = Money(float(value) * lot, quote_currency)
    info.state = "KNOWN"

    # --- 古さ ---
    limit = int(swap_cfg.get("stale_after_days", 0) or 0)
    if info.as_of:
        try:
            as_of = datetime.strptime(info.as_of, "%Y-%m-%d").date()
            info.age_days = ((today or date.today()) - as_of).days
        except ValueError:
            info.notes.append("表の日付が読めません")
    else:
        info.notes.append("表に日付がありません。現在の値かどうか確かめられません")
        info.state = "STALE"

    if limit and info.age_days is not None and info.age_days > limit:
        info.state = "STALE"
        info.notes.append(
            f"表が {info.age_days} 日前のものです（{limit} 日まで）。"
            f"スワップは毎日変わるので、取り直してください")

    info.notes.append(
        "水曜（一部は木曜）をまたぐと3日分付くことがあります。"
        "何日分になるかはこのアプリでは判定しません")
    return info
