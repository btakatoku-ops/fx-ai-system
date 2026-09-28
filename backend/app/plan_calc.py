# -*- coding: utf-8 -*-
"""建玉計画の計算（Phase 2・P2-1）。**純粋な関数だけ。I/O をしない。**

設計は ``docs/phase2-design.md``。ここは §3 の計算順序と §4 の停止表の実装で、
**どこからも呼ばれていない**（画面・API・朝の確認・記録は、いままでどおり
``trade_plan.build_plan`` を使う）。つなぐのは P2-4 で、別に承認を取ってから。

決まっていること（2026-09-28 利用者の決定）

- **向きは使う人が選ぶ。** エンジンの BUY/SELL や点数は計画に入らない。
- **基準価格は MT4 から手で入れた bid・ask。** 無ければ止める。足の終値で代用しない。
- 損切り・利確はいまの規則（構造の外側か ATR×2、1.5R/2.5R/3.5R を帯の手前に）。
  ただし RR の下限は**費用込み**（netRR）で判定する。
- 滑り・換算ストレス・手入力の有効期限は**研究上の仮定**で、引数で受け取る。

数値はすべて ``Decimal``。丸めは**不利な側へ**（損失は大きく、利益と数量は小さく）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from . import day_trade

D = Decimal
LONG, SHORT = "LONG", "SHORT"

NO_EDGE_NOTE = (
    "この計画は「その向きで建てるなら、この幅・この数量」という算術で、"
    "儲かる根拠ではありません。向きを決めたのはご自身です。")
ASSUMPTION_NOTE = (
    "滑りと円換算の余裕は研究上の仮定で、業者の数字ではありません。"
    "証拠金は推定です。発注前に取引画面の値を確かめてください。")


# ------------------------------------------------------------ 停止理由


@dataclass(frozen=True)
class Stop:
    """止めた理由。**機械で数えられるコード**と、人が読む文。"""

    code: str
    message: str


# ------------------------------------------------------------ 入力（スナップショット）


@dataclass(frozen=True)
class Quote:
    """MT4 から手で入れた気配値。"""

    bid: Decimal
    ask: Decimal
    observed_at: datetime
    valid_until: datetime
    source: str = "manual_mt4"
    price_basis: str = "bid_ask"

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class Candidate:
    """TechnicalCandidate。向きは使う人が選んだもの。"""

    pair: str
    direction: str                      # LONG / SHORT
    decided_at: datetime                # 判定した時刻
    valid_until: datetime               # 候補（構造・ATR）を使ってよい期限
    last_bar_at: datetime               # 使った足のうち最後のものの時刻
    reference_close: Decimal            # 取り込んだ足の終値（手入力の照合用）
    atr: Decimal
    swing: Optional[Decimal] = None     # 買いなら直近の転換安値、売りなら転換高値
    barrier: Optional[Decimal] = None   # 買いなら抵抗帯、売りなら支持帯
    config_version: str = ""


@dataclass(frozen=True)
class Costs:
    slippage_pips_per_side: Decimal
    conversion_stress: Decimal          # 0.01 = 1%
    wide_spread: Optional[Decimal]      # spreads.json の wide（価格の単位）。無ければ照合しない
    max_spread_x_wide: Decimal = D(3)
    max_gap_atr: Decimal = D(3)
    source: str = ""


@dataclass(frozen=True)
class Rate:
    """円換算レート（例: USDJPY）。期限と出典を必ず持つ。"""

    value: Decimal
    observed_at: datetime
    valid_until: datetime
    source: str = ""


@dataclass(frozen=True)
class Instrument:
    symbol: str
    base: str
    quote: str
    pip: Decimal
    price_tick: Decimal
    contract_unit: int                  # 数量1あたりの通貨量
    min_qty: int = 1
    qty_step: int = 1
    max_qty: int = 100
    course: str = ""
    verified_on: Optional[str] = None   # 取引要綱で確かめた日
    source: str = ""


@dataclass(frozen=True)
class Account:
    balance: Decimal                    # 円
    risk_pct: Decimal
    max_risk_pct: Decimal
    max_margin_pct: Decimal
    margin_rate: Decimal
    course: str
    currency: str = "JPY"


@dataclass(frozen=True)
class Rules:
    """いまの規則（config/account.json の stop・targets）。"""

    atr_multiple: Decimal = D("2.0")
    min_atr_multiple: Decimal = D("1.0")
    structure_buffer_atr: Decimal = D("0.25")
    use_structure: bool = True
    r_multiples: Tuple[Decimal, ...] = (D("1.5"), D("2.5"), D("3.5"))
    min_rr: Decimal = D("1.5")
    barrier_offset_atr: Decimal = D("0.1")


@dataclass(frozen=True)
class Board:
    """その時点のボード。**除外の日は、向きを選んでも計画を出さない。**"""

    excluded: bool
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Inputs:
    quote: Quote
    candidate: Candidate
    costs: Costs
    instrument: Instrument
    account: Account
    board: Board
    rules: Rules = Rules()
    rates: Dict[str, Rate] = field(default_factory=dict)
    day_trade_cfg: Dict = field(default_factory=dict)   # filters.json（day_trade を含む）


# ------------------------------------------------------------ 出力


@dataclass
class Target:
    label: str
    price: Decimal
    net_rr: Decimal
    profit_jpy: int
    capped_by: Optional[str] = None


@dataclass
class PlanDraft:
    """**画面に出さない**計算途中の案。gate を通る前。"""

    pair: str
    direction: str
    entry: Decimal
    stop: Decimal
    stop_basis: str
    targets: List[Target]
    qty: int
    units: int
    loss_jpy: int
    risk_amount_jpy: Decimal
    margin_jpy: int
    margin_pct: Decimal
    loss_distance: Decimal              # 滑り込みの価格差
    notes: List[str] = field(default_factory=list)

    @property
    def net_rr(self) -> Decimal:
        return self.targets[0].net_rr


@dataclass
class PlanDecision:
    """最終判定。画面に出すのはこちらだけ。"""

    status: str                         # PLAN_OK / NO_TRADE
    stops: List[Stop]
    plan: Optional[PlanDraft]           # PLAN_OK のときだけ
    disclaimers: List[str]

    @property
    def codes(self) -> List[str]:
        return [s.code for s in self.stops]


# ------------------------------------------------------------ 丸め


def _q(x: Decimal, tick: Decimal, up: bool) -> Decimal:
    """呼値に量子化。``up`` なら切り上げ、そうでなければ切り下げ。"""
    n = (x / tick).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return n * tick


def _yen_up(x: Decimal) -> int:
    return int(x.to_integral_value(rounding=ROUND_CEILING))


def _yen_down(x: Decimal) -> int:
    return int(x.to_integral_value(rounding=ROUND_FLOOR))


def _finite_pos(*xs) -> bool:
    for x in xs:
        if x is None:
            return False
        if not isinstance(x, Decimal) or not x.is_finite() or x <= 0:
            return False
    return True


# ------------------------------------------------------------ A 入力の検証


def _check_inputs(i: Inputs, now: datetime) -> List[Stop]:
    q, c, cost, ins, acc = i.quote, i.candidate, i.costs, i.instrument, i.account
    out: List[Stop] = []
    if not _finite_pos(q.bid, q.ask, c.atr, c.reference_close, acc.balance,
                       acc.risk_pct, ins.pip, ins.price_tick):
        return [Stop("PLAN_INVALID_NUMBER",
                     "価格・ATR・資金・リスク割合に、正の有限の数でないものがあります")]
    if c.direction not in (LONG, SHORT):
        out.append(Stop("DIRECTION_UNSET", "向きが選ばれていません"))
    if now >= c.valid_until:
        out.append(Stop("PLAN_EXPIRED", "候補（構造・ATR）の有効期限が切れています"))
    if c.last_bar_at > c.decided_at:
        out.append(Stop("FUTURE_BAR_USED",
                        "判定した時刻より後の足を使っています"))
    if q.price_basis != "bid_ask":
        out.append(Stop("QUOTE_MISSING",
                        "取引画面の bid・ask が入っていません（足の終値では代用しません）"))
    if now >= q.valid_until:
        out.append(Stop("QUOTE_STALE",
                        "手で入れた bid・ask の有効期限が切れています。入れ直してください"))
    if q.bid >= q.ask:
        out.append(Stop("QUOTE_INVALID",
                        "売値（Bid）が買値（Ask）以上になっています。MT4 の気配値表示の"
                        "2つの値をそのまま入れてください（買値の方が少し高い。向きに関係なく同じ）"))
    elif cost.wide_spread is not None and (q.ask - q.bid) > cost.wide_spread * cost.max_spread_x_wide:
        out.append(Stop("QUOTE_INVALID",
                        f"売値と買値の差 {q.ask - q.bid} が、ふだんの広いとき（{cost.wide_spread}）の"
                        f"{cost.max_spread_x_wide} 倍を超えています。建値や利確ではなく、"
                        f"気配値表示の売値・買値を入れてください（指標時の拡大なら、落ち着いてから）"))
    if abs(q.mid - c.reference_close) >= c.atr * cost.max_gap_atr:
        out.append(Stop("QUOTE_MISMATCH",
                        "手で入れた値が、取り込んだ足の終値から大きく離れています"
                        "（別の銘柄の値を入れた疑い）"))
    return out


# ------------------------------------------------------------ 換算


def _rate(i: Inputs, ccy: str, now: datetime, for_margin: bool
          ) -> Tuple[Optional[Decimal], Optional[Stop]]:
    """``ccy`` を円に直すレート（ストレス前）。止める理由があれば返す。"""
    if ccy == i.account.currency:
        return D(1), None
    sym = f"{ccy}{i.account.currency}"
    if sym == i.instrument.symbol:
        # 自分自身（GBPJPY の証拠金など）。手入力の値を使う。**ask の方＝大きく見せる側**
        return i.quote.ask, None
    r = i.rates.get(sym)
    if r is None:
        return None, Stop("CONVERSION_MISSING",
                          f"{ccy} を円に直すレート（{sym}）がありません。1.0 では代用しません")
    if now >= r.valid_until:
        return None, Stop("CONVERSION_STALE", f"{sym} のレートの有効期限が切れています")
    if not _finite_pos(r.value):
        return None, Stop("PLAN_INVALID_NUMBER", f"{sym} のレートが正の有限の数ではありません")
    return r.value, None


# ------------------------------------------------------------ B〜I 計算


def draft(i: Inputs, now: datetime) -> Tuple[Optional[PlanDraft], List[Stop]]:
    """計算する。途中で止まれば (None, 理由)。**gate は通していない。**"""
    stops = _check_inputs(i, now)
    if stops:
        return None, stops
    q, c, cost, ins, acc, r = (i.quote, i.candidate, i.costs, i.instrument,
                               i.account, i.rules)
    long = c.direction == LONG
    tick = ins.price_tick

    # --- C 実効建値（不利側）---
    entry = _q(q.ask, tick, up=True) if long else _q(q.bid, tick, up=False)

    # --- D 損切り（いまの規則。構造が ATR より遠いときだけ構造を採る）---
    atr_stop = entry - r.atr_multiple * c.atr if long else entry + r.atr_multiple * c.atr
    stop, basis = atr_stop, f"ATR の {r.atr_multiple} 倍"
    if r.use_structure and c.swing is not None:
        s = (c.swing - r.structure_buffer_atr * c.atr if long
             else c.swing + r.structure_buffer_atr * c.atr)
        farther = s < atr_stop if long else s > atr_stop
        if farther and abs(entry - s) >= r.min_atr_multiple * c.atr:
            stop, basis = s, ("直近の転換安値の外側" if long else "直近の転換高値の外側")
    elif c.swing is None:
        basis += "（転換点なし）"
    stop = _q(stop, tick, up=not long)              # 建値から遠ざかる側
    if (long and stop >= entry) or (not long and stop <= entry) or stop <= 0:
        return None, [Stop("STOP_INVALID", "損切りが建値の反対側に来ています")]
    if abs(entry - stop) < r.min_atr_multiple * c.atr:
        return None, [Stop("STOP_INVALID", "損切りが近すぎます（ATR の最小倍数に届きません）")]

    # --- F 費用込みの距離（スプレッドは建値を ask/bid に置いた時点で入っている）---
    slip = cost.slippage_pips_per_side * ins.pip
    gross_risk = abs(entry - stop)
    loss_dist = gross_risk + 2 * slip

    # --- E 利確（R は**費用込み**で測る。帯を越えるなら手前へ）---
    # TP1 を「総額で 1.5R」に置くと、滑りが少しでもあれば費用込みの比は必ず
    # 1.5 を割り、計画が1つも出なくなる。そこで「費用込みで m 倍」になる値に
    # 置く：利益距離（滑り込み）＝ m × 損失距離（滑り込み）。
    # 丸めは、帯で止めた段は建値の側（帯を越えない）、そうでない段は外側
    # （丸めで m 倍を割らない）。利益額はその丸めた値で計算するので、多く見せない。
    raw: List[Tuple[Decimal, Optional[str]]] = []
    for m in r.r_multiples:
        dist = m * loss_dist + 2 * slip
        price = entry + dist if long else entry - dist
        capped = None
        if c.barrier is not None and (price > c.barrier if long else price < c.barrier):
            price = (c.barrier - r.barrier_offset_atr * c.atr if long
                     else c.barrier + r.barrier_offset_atr * c.atr)
            capped = "抵抗帯" if long else "支持帯"
            price = _q(price, tick, up=not long)    # 帯の側へは越えない
        else:
            price = _q(price, tick, up=long)        # m 倍を割らない側
        if raw and price == raw[-1][0]:
            continue                                  # 同じ値の段は畳む
        raw.append((price, capped))
    targets: List[Target] = []

    # --- G 円換算（損失は大きく、利益は小さく）---
    base_rate, err = _rate(i, ins.quote, now, for_margin=False)
    if err:
        return None, [err]
    loss_rate = base_rate * (1 + cost.conversion_stress)
    gain_rate = base_rate * (1 - cost.conversion_stress)

    # --- H 数量（許容損失から逆算。切り捨て）---
    if acc.risk_pct > acc.max_risk_pct:
        return None, [Stop("RISK_ABOVE_CAP",
                           f"1回のリスク {acc.risk_pct}% が上限 {acc.max_risk_pct}% を超えています")]
    risk_amount = acc.balance * acc.risk_pct / 100
    loss_per_qty = loss_dist * ins.contract_unit * loss_rate
    qty = int((risk_amount / loss_per_qty).to_integral_value(rounding=ROUND_FLOOR))
    qty -= qty % ins.qty_step
    notes: List[str] = []
    if qty > ins.max_qty:
        qty = ins.max_qty - ins.max_qty % ins.qty_step
        notes.append(f"数量は上限 {ins.max_qty} までです")
    # 円に丸めた損失が許容を超えるなら、1刻み減らす（丸めで上限を越えない）
    while qty >= ins.min_qty and _yen_up(loss_per_qty * qty) > risk_amount:
        qty -= ins.qty_step
    if qty < ins.min_qty:
        return None, [Stop("QTY_BELOW_MIN",
                           f"許容損失 {_yen_down(risk_amount):,}円 では数量{ins.min_qty}"
                           f"（{ins.contract_unit * ins.min_qty:,}通貨）も建てられません"
                           f"（数量1で約 {_yen_up(loss_per_qty):,}円 の損失）")]
    units = qty * ins.contract_unit
    loss_jpy = _yen_up(loss_dist * units * loss_rate)

    for n, (price, capped) in enumerate(raw, start=1):
        gain = (price - entry if long else entry - price) - 2 * slip
        if gain <= 0:
            continue
        net = (gain / loss_dist).quantize(D("0.01"), rounding=ROUND_FLOOR)
        targets.append(Target(label=f"TP{len(targets) + 1}", price=price,
                              net_rr=net, profit_jpy=_yen_down(gain * units * gain_rate),
                              capped_by=capped))
    if not targets:
        return None, [Stop("TARGET_NO_ROOM", "利確を置ける余地がありません（帯が近すぎます）")]
    first_gain = (targets[0].price - entry if long else entry - targets[0].price) - 2 * slip
    if first_gain / loss_dist < r.min_rr:          # 判定は丸める前の値で
        return None, [Stop("NET_RR_BELOW_MIN",
                           f"費用込みの TP1 までの比が {targets[0].net_rr} で、"
                           f"下限 {r.min_rr} に届きません")]

    # --- I 証拠金（基軸通貨の取引額 × 証拠金率。大きく見せる側）---
    m_rate, err = _rate(i, ins.base, now, for_margin=True)
    if err:
        return None, [err]
    margin = _yen_up(units * m_rate * (1 + cost.conversion_stress) * acc.margin_rate)
    margin_pct = D(margin) / acc.balance * 100
    if margin_pct > acc.max_margin_pct:
        return None, [Stop("MARGIN_ABOVE_CAP",
                           f"必要証拠金（推定）が資金の {margin_pct:.1f}% で、"
                           f"上限 {acc.max_margin_pct}% を超えます")]

    return PlanDraft(
        pair=c.pair, direction=c.direction, entry=entry, stop=stop, stop_basis=basis,
        targets=targets, qty=qty, units=units, loss_jpy=loss_jpy,
        risk_amount_jpy=risk_amount, margin_jpy=margin, margin_pct=margin_pct,
        loss_distance=loss_dist, notes=notes), []


# ------------------------------------------------------------ J 独立 gate


def gate(i: Inputs, now: datetime) -> List[Stop]:
    """**計算結果を見ずに**、入力と時刻だけから止める理由を集める。"""
    out: List[Stop] = []
    if i.board.excluded:
        why = "・".join(i.board.reasons) or "理由の記載なし"
        out.append(Stop("BOARD_EXCLUDED",
                        f"ボードが除外の日です（{why}）。向きを選んでも計画は出しません"))
    ok, why = day_trade.can_open(i.day_trade_cfg, now)
    if not ok:
        out.append(Stop("DEADLINE", (why or "").replace("**", "")))
    deadline = day_trade.exit_deadline(i.day_trade_cfg, now)
    if deadline is not None:
        next_roll = day_trade.rollover_start(now) + timedelta(days=1)
        if deadline > next_roll:
            out.append(Stop("ROLLOVER_CROSS",
                            "手仕舞いの刻限が日替わりを跨ぎます（スワップが付く）"))
    ins, acc = i.instrument, i.account
    if not ins.verified_on or not ins.course or ins.course != acc.course:
        out.append(Stop("SPEC_UNVERIFIED",
                        "取引単位・数量の条件が、使っている口座のコースで確かめられていません"))
    if now >= i.quote.valid_until:
        out.append(Stop("QUOTE_STALE",
                        "手で入れた bid・ask の有効期限が切れています。入れ直してください"))
    return out


# ------------------------------------------------------------ K 最終判定


def decide(i: Inputs, now: datetime) -> PlanDecision:
    """計算と独立 gate の両方を通ったときだけ ``PLAN_OK``。"""
    plan, stops = draft(i, now)
    for s in gate(i, now):
        if s not in stops:
            stops.append(s)
    disclaimers = [NO_EDGE_NOTE, ASSUMPTION_NOTE]
    if stops or plan is None:
        return PlanDecision("NO_TRADE", stops, None, disclaimers)
    return PlanDecision("PLAN_OK", [], plan, disclaimers)


def summary(d: PlanDecision) -> Dict:
    """一覧の1行ぶん。**詳細と同じ判定・同じ理由コードを返す。**"""
    return {"status": d.status, "codes": d.codes,
            "net_rr": str(d.plan.net_rr) if d.plan else None,
            "qty": d.plan.qty if d.plan else None}


def dec(x) -> Decimal:
    """境界（JSON・CSV の float）で Decimal に直す。非有限は NaN のまま渡し、検証で止める。"""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float) and not math.isfinite(x):
        return D("NaN")
    return D(str(x))
