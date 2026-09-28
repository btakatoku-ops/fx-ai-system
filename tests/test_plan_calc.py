# -*- coding: utf-8 -*-
"""Phase 2 の計算（P2-1）の受入試験。``docs/phase2-design.md`` §6 の T1〜T15。

期待値は**手で計算した値**を書いている（関数の出力を写していない）。
"""
from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app import plan_calc as P

UTC = timezone.utc
JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 29, 5, 0, tzinfo=UTC)          # 火曜 14:00 JST

ONE_AM = {"day_trade": {"enabled": True, "force_exit_jst": "01:00",
                        "force_exit_jst_dst": "01:00", "min_minutes_to_exit": 90}}


def quote(bid, ask, at=NOW, minutes=5):
    return P.Quote(bid=D(bid), ask=D(ask), observed_at=at,
                   valid_until=at + timedelta(minutes=minutes))


def cand(pair="USDJPY", direction=P.LONG, close="150.005", atr="0.100",
         swing=None, barrier=None, last_bar_at=None):
    return P.Candidate(pair=pair, direction=direction, decided_at=NOW,
                       valid_until=NOW + timedelta(minutes=10),
                       last_bar_at=last_bar_at or NOW - timedelta(minutes=15),
                       reference_close=D(close), atr=D(atr),
                       swing=D(swing) if swing else None,
                       barrier=D(barrier) if barrier else None)


def inst(symbol="USDJPY", base="USD", q="JPY", pip="0.01", tick="0.001", unit=10000):
    return P.Instrument(symbol=symbol, base=base, quote=q, pip=D(pip), price_tick=D(tick),
                        contract_unit=unit, max_qty=100, course="L25_10k",
                        verified_on="2026-09-14", source="取引要綱")


ACC = P.Account(balance=D(3_000_000), risk_pct=D("0.5"), max_risk_pct=D(2),
                max_margin_pct=D(20), margin_rate=D("0.04"), course="L25_10k")
ZERO = P.Costs(slippage_pips_per_side=D(0), conversion_stress=D(0), wide_spread=None)
DEFAULT = P.Costs(slippage_pips_per_side=D("0.2"), conversion_stress=D("0.01"),
                  wide_spread=D("0.05"))
OPEN = P.Board(excluded=False)


def rate(v):
    return P.Rate(value=D(v), observed_at=NOW, valid_until=NOW + timedelta(minutes=5))


def inputs(**over):
    base = dict(quote=quote("150.000", "150.010"), candidate=cand(), costs=ZERO,
                instrument=inst(), account=ACC, board=OPEN, rates={},
                day_trade_cfg=ONE_AM)
    base.update(over)
    return P.Inputs(**base)


def ok(i, now=NOW):
    d = P.decide(i, now)
    assert d.status == "PLAN_OK", d.codes
    return d.plan


def codes(i, now=NOW):
    return P.decide(i, now).codes


# ------------------------------------------------ T2 手計算との一致（1円単位）


def test_t2_usdjpy_long_by_hand():
    """ask 150.010、ATR 0.100 → 損切り 149.810（2ATR）。損失距離 0.200。
    数量1あたり 0.2×10,000 = 2,000円。許容 3,000,000×0.5% = 15,000円 → 数量7。
    損失 0.2×70,000 = 14,000円。TP1 = 150.010＋0.300 = 150.310、利益 21,000円。
    証拠金 70,000×150.010×4% = 420,028円（14.0%）。"""
    p = ok(inputs())
    assert (p.entry, p.stop, p.qty, p.units) == (D("150.010"), D("149.810"), 7, 70000)
    assert p.loss_jpy == 14000
    assert (p.targets[0].price, p.targets[0].profit_jpy) == (D("150.310"), 21000)
    assert p.targets[0].net_rr == D("1.50")
    assert p.margin_jpy == 420028


def test_t2_eurusd_short_needs_usdjpy_and_eurjpy():
    """bid 1.10000、ATR 0.00100 → 損切り 1.10200。距離 0.00200 USD。
    USDJPY 150 → 数量1あたり 0.002×10,000×150 = 3,000円 → 数量5、損失 15,000円。
    TP1 = 1.09700、利益 0.003×50,000×150 = 22,500円。
    証拠金 50,000×EURJPY 165×4% = 330,000円。"""
    i = inputs(quote=quote("1.10000", "1.10010"),
               candidate=cand("EURUSD", P.SHORT, close="1.10005", atr="0.00100"),
               instrument=inst("EURUSD", "EUR", "USD", pip="0.0001", tick="0.00001"),
               rates={"USDJPY": rate("150"), "EURJPY": rate("165")})
    p = ok(i)
    assert (p.entry, p.stop, p.qty) == (D("1.10000"), D("1.10200"), 5)
    assert p.loss_jpy == 15000
    assert (p.targets[0].price, p.targets[0].profit_jpy) == (D("1.09700"), 22500)
    assert p.margin_jpy == 330000


def test_t2_eurgbp_long_converts_through_gbpjpy():
    """ask 0.85010、ATR 0.00080 → 損切り 0.84850。距離 0.0016 GBP。
    GBPJPY 200 → 数量1あたり 3,200円 → 数量4（4.69 を切り捨て）。損失 12,800円。
    TP1 0.85250、利益 0.0024×40,000×200 = 19,200円。証拠金 40,000×165×4% = 264,000円。"""
    i = inputs(quote=quote("0.85000", "0.85010"),
               candidate=cand("EURGBP", close="0.85005", atr="0.00080"),
               instrument=inst("EURGBP", "EUR", "GBP", pip="0.0001", tick="0.00001"),
               rates={"GBPJPY": rate("200"), "EURJPY": rate("165")})
    p = ok(i)
    assert (p.stop, p.qty, p.loss_jpy) == (D("0.84850"), 4, 12800)
    assert (p.targets[0].price, p.targets[0].profit_jpy) == (D("0.85250"), 19200)
    assert p.margin_jpy == 264000


def test_t2_zarjpy_has_a_ten_times_larger_unit():
    """数量1＝10万通貨。ask 8.520、ATR 0.050 → 損切り 8.420。
    数量1あたり 0.1×100,000 = 10,000円 → 数量1、損失 10,000円。TP1 8.670、利益 15,000円。
    証拠金 100,000×8.520×4% = 34,080円。"""
    i = inputs(quote=quote("8.500", "8.520"),
               candidate=cand("ZARJPY", close="8.510", atr="0.050"),
               instrument=inst("ZARJPY", "ZAR", "JPY", unit=100000))
    p = ok(i)
    assert (p.stop, p.qty, p.units, p.loss_jpy) == (D("8.420"), 1, 100000, 10000)
    assert p.targets[0].profit_jpy == 15000
    assert p.margin_jpy == 34080


# ------------------------------------------------ T1 / T15 鏡像・向きは利用者の選択


def test_t1_buy_and_sell_are_mirror_images():
    """同じ距離・同じ費用なら、額・数量・netRR が一致し、価格は建値を挟んで対称。"""
    q = quote("149.995", "150.005")                # 仲値 150.000 を挟んで対称
    long = ok(inputs(quote=q, candidate=cand(close="150.000"), costs=DEFAULT))
    short = ok(inputs(quote=q, candidate=cand(direction=P.SHORT, close="150.000"),
                      costs=DEFAULT))
    assert (long.qty, long.loss_jpy, long.net_rr) == (short.qty, short.loss_jpy, short.net_rr)
    assert [t.profit_jpy for t in long.targets] == [t.profit_jpy for t in short.targets]
    assert long.entry - long.stop == short.stop - short.entry
    for a, b in zip(long.targets, short.targets):
        assert a.price - long.entry == short.entry - b.price


def test_t15_the_engine_signal_and_score_do_not_enter_the_plan():
    """計画の入力にエンジンの BUY/SELL や点数の欄が無い。向きだけを変えて2通り作れる。"""
    fields = set(P.Candidate.__dataclass_fields__)
    assert not fields & {"signal", "score", "quality"}
    assert ok(inputs(candidate=cand(direction=P.LONG))).direction == P.LONG
    assert ok(inputs(candidate=cand(direction=P.SHORT))).direction == P.SHORT


# ------------------------------------------------ T3 費用を増やして RR が良くならない


def test_t3_more_cost_never_improves_net_rr():
    rng = random.Random(20260928)
    for _ in range(300):
        spread = D(rng.randint(0, 30)) / 1000
        slip = D(rng.randint(0, 10)) / 10
        more_spread = spread + D(rng.randint(1, 20)) / 1000
        more_slip = slip + D(rng.randint(1, 5)) / 10
        direction = rng.choice([P.LONG, P.SHORT])
        barrier = rng.choice([None, "150.600", "149.400"])
        def rr(sp, sl):
            i = inputs(quote=quote("150.000", str(D("150.000") + sp)),
                       candidate=cand(direction=direction, close="150.000", barrier=barrier),
                       costs=P.Costs(slippage_pips_per_side=sl, conversion_stress=D(0),
                                     wide_spread=None))
            plan, stops = P.draft(i, NOW)
            if plan is None:
                return None
            gain = (plan.targets[0].price - plan.entry if direction == P.LONG
                    else plan.entry - plan.targets[0].price) - 2 * sl * D("0.01")
            return gain / plan.loss_distance
        a, b = rr(spread, slip), rr(more_spread, more_slip)
        if a is not None and b is not None:
            assert b <= a


# ------------------------------------------------ T4 / T5 数量


def test_t4_below_the_minimum_quantity_is_not_rounded_up():
    """許容 1,800円、数量1あたり 2,000円（0.9）→ 1 に切り上げない。"""
    acc = replace(ACC, balance=D(360_000))          # 0.5% = 1,800円
    assert "QTY_BELOW_MIN" in codes(inputs(account=acc))


@pytest.mark.parametrize("balance,qty", [
    (D(2_800_000), 7),        # 14,000円 ちょうど → 7
    (D(2_799_999), 6),        # 13,999.995円 → 6
    (D(2_800_001), 7),        # 14,000.005円 → 7
])
def test_t5_quantity_floors_exactly_at_the_step_boundary(balance, qty):
    """数量1あたり 2,000円。許容が 14,000円 ちょうど・その少し下・少し上。"""
    acc = replace(ACC, balance=balance)
    plan, _ = P.draft(inputs(account=acc), NOW)
    assert plan.qty == qty


def test_the_quantity_is_capped_at_the_published_maximum():
    acc = replace(ACC, balance=D(100_000_000), max_margin_pct=D(100))
    p = ok(inputs(account=acc))
    assert p.qty == 100 and any("上限" in n for n in p.notes)


# ------------------------------------------------ T6 換算の期限


def test_t6_conversion_rate_expiry():
    i = inputs(quote=quote("1.10000", "1.10010", minutes=30),   # 気配値は長めに
               candidate=cand("EURUSD", P.SHORT, close="1.10005", atr="0.00100"),
               instrument=inst("EURUSD", "EUR", "USD", pip="0.0001", tick="0.00001"),
               rates={"USDJPY": rate("150"), "EURJPY": rate("165")})
    assert P.decide(i, NOW + timedelta(minutes=4, seconds=59)).status == "PLAN_OK"
    assert "CONVERSION_STALE" in codes(i, NOW + timedelta(minutes=5))   # 期限ちょうど
    missing = replace(i, rates={"EURJPY": rate("165")})
    assert "CONVERSION_MISSING" in codes(missing)


def test_jpy_quoted_pairs_need_no_other_rate():
    """円が決済通貨の銘柄は、手入力の値だけで損益も証拠金も円で出せる。"""
    assert ok(inputs(rates={})).margin_jpy > 0


# ------------------------------------------------ T7 将来の足


def test_t7_a_future_bar_stops_the_plan_and_its_absence_changes_nothing():
    bad = inputs(candidate=cand(last_bar_at=NOW + timedelta(minutes=15)))
    assert "FUTURE_BAR_USED" in codes(bad)
    a = ok(inputs(candidate=cand(last_bar_at=NOW - timedelta(minutes=15))))
    b = ok(inputs(candidate=cand(last_bar_at=NOW - timedelta(hours=1))))
    assert (a.qty, a.stop, a.targets[0].price) == (b.qty, b.stop, b.targets[0].price)


# ------------------------------------------------ T8 除外の日


def test_t8_an_excluded_board_blocks_even_a_chosen_direction():
    """点数がいくつでも、向きを選んでも、除外の日は計画を出さない。"""
    i = inputs(board=P.Board(excluded=True, reasons=("テクニカルとファンダが逆（対立）",)))
    d = P.decide(i, NOW)
    assert d.status == "NO_TRADE" and "BOARD_EXCLUDED" in d.codes and d.plan is None


# ------------------------------------------------ T9 一覧と詳細の整合


def test_t9_the_list_row_matches_the_detail():
    for i in (inputs(), inputs(board=P.Board(excluded=True))):
        d = P.decide(i, NOW)
        s = P.summary(d)
        assert s["status"] == d.status and s["codes"] == d.codes
        assert s["qty"] == (d.plan.qty if d.plan else None)


# ------------------------------------------------ T10 / T14 手入力


def test_t10_no_manual_quote_means_no_plan():
    q = replace(quote("150.000", "150.010"), price_basis="unknown")
    assert "QUOTE_MISSING" in codes(inputs(quote=q))


def test_t14_manual_quote_checks():
    assert "QUOTE_INVALID" in codes(inputs(quote=quote("150.010", "150.000")))   # bid > ask
    wide = inputs(quote=quote("150.000", "150.200"), costs=DEFAULT)                # 0.2 > 0.05×3
    assert "QUOTE_INVALID" in codes(wide)
    far = inputs(quote=quote("160.000", "160.010"))                                # 終値から10円
    assert "QUOTE_MISMATCH" in codes(far)
    assert "QUOTE_STALE" in codes(inputs(), NOW + timedelta(minutes=5))


# ------------------------------------------------ T11 刻限


@pytest.mark.parametrize("jst,code", [
    ((9, 29, 23, 31), "DEADLINE"),        # 残り89分
    ((9, 30, 1, 30), "DEADLINE"),         # 01:00〜日替わり
    ((9, 30, 6, 30), None),               # 夏時間の日替わり後
])
def test_t11_deadline(jst, code):
    at = datetime(2026, *jst, tzinfo=JST).astimezone(UTC)
    i = inputs(quote=quote("150.000", "150.010", at=at),
               candidate=replace(cand(), decided_at=at, valid_until=at + timedelta(minutes=10),
                                 last_bar_at=at - timedelta(minutes=15)))
    got = codes(i, at)
    assert (code in got) if code else ("DEADLINE" not in got), got
    assert "ROLLOVER_CROSS" not in got


def test_a_deadline_after_the_rollover_is_refused(monkeypatch):
    """刻限が日替わりを跨いだら、計画の側でも止める。

    ``day_trade.exit_deadline`` は作りとして跨がない（test_day_trade で固定）。
    ここでは、それが崩れたときに計画の側の見張りが働くことを確かめる。
    """
    from app import day_trade

    monkeypatch.setattr(day_trade, "exit_deadline",
                        lambda cfg, now: day_trade.rollover_start(now) + timedelta(days=1, hours=1))
    assert "ROLLOVER_CROSS" in codes(inputs())


# ------------------------------------------------ T12 丸めの向き


def test_t12_rounding_is_always_against_you():
    i = inputs(quote=quote("150.0004", "150.0106"), candidate=cand(atr="0.1003"),
               costs=DEFAULT)
    p = ok(i)
    assert p.entry == D("150.011")                  # ask を切り上げ
    assert p.stop <= p.entry - 2 * D("0.1003")      # 損切りは遠い側
    gain = p.targets[0].price - p.entry - 2 * D("0.2") * D("0.01")
    assert gain / p.loss_distance >= D("1.5")       # 丸めで 1.5 倍を割らない
    assert p.targets[0].net_rr == D("1.50")
    assert p.loss_jpy <= p.risk_amount_jpy          # 丸めても許容を超えない
    assert p.loss_jpy >= p.loss_distance * p.units  # 損失は切り上げ（ストレス込みなので以上）


def test_loss_never_exceeds_the_allowed_amount_with_awkward_numbers():
    rng = random.Random(7)
    for _ in range(200):
        bal = D(rng.randint(500_000, 5_000_000)) + D(rng.randint(0, 99)) / 100
        atr = D(rng.randint(40, 300)) / 1000
        i = inputs(account=replace(ACC, balance=bal), candidate=cand(atr=atr),
                   costs=DEFAULT)
        plan, _ = P.draft(i, NOW)
        if plan:
            assert plan.loss_jpy <= plan.risk_amount_jpy


# ------------------------------------------------ T13 いまの計算との一致


def test_t13_matches_the_current_rules_with_zero_costs():
    """費用ゼロ・滑りゼロ・除外なしなら、いまの build_plan と損切り・TP1・数量が一致する。"""
    from app.config import get_trading_config
    from app.risk_engine import size_position
    from app.trade_plan import _pick_stop

    cfg = get_trading_config()
    spec = cfg.pair("USDJPY")
    close, atr, swing = 150.010, 0.100, 149.700
    analysis = SimpleNamespace(
        indicators={"M15": SimpleNamespace(close=close)},
        market_structure=SimpleNamespace(last_swing_low=swing, last_swing_high=None))
    old_stop, old_basis = _pick_stop(analysis, True, atr, cfg.account["stop"])
    sizing = size_position(spec, 3_000_000, 0.5, close, old_stop,
                           {"USDJPY": close}, cfg.account)

    p = ok(inputs(quote=quote("150.000", "150.010"),         # 建値＝ask＝終値
                  candidate=cand(close="150.010", swing=str(swing))))
    assert p.stop == D(str(round(old_stop, 3))) and p.stop_basis == old_basis
    assert p.qty == sizing.qty
    # TP1 は1刻みまで違いうる。**違う理由**：いまの計算は 150.5125 を四捨五入して
    # 150.512（1.5R をわずかに割る）。新しい計算は外側へ丸めて 150.513（1.5R を割らない）。
    old_tp1 = D(str(close)) + D("1.5") * (D(str(close)) - p.stop)
    assert p.targets[0].price >= old_tp1
    assert p.targets[0].price - old_tp1 < D("0.001")


# ------------------------------------------------ 停止表の残りの行


def test_invalid_numbers_stop_everything():
    assert codes(inputs(candidate=cand(atr="NaN"))) [0] == "PLAN_INVALID_NUMBER"
    assert "PLAN_INVALID_NUMBER" in codes(inputs(account=replace(ACC, balance=D(0))))


def test_unset_direction_and_expired_candidate():
    assert "DIRECTION_UNSET" in codes(inputs(candidate=cand(direction="NEUTRAL")))
    assert "PLAN_EXPIRED" in codes(inputs(), NOW + timedelta(minutes=10))


def test_risk_margin_and_target_caps():
    assert "RISK_ABOVE_CAP" in codes(inputs(account=replace(ACC, risk_pct=D("2.5"))))
    assert "MARGIN_ABOVE_CAP" in codes(inputs(account=replace(ACC, max_margin_pct=D(5))))
    assert "TARGET_NO_ROOM" in codes(inputs(candidate=cand(barrier="150.015")))


def test_net_rr_below_min_uses_costs():
    """抵抗帯で TP1 を手前に寄せた結果、費用込みの比が 1.5 を割るなら止める。

    建値 150.010・損切り 149.810（損失 0.200）。抵抗帯 150.300 の手前 150.290 に
    寄せると、利益 0.280 で 1.40。滑りを入れるとさらに下がる。
    """
    assert "NET_RR_BELOW_MIN" in codes(inputs(candidate=cand(barrier="150.300")))
    # 帯が無ければ、滑りがあっても TP1 を費用込み 1.5 倍の位置に置くので通る
    assert P.decide(inputs(costs=DEFAULT), NOW).status == "PLAN_OK"


def test_an_unverified_spec_is_refused():
    assert "SPEC_UNVERIFIED" in codes(inputs(instrument=replace(inst(), verified_on=None)))
    assert "SPEC_UNVERIFIED" in codes(inputs(instrument=replace(inst(), course="L25_1k")))


def test_a_plan_ok_always_carries_the_no_edge_note():
    d = P.decide(inputs(), NOW)
    assert d.status == "PLAN_OK" and P.NO_EDGE_NOTE in d.disclaimers


def test_only_the_new_endpoint_uses_the_new_calculation():
    """P2-4 でつないだのは ``/api/plan-calc`` だけ。記録・朝の確認・公開ページ・
    いまの ``/api/plan`` は、いままでどおり（新しい計算を使わない）。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    allowed = {"plan_calc.py", "plan_inputs.py", "main.py"}
    for f in list((root / "backend" / "app").glob("*.py")) + list((root / "scripts").glob("*.py")):
        if f.name in allowed:
            continue
        assert "plan_calc" not in f.read_text(encoding="utf-8"), f.name
