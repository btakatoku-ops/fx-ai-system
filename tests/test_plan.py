# -*- coding: utf-8 -*-
"""Phase 2（Entry / SL / TP / Lot / 証拠金）の試験。

**ここを間違えると、決めたリスクそのものが狂う。** 方向が当たっていても
Lot が2倍なら損失も2倍。だから数値は手計算した既知の値と突き合わせる。

自分の実装の出力を自分で正解にしない。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.analysis import analyze_pair
from app.entry_engine import entry_zone, order_hint, required_symbols
from app.market_data import MockMarketDataProvider
from app.models import Direction, Signal
from app.risk_engine import (
    RiskError,
    conversion_symbol,
    loss_at_stop,
    pip_value_per_lot,
    profit_at,
    size_position,
    to_account_currency,
)
from app.trade_plan import build_plan

def _fresh_as_of() -> str:
    """きょうの日付。

    **試験を、置いてあるデータの古さに依存させない。** 日付を決め打ちすると、
    その日を過ぎた瞬間に「古い表」になって、スワップの配線とは関係のない
    ところで落ちる。実際 2026-09-20 に落ちた。古い表の扱いを確かめる試験は
    別にあり（下）、そちらは古い日付を明示して渡している。
    """
    from datetime import date

    return date.today().isoformat()


NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)

# 円換算のレート。手計算に使うので固定値。
RATES = {
    "USDJPY": 154.15, "EURJPY": 178.90, "GBPJPY": 208.40, "AUDJPY": 110.60,
    "NZDJPY": 89.90, "CADJPY": 111.40, "CHFJPY": 189.50,
}


# ====================================================== pip の価値（手計算）


def test_pip_value_for_a_jpy_quote_pair(cfg):
    """USDJPY: 0.01 × 10,000 = 100円。換算は不要。"""
    pv = pip_value_per_lot(cfg.pair("USDJPY"), 10000, RATES, "JPY")
    assert pv.amount == pytest.approx(100.0)
    assert pv.currency == "JPY"


def test_pip_value_for_a_usd_quote_pair(cfg):
    """EURUSD: 0.0001 × 10,000 = 1 USD → ×154.15 = 154.15円。"""
    pv = pip_value_per_lot(cfg.pair("EURUSD"), 10000, RATES, "JPY")
    assert pv.amount == pytest.approx(154.15)


def test_pip_value_for_a_cross_pair(cfg):
    """EURGBP: 0.0001 × 10,000 = 1 GBP → ×208.40 = 208.40円。

    **ここが1.0倍で済むと思うと、損失が1/200に見える。**
    """
    pv = pip_value_per_lot(cfg.pair("EURGBP"), 10000, RATES, "JPY")
    assert pv.amount == pytest.approx(208.40)


def test_pip_value_for_gbpnzd(cfg):
    """GBPNZD: 決済通貨は NZD。1 NZD → ×89.90 = 89.90円。"""
    pv = pip_value_per_lot(cfg.pair("GBPNZD"), 10000, RATES, "JPY")
    assert pv.amount == pytest.approx(89.90)


def test_missing_conversion_rate_raises_instead_of_guessing(cfg):
    """**レートが無ければ計算しない。1.0 で代用しない。**"""
    with pytest.raises(RiskError):
        pip_value_per_lot(cfg.pair("EURGBP"), 10000, {}, "JPY")
    with pytest.raises(RiskError):
        to_account_currency(100.0, "GBP", {"GBPJPY": 0.0}, "JPY")


def test_conversion_symbol_is_none_for_the_account_currency():
    assert conversion_symbol("JPY", "JPY") is None
    assert conversion_symbol("GBP", "JPY") == "GBPJPY"


def test_required_symbols_covers_both_profit_and_margin(cfg):
    """損益は決済通貨、証拠金は基軸通貨。**両方要る。**"""
    assert set(required_symbols(cfg.pair("EURGBP"))) == {"GBPJPY", "EURJPY"}
    assert required_symbols(cfg.pair("USDJPY")) == ["USDJPY"]


# ============================================================ Lot（手計算）


def test_quantity_is_derived_from_the_allowed_loss(cfg):
    """USDJPY・資金100万・1%・損切30pips。

    **発注は「数量」（整数）。** 0.1Lot のような刻みは1万通貨コースに無い。
    許容損失 10,000円 ÷ (30pips × 100円/数量) = 3.33 → 切り捨てて数量3。
    """
    r = size_position(cfg.pair("USDJPY"), 1_000_000, 1.0,
                      entry=154.15, stop=153.85, rates=RATES,
                      account={**cfg.account,
                               "defaults": {**cfg.account["defaults"],
                                            "max_margin_use_pct": 100.0}})
    assert r.ok is True
    assert r.stop_distance_pips == pytest.approx(30.0)
    assert r.qty == 3
    assert r.contract_unit == 10000
    assert r.units == 30000
    # 実際の損失は許容内
    assert loss_at_stop(r).amount == pytest.approx(30 * 100 * 3)
    assert loss_at_stop(r).amount <= 10_000


def test_quantity_is_floored_not_rounded(cfg):
    """**切り上げると決めたリスクを超える。** 必ず切り捨て。"""
    acct = {**cfg.account,
            "defaults": {**cfg.account["defaults"], "max_margin_use_pct": 100.0}}
    r = size_position(cfg.pair("USDJPY"), 1_000_000, 1.0,
                      entry=154.15, stop=153.86, rates=RATES, account=acct)
    # 29pips → 10000/2900 = 3.448 → 数量3（4 にはしない）
    assert r.qty == 3
    assert loss_at_stop(r).amount <= 10_000


def test_margin_uses_the_base_currency_notional(cfg):
    """証拠金は基軸通貨の量 × 円レート × 4%（決済通貨ではない）。"""
    r = size_position(cfg.pair("EURUSD"), 1_000_000, 1.0,
                      entry=1.1612, stop=1.1582, rates=RATES,
                      account=cfg.account)
    assert r.ok is True
    assert r.required_margin.amount == pytest.approx(
        r.units * 178.90 * 0.04, rel=1e-6)


def test_margin_cap_blocks_an_oversized_position(cfg):
    """証拠金が資金の上限を超えたら建てない。"""
    # 損切りを狭くすると数量が増え、証拠金が上限を超える
    r = size_position(cfg.pair("USDJPY"), 1_000_000, 2.0,
                      entry=154.15, stop=154.10, rates=RATES,
                      account=cfg.account)
    assert r.ok is False
    assert any("証拠金" in x for x in r.reasons)


def test_risk_above_the_cap_is_refused(cfg):
    r = size_position(cfg.pair("USDJPY"), 1_000_000, 5.0,
                      entry=154.15, stop=153.85, rates=RATES,
                      account=cfg.account)
    assert r.ok is False
    assert any("上限" in x for x in r.reasons)


def test_a_stop_too_wide_for_the_balance_is_refused(cfg):
    """許容損失で最小Lotも建てられないなら、建てない。"""
    r = size_position(cfg.pair("USDJPY"), 10_000, 1.0,
                      entry=154.15, stop=150.00, rates=RATES,
                      account=cfg.account)
    assert r.ok is False
    assert any("最小" in x or "広すぎ" in x for x in r.reasons)


def test_zero_stop_distance_is_refused(cfg):
    r = size_position(cfg.pair("USDJPY"), 1_000_000, 1.0,
                      entry=154.15, stop=154.15, rates=RATES,
                      account=cfg.account)
    assert r.ok is False


def test_non_finite_inputs_are_refused(cfg):
    for bad in (float("inf"), float("nan"), -1.0, 0.0):
        r = size_position(cfg.pair("USDJPY"), 1_000_000, 1.0,
                          entry=bad, stop=153.0, rates=RATES,
                          account=cfg.account)
        assert r.ok is False


def test_profit_is_not_multiplied_by_any_probability(cfg):
    """**「当たったらいくら」であって期待値ではない。**

    30pips 取れたときの額は、確率を掛けずに 30 × pip価値 × Lot。
    """
    acct = {**cfg.account,
            "defaults": {**cfg.account["defaults"], "max_margin_use_pct": 100.0}}
    r = size_position(cfg.pair("USDJPY"), 1_000_000, 1.0,
                      entry=154.15, stop=153.85, rates=RATES, account=acct)
    p = profit_at(154.45, 154.15, True, r, cfg.pair("USDJPY"))
    assert p.amount == pytest.approx(30 * 100 * r.lot)


# ================================================================ 建値の帯


def test_entry_zone_is_a_band_not_a_single_price():
    low, high = entry_zone(154.15, atr=0.15, width_atr=0.15)
    assert low < 154.15 < high
    assert high - low == pytest.approx(2 * 0.15 * 0.15)


def test_order_hint_describes_but_never_orders():
    low, high = 154.10, 154.20
    assert "帯の中" in order_hint(True, 154.15, low, high, 154.15)
    assert "外側" in order_hint(True, 154.15, low, high, 154.30)
    assert "手前" in order_hint(True, 154.15, low, high, 154.00)


# ================================================================ 計画全体


def _forced(cfg, symbol: str, signal: Signal, direction: Direction):
    p = MockMarketDataProvider(scenario="trend_pullback")
    a = analyze_pair(symbol, p, cfg)
    return p, a.model_copy(update={"signal": signal, "direction": direction,
                                   "hard_blocked": False})


def test_no_plan_is_built_for_wait_or_no_trade(cfg):
    """**見送りの場面に「もし買うなら」の数字を添えない。**

    添えると見送りの意味が薄れる。
    """
    p = MockMarketDataProvider()
    for sym in [s.symbol for s in cfg.enabled_pairs()][:6]:
        a = analyze_pair(sym, p, cfg)
        if a.signal in (Signal.BUY, Signal.SELL):
            continue
        plan = build_plan(a, cfg.pair(sym), cfg, RATES)
        assert plan.ok is False
        assert plan.blocked_reasons
        assert plan.targets == []
        assert plan.sizing is None or plan.sizing.lot == 0.0


def test_a_plan_is_buildable_for_a_valid_signal(cfg):
    """BUY/SELL なら計画が組めること。**組めないままだと機能が死ぬ。**"""
    built = 0
    for sym in [s.symbol for s in cfg.enabled_pairs()]:
        for sig, d in ((Signal.BUY, Direction.LONG),
                       (Signal.SELL, Direction.SHORT)):
            p, a = _forced(cfg, sym, sig, d)
            plan = build_plan(a, cfg.pair(sym), cfg, RATES,
                              balance=1_000_000, risk_pct=1.0)
            if plan.ok:
                built += 1
                assert plan.stop is not None and plan.stop_basis
                assert plan.entry_low < plan.entry_high
                assert plan.targets
                assert plan.risk_reward >= cfg.account["targets"]["min_rr"]
                assert plan.loss_if_stopped.amount <= 10_000 * 1.001
    assert built > 0, "どの銘柄でも計画が組めていません"


def test_stop_is_on_the_correct_side(cfg):
    for sym in ("USDJPY", "GBPJPY", "EURUSD"):
        for sig, d in ((Signal.BUY, Direction.LONG),
                       (Signal.SELL, Direction.SHORT)):
            p, a = _forced(cfg, sym, sig, d)
            plan = build_plan(a, cfg.pair(sym), cfg, RATES)
            if not plan.ok:
                continue
            if d is Direction.LONG:
                assert plan.stop < plan.reference_price
                assert all(t.price > plan.reference_price for t in plan.targets)
            else:
                assert plan.stop > plan.reference_price
                assert all(t.price < plan.reference_price for t in plan.targets)


def test_first_target_respects_min_rr(cfg):
    """TP1 が下限を割る計画は出さない。

    設定の r_multiples の先頭が min_rr を下回っていると、TP1 が構造的に
    条件を満たせず、計画が永久に作れなくなる。実際その状態を作った。
    """
    assert cfg.account["targets"]["r_multiples"][0] >= \
        cfg.account["targets"]["min_rr"]


def test_targets_are_not_duplicated_at_the_same_price(cfg):
    """同じ帯で止まった段は畳む。3段に見せて実質1段にしない。"""
    for sym in [s.symbol for s in cfg.enabled_pairs()]:
        p, a = _forced(cfg, sym, Signal.BUY, Direction.LONG)
        plan = build_plan(a, cfg.pair(sym), cfg, RATES)
        if not plan.ok:
            continue
        prices = [t.price for t in plan.targets]
        assert len(prices) == len(set(prices))
        assert [t.label for t in plan.targets] == \
            [f"TP{i}" for i in range(1, len(prices) + 1)]


def test_swap_is_never_silently_zero(cfg):
    """**スワップを0円と書けば「費用ゼロ」に見える。** 不明は不明と出す。

    表を取り込んでいれば実額、取り込んでいなければ UNKNOWN。
    どちらの場合も「0円」で済ませない。
    """
    empty = cfg.model_copy(update={"swap": {"available": False, "table": {}}})
    for sym in [s.symbol for s in cfg.enabled_pairs()]:
        p, a = _forced(empty, sym, Signal.BUY, Direction.LONG)
        plan = build_plan(a, empty.pair(sym), empty, RATES)
        if not plan.ok:
            continue
        assert plan.swap_state == "UNKNOWN"
        assert plan.swap_per_day is None
        assert any("スワップ" in d for d in plan.disclaimers)
        return
    pytest.fail("計画が1件も組めず、確認できませんでした")


def test_spread_cost_is_charged_and_negative(cfg):
    for sym in [s.symbol for s in cfg.enabled_pairs()]:
        p, a = _forced(cfg, sym, Signal.BUY, Direction.LONG)
        plan = build_plan(a, cfg.pair(sym), cfg, RATES)
        if not plan.ok:
            continue
        assert plan.spread_cost is not None
        assert plan.spread_cost.amount < 0


def test_plan_carries_the_no_edge_warning(cfg):
    """**優位性が測れていないことを、計画から外して読めないようにする。**"""
    for sym in [s.symbol for s in cfg.enabled_pairs()]:
        p, a = _forced(cfg, sym, Signal.BUY, Direction.LONG)
        plan = build_plan(a, cfg.pair(sym), cfg, RATES)
        if not plan.ok:
            continue
        joined = " ".join(plan.disclaimers)
        assert "コイン投げ" in joined
        assert "証拠金は推定" in joined
        return
    pytest.fail("計画が1件も組めず、確認できませんでした")


def test_expired_analysis_yields_no_plan(cfg):
    p, a = _forced(cfg, "USDJPY", Signal.BUY, Direction.LONG)
    stale = a.model_copy(update={"valid_until": NOW - timedelta(seconds=1)})
    plan = build_plan(stale, cfg.pair("USDJPY"), cfg, RATES, now=NOW)
    assert plan.ok is False
    assert any("有効期限" in r for r in plan.blocked_reasons)


def test_missing_rates_yield_no_plan(cfg):
    """換算レートが無いのに計画を出すと、損失額が桁違いに見える。"""
    p, a = _forced(cfg, "EURGBP", Signal.BUY, Direction.LONG)
    plan = build_plan(a, cfg.pair("EURGBP"), cfg, rates={})
    assert plan.ok is False


# ============================================================== API


def test_plan_endpoint_returns_a_plan_or_a_reason():
    from fastapi.testclient import TestClient

    import app.main as main

    with TestClient(main.app) as client:
        r = client.get("/api/plan/USDJPY?balance=1000000&risk_pct=1")
        assert r.status_code == 200
        body = r.json()
        assert "plan" in body and "analysis" in body and "account" in body
        # 出所の未確認を隠さない
        assert body["account"]["unverified"]
        plan = body["plan"]
        assert plan["ok"] is True or plan["blocked_reasons"]
        assert plan["swap_state"] in ("UNKNOWN", "KNOWN")

        assert client.get("/api/plan/XXXYYY").status_code == 404
        assert client.get("/api/plan/USDJPY?risk_pct=99").status_code == 422


def test_plan_endpoint_never_places_an_order():
    """**発注の口が無いこと。** Phase 2 でも注文はしない。"""
    import app.main as main

    paths = {r.path for r in main.app.routes if hasattr(r, "path")}
    for p in paths:
        assert "order" not in p.lower()
        assert "execute" not in p.lower()
        assert "trade/submit" not in p.lower()


# ============================================================== スワップ

# **日付を固定しておく。** これを使う試験は「古い表をどう扱うか」を
# 確かめるもので、基準日（today=...）を明示して渡している。ここを
# きょうの日付にすると、その基準日との差が毎日変わって落ちる。
_SWAP_CFG = {
    "available": True,
    "source": "検証用",
    "as_of": "2026-09-12",
    "unit": "JPY_PER_LOT_PER_DAY",
    "stale_after_days": 7,
    "table": {
        "USDJPY": {"buy": 251.0, "sell": -311.0},
        "EURJPY": {"buy": -95.0, "sell": 55.0},
    },
}
_TODAY = date(2026, 9, 12)


def test_swap_is_unknown_when_no_table_is_loaded(cfg):
    from app.risk_engine import swap_estimate

    info = swap_estimate(cfg.pair("USDJPY"), True, {"available": False})
    assert info.state == "UNKNOWN"
    assert info.per_lot_per_day is None
    assert any("0円ではありません" in n for n in info.notes)


def test_swap_differs_by_direction(cfg):
    """**買い持ちと売り持ちで額も符号も違う。** 取り違えたら逆になる。"""
    from app.risk_engine import swap_estimate

    spec = cfg.pair("USDJPY")
    long_ = swap_estimate(spec, True, _SWAP_CFG, lot=1.0, today=_TODAY)
    short = swap_estimate(spec, False, _SWAP_CFG, lot=1.0, today=_TODAY)
    assert long_.state == "KNOWN" and short.state == "KNOWN"
    assert long_.per_lot_per_day.amount == pytest.approx(251.0)
    assert short.per_lot_per_day.amount == pytest.approx(-311.0)
    # 受取と支払いが逆転している
    assert long_.per_lot_per_day.amount > 0 > short.per_lot_per_day.amount


def test_swap_scales_with_lot(cfg):
    from app.risk_engine import swap_estimate

    info = swap_estimate(cfg.pair("USDJPY"), True, _SWAP_CFG, lot=2.5,
                         today=_TODAY)
    assert info.per_lot_per_day.amount == pytest.approx(251.0)
    assert info.per_position_per_day.amount == pytest.approx(251.0 * 2.5)


def test_swap_for_a_pair_not_in_the_table_is_unknown(cfg):
    from app.risk_engine import swap_estimate

    info = swap_estimate(cfg.pair("GBPNZD"), True, _SWAP_CFG, today=_TODAY)
    assert info.state == "UNKNOWN"
    assert info.per_lot_per_day is None


def test_an_old_swap_table_is_marked_stale(cfg):
    """スワップは毎日変わる。古い表を現在の値として出さない。"""
    from app.risk_engine import swap_estimate

    info = swap_estimate(cfg.pair("USDJPY"), True, _SWAP_CFG, lot=1.0,
                         today=date(2026, 10, 1))
    assert info.state == "STALE"
    assert info.age_days == 19
    assert any("取り直して" in n for n in info.notes)


def test_a_table_without_a_date_is_stale(cfg):
    from app.risk_engine import swap_estimate

    cfg_no_date = {**_SWAP_CFG, "as_of": ""}
    info = swap_estimate(cfg.pair("USDJPY"), True, cfg_no_date, today=_TODAY)
    assert info.state == "STALE"
    assert any("日付がありません" in n for n in info.notes)


def test_swap_always_warns_about_the_three_day_rollover(cfg):
    from app.risk_engine import swap_estimate

    info = swap_estimate(cfg.pair("USDJPY"), True, _SWAP_CFG, today=_TODAY)
    assert any("3日分" in n for n in info.notes)


def test_swap_numbers_never_exist_without_provenance(cfg):
    """**数字があるなら、出所と日付が必ずある。**

    推測値を入れないための歯止め。表が空なら空でよいが、値が入っている
    のに出所や日付が無い状態は許さない（後から確かめられなくなる）。
    """
    swap = cfg.swap
    if swap["table"]:
        assert swap["available"] is True
        assert swap["source"], "スワップ値があるのに出所がありません"
        assert swap["as_of"], "スワップ値があるのに日付がありません"
        datetime.strptime(swap["as_of"], "%Y-%m-%d")
    else:
        assert swap["available"] is False


def test_swap_config_is_the_single_source_of_truth(cfg):
    """スワップの在り処は config/swap.json だけ。

    account.json にも swap 欄を持たせていた時期があり、真実の在り処が
    2か所になっていた。片方だけ更新すれば静かに食い違う。
    """
    assert "swap" not in cfg.account


# ------------------------------------------------ 取り込みスクリプト


def _import_swap_module():
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "import_swap", root / "scripts" / "import_swap.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_importer_rejects_bad_rows_without_fixing_them(tmp_path, cfg):
    """**壊れた行を黙って直さない。** 飛ばして理由を返す。"""
    mod = _import_swap_module()
    csv_path = tmp_path / "swap.csv"
    csv_path.write_text(
        "symbol,buy,sell\n"
        "USDJPY,251,-311\n"
        "NOTAPAIR,10,-10\n"        # 設定に無い
        "AUDJPY,abc,-50\n"         # 数値でない
        "CHFJPY,-120,\n"           # 片側だけ
        "NZDJPY,999999,-10\n"      # 桁がおかしい
        "EURJPY,-95,55\n"
        "USDJPY,1,-1\n",           # 重複
        encoding="utf-8")
    known = {p.symbol for p in cfg.pairs}
    table, problems = mod.parse_rows(csv_path, known)

    assert set(table) == {"USDJPY", "EURJPY"}
    assert table["USDJPY"] == {"buy": 251.0, "sell": -311.0}
    assert len(problems) == 5
    joined = " ".join(problems)
    for fragment in ("設定にない", "数値では", "空です", "大きすぎ", "重複"):
        assert fragment in joined


def test_importer_skips_blank_rows(tmp_path, cfg):
    """雛形の未入力行は飛ばす。分かる銘柄だけ入れればよい。"""
    mod = _import_swap_module()
    csv_path = tmp_path / "swap.csv"
    csv_path.write_text("symbol,buy,sell\nUSDJPY,251,-311\nEURJPY,,\n",
                        encoding="utf-8")
    table, problems = mod.parse_rows(csv_path, {p.symbol for p in cfg.pairs})
    assert set(table) == {"USDJPY"}
    assert problems == []


def test_importer_flags_suspicious_signs():
    """買いも売りも受取なら、写し間違いの疑い。**直さず気づかせる。**"""
    mod = _import_swap_module()
    notes = mod.check_signs({"USDJPY": {"buy": 10.0, "sell": 5.0},
                             "EURJPY": {"buy": 0.0, "sell": 0.0}})
    assert any("写し間違い" in n for n in notes)
    assert any("両方0" in n for n in notes)


def test_importer_accepts_comma_and_yen_formatting(tmp_path, cfg):
    mod = _import_swap_module()
    csv_path = tmp_path / "swap.csv"
    csv_path.write_text("symbol,buy,sell\nUSDJPY,\"1,251円\",-311\n",
                        encoding="utf-8")
    table, problems = mod.parse_rows(csv_path, {p.symbol for p in cfg.pairs})
    assert table["USDJPY"]["buy"] == pytest.approx(1251.0)
    assert problems == []


def test_importer_requires_the_expected_header(tmp_path, cfg):
    mod = _import_swap_module()
    csv_path = tmp_path / "swap.csv"
    csv_path.write_text("pair,long,short\nUSDJPY,1,-1\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        mod.parse_rows(csv_path, {p.symbol for p in cfg.pairs})


def _first_buildable(cfg, signal, direction):
    """計画が実際に組める銘柄を1つ探す。**skip で済ませない。**"""
    for spec in cfg.enabled_pairs():
        p, a = _forced(cfg, spec.symbol, signal, direction)
        plan = build_plan(a, spec, cfg, RATES, balance=1_000_000, risk_pct=1.0)
        if plan.ok:
            return spec, a
    return None, None


def test_plan_shows_swap_once_a_table_is_loaded(cfg):
    """表を入れたら、計画のスワップ欄が実額になること。

    計画が組める銘柄を探してから、その銘柄ぶんの表を与えて確かめる。
    組めない銘柄で skip すると、配線が壊れても気づけない。
    """
    spec, a = _first_buildable(cfg, Signal.BUY, Direction.LONG)
    assert spec is not None, "計画が組める銘柄が1つもありません"

    table = {"available": True, "source": "検証用", "as_of": _fresh_as_of(),
             "stale_after_days": 7,
             "table": {spec.symbol: {"buy": 251.0, "sell": -311.0}}}
    loaded = cfg.model_copy(update={"swap": table})
    plan = build_plan(a, spec, loaded, RATES, balance=1_000_000, risk_pct=1.0)

    assert plan.ok is True
    assert plan.swap_state == "KNOWN"
    assert plan.swap.per_lot_per_day.amount == pytest.approx(251.0)
    assert plan.swap_per_day.amount == pytest.approx(251.0 * plan.sizing.lot)


def test_plan_swap_flips_with_direction(cfg):
    """売り持ちなら符号が変わること。**配線の取り違えを捕まえる。**"""
    spec, a = _first_buildable(cfg, Signal.SELL, Direction.SHORT)
    assert spec is not None
    table = {"available": True, "source": "検証用", "as_of": _fresh_as_of(),
             "stale_after_days": 7,
             "table": {spec.symbol: {"buy": 251.0, "sell": -311.0}}}
    loaded = cfg.model_copy(update={"swap": table})
    plan = build_plan(a, spec, loaded, RATES, balance=1_000_000, risk_pct=1.0)
    assert plan.ok is True
    assert plan.swap.per_lot_per_day.amount == pytest.approx(-311.0)


def test_plan_keeps_swap_unknown_when_the_pair_is_absent(cfg):
    """表にその銘柄が無ければ、計画でも不明のまま。0円にしない。"""
    spec, a = _first_buildable(cfg, Signal.BUY, Direction.LONG)
    assert spec is not None
    other = next(s.symbol for s in cfg.enabled_pairs() if s.symbol != spec.symbol)
    table = {"available": True, "source": "検証用", "as_of": _fresh_as_of(),
             "stale_after_days": 7,
             "table": {other: {"buy": 1.0, "sell": -1.0}}}
    loaded = cfg.model_copy(update={"swap": table})
    plan = build_plan(a, spec, loaded, RATES, balance=1_000_000, risk_pct=1.0)
    assert plan.swap_state == "UNKNOWN"
    assert plan.swap_per_day is None


# ------------------------------------------ 業者PDFからの取り込み


def _gaitame_module():
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "fetch_swap_gaitame", root / "scripts" / "fetch_swap_gaitame.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_PDF_PAGE = """スワップポイント一覧表2026年9月
通貨ペア USD/CAD USD/CHF ZAR/JPY TRY/JPY MXN/JPY
米ドル/カナダドル 米ドル/スイスフラン 南アフリカランド/円 トルコリラ/円 メキシコペソ/円
日付 付与日数 売 買 付与日数 売 買 付与日数 売 買 付与日数 売 買 付与日数 売 買
2026 / 09 / 10 木 3 -210 75 1 -190 95 1 -10 5 1 -25 25 1 -15 5
2026 / 09 / 11 金 1 -70 25 1 -190 95 1 -10 5 1 -25 25 2 -30 10
2026 / 09 / 12 土 - - - - -
2026 / 09 / 14 月 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
【ご注意】
スワップポイントは1万通貨あたりの値です。
"""


def test_pdf_values_are_divided_by_granted_days():
    """**表の値は「付与日数ぶんの合計」。** 割らずに読むと3倍になる。

    USD/CAD の 09/10 は3日分で −210/75。1日あたりは −70/25 で、
    翌日の1日分の値（−70/25）と一致する。
    """
    mod = _gaitame_module()
    pairs, rows = mod.parse_page(_PDF_PAGE)
    assert pairs == ["USDCAD", "USDCHF", "ZARJPY", "TRYJPY", "MXNJPY"]

    from datetime import date as _d
    table, as_of, _notes = mod.per_day_rates([(pairs, rows)],
                                             on_or_before=_d(2026, 9, 30))
    assert table["USDCAD"] == {"buy": 25.0, "sell": -70.0}


def test_granted_days_differ_between_pairs_on_the_same_date():
    """**付与日数は銘柄ごとに違う。**

    同じ 09/11 で USD/CAD は1日、MXN/JPY は2日。「付与日数1の日だけ
    拾う」という選び方をすると、その日の MXN/JPY を丸ごと落とす。
    実際に落として気づいた。
    """
    from datetime import date as _d

    mod = _gaitame_module()
    pairs, rows = mod.parse_page(_PDF_PAGE)
    assert rows[_d(2026, 9, 11)]["USDCAD"][0] == 1
    assert rows[_d(2026, 9, 11)]["MXNJPY"][0] == 2

    table, as_of, _ = mod.per_day_rates([(pairs, rows)],
                                        on_or_before=_d(2026, 9, 30))
    assert set(table) == set(pairs), "付与日数が1でない銘柄が落ちています"
    # 2日分 -30/10 → 1日あたり -15/5
    assert table["MXNJPY"] == {"buy": 5.0, "sell": -15.0}


def test_zero_granted_days_are_skipped():
    """付与日数0は「その日は付かない」。1日あたりの手がかりにならない。"""
    from datetime import date as _d

    mod = _gaitame_module()
    pairs, rows = mod.parse_page(_PDF_PAGE)
    table, as_of, _ = mod.per_day_rates([(pairs, rows)],
                                        on_or_before=_d(2026, 9, 30))
    # 09/14 は全銘柄0日なので採用されない
    assert all(d <= _d(2026, 9, 11) for d in as_of.values())


def test_undetermined_rows_are_not_read_as_zero():
    """まだ決まっていない日（``-``）を0として読まない。"""
    from datetime import date as _d

    mod = _gaitame_module()
    _pairs, rows = mod.parse_page(_PDF_PAGE)
    assert _d(2026, 9, 12) not in rows


def test_shipped_swap_table_matches_the_configured_pairs(cfg):
    """取り込んだ表が26銘柄そろい、出所と日付を持っていること。"""
    if not cfg.swap["available"]:
        pytest.skip("スワップ表が未取り込みです")
    assert set(cfg.swap["table"]) == {p.symbol for p in cfg.pairs}
    assert cfg.swap["source"] and cfg.swap["as_of"]
    for sym, v in cfg.swap["table"].items():
        assert set(v) == {"buy", "sell"}
        # 買いと売りが両方受取になっていない（業者はそうしない）
        assert not (v["buy"] > 0 and v["sell"] > 0), sym



# ================================== 取引単位（外為オンライン 取引要綱）


def test_contract_unit_matches_the_published_trade_units(cfg):
    """**ZARJPY と MXNJPY だけ単位が10倍。**

    取引要綱: 1万通貨コースは1万通貨（数量1）。但し南アフリカランド／円、
    メキシコペソ／円は10万通貨（数量1）。揃えて扱うと、その2銘柄だけ
    建玉が10倍になり、決めたリスクが10倍ずれる。
    """
    from app.risk_engine import contract_unit

    for sym in ("USDJPY", "EURUSD", "GBPNZD"):
        assert contract_unit(cfg.pair(sym), cfg.account) == 10000
    for sym in ("ZARJPY", "MXNJPY"):
        assert contract_unit(cfg.pair(sym), cfg.account) == 100000


def test_the_thousand_unit_course_has_its_own_units(cfg):
    """1,000通貨コースでも、その2銘柄だけ10倍の関係は変わらない。"""
    from app.risk_engine import contract_unit

    acct = {**cfg.account, "course": "L25_1k"}
    assert contract_unit(cfg.pair("USDJPY"), acct) == 1000
    assert contract_unit(cfg.pair("ZARJPY"), acct) == 10000


def test_quantity_respects_the_published_maximum(cfg):
    """数量の上限（1万通貨コースは100）を超えない。"""
    from app.risk_engine import max_quantity

    acct = {**cfg.account,
            "defaults": {**cfg.account["defaults"],
                         "max_margin_use_pct": 100000.0,
                         "max_risk_per_trade_pct": 100.0}}
    r = size_position(cfg.pair("USDJPY"), 10_000_000_000, 50.0,
                      entry=154.15, stop=154.14, rates=RATES, account=acct)
    assert r.ok is True
    assert r.qty <= max_quantity(acct)


def test_a_ten_times_larger_unit_gives_a_ten_times_smaller_quantity(cfg):
    """同じリスク額なら、単位が10倍の銘柄は数量が1/10になる。

    ここが揃っていないと、その銘柄だけ実際のリスクが10倍になる。
    """
    acct = {**cfg.account,
            "defaults": {**cfg.account["defaults"],
                         "max_margin_use_pct": 100000.0}}
    rates = {**RATES, "ZARJPY": 9.52}
    zar = size_position(cfg.pair("ZARJPY"), 10_000_000, 1.0,
                        entry=9.52, stop=9.42, rates=rates, account=acct)
    assert zar.ok is True
    assert zar.contract_unit == 100000
    # 損失は許容の範囲に収まる
    assert loss_at_stop(zar).amount <= 10_000_000 * 0.01 * 1.001
