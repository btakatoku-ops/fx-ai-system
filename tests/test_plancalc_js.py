# -*- coding: utf-8 -*-
"""公開ページの計画計算（static/plancalc.cjs）が、Python の plan_calc と同じ結果を出すか。

**ページの計算を別に作ると、いつの間にか食い違う。** 乱数で作った数百の入力を両方に
通し、判定・理由コード・建値・損切り・利確・数量・円の額が一致することを確かめる。
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import plan_calc as P
from app import plan_inputs as pi
from app import plan_snapshot as ps

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "backend" / "app" / "static" / "plancalc.cjs"
UTC = timezone.utc
GEN = datetime(2026, 9, 29, 5, 0, tzinfo=UTC)                  # 火曜 14:00 JST
ONE_AM = {"day_trade": {"enabled": True, "force_exit_jst": "01:00",
                        "force_exit_jst_dst": "01:00", "min_minutes_to_exit": 90}}

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が無い")


def snapshot(pair="USDJPY", base="USD", close="150.005", atr="0.100", tick="0.001",
             pip="0.01", unit=10000, long_swing=None, long_barrier=None,
             short_swing=None, short_barrier=None, excluded=False, balance="3000000",
             risk="0.5", wide="0.05", course="L25_10k", gen=GEN, quote="JPY", rates=None):
    from app import day_trade

    deadline = day_trade.exit_deadline(ONE_AM, gen)
    return {
        "pair": pair, "generated_at": gen.isoformat(), "valid_minutes": 20,
        "last_bar_at": (gen - timedelta(minutes=15)).isoformat(),
        "reference_close": close, "atr": atr,
        "long": {"swing": long_swing, "barrier": long_barrier},
        "short": {"swing": short_swing, "barrier": short_barrier},
        "instrument": {"symbol": pair, "base": base, "quote": quote, "pip": pip,
                       "price_tick": tick, "contract_unit": unit, "min_qty": 1,
                       "qty_step": 1, "max_qty": 100, "course": course,
                       "verified_on": "2026-09-14"},
        "account": {"balance": balance, "risk_pct": risk, "max_risk_pct": "2",
                    "max_margin_pct": "20", "margin_rate": "0.04",
                    "course": "L25_10k", "currency": "JPY"},
        "rules": {"atr_multiple": "2.0", "min_atr_multiple": "1.0",
                  "structure_buffer_atr": "0.25", "use_structure": True,
                  "r_multiples": ["1.5", "2.5", "3.5"], "min_rr": "1.5",
                  "barrier_offset_atr": "0.1"},
        "costs": {"slippage_pips_per_side": "0.2", "conversion_stress": "0.01",
                  "wide_spread": wide, "max_spread_x_wide": "3", "max_gap_atr": "3"},
        "board": {"excluded": excluded, "reasons": ["対立"] if excluded else []},
        "deadline": deadline.isoformat(),
        "next_rollover": (day_trade.rollover_start(gen) + timedelta(days=1)).isoformat(),
        "min_minutes_to_exit": 90,
        "rates": {k: {"value": v, "observed_at": (gen - timedelta(minutes=5)).isoformat()}
                  for k, v in (rates or {}).items()},
    }


def run_js(cases):
    script = (
        "const pc = require(process.argv[1]);"
        "const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "const out = cases.map(c => pc.decide(c.snap, c.dir, c.bid, c.ask, new Date(c.now)));"
        "process.stdout.write(JSON.stringify(out));")
    got = subprocess.run(["node", "-e", script, str(JS)], input=json.dumps(cases),
                         capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(got.stdout)


def run_py(c):
    now = datetime.fromisoformat(c["now"])
    i = ps.inputs_from_snapshot(c["snap"], c["dir"], c["bid"], c["ask"], now, ONE_AM)
    d = P.decide(i, now)
    return pi.decision_dict(d, i), d


def comparable_plan(p):
    if p is None:
        return None
    return {k: p[k] for k in ("direction", "entry", "stop", "stop_basis", "qty", "units",
                              "loss_jpy", "risk_amount_jpy", "margin_jpy", "notes")} | {
        "targets": [{k: t[k] for k in ("label", "price", "net_rr", "profit_jpy", "capped_by")}
                    for t in p["targets"]]}


def check(cases):
    js = run_js(cases)
    for c, j in zip(cases, js):
        py, d = run_py(c)
        assert j["status"] == py["status"], (c, j, py)
        assert [s["code"] for s in j["stops"]] == [s["code"] for s in py["stops"]], (c, j, py)
        assert comparable_plan(j["plan"]) == comparable_plan(py["plan"]), (c, j, py)


def test_the_hand_checked_example_matches():
    """USDJPY 買い・ask 150.010・ATR 0.100。Python の受入試験と同じ例。"""
    snap = snapshot()
    cases = [{"snap": snap, "dir": "LONG", "bid": "150.000", "ask": "150.010",
              "now": GEN.isoformat()}]
    check(cases)
    j = run_js(cases)[0]
    assert j["status"] == "PLAN_OK" and j["plan"]["entry"] == "150.010"


def test_stops_match():
    s = snapshot()
    cases = [
        {"snap": s, "dir": "LONG", "bid": "150.010", "ask": "150.000", "now": GEN.isoformat()},
        {"snap": s, "dir": "LONG", "bid": "150.000", "ask": "150.300", "now": GEN.isoformat()},
        {"snap": s, "dir": "LONG", "bid": "160.000", "ask": "160.010", "now": GEN.isoformat()},
        {"snap": s, "dir": "LONG", "bid": "150.000", "ask": "150.010",
         "now": (GEN + timedelta(minutes=20)).isoformat()},             # 写しが古い
        {"snap": snapshot(excluded=True), "dir": "SHORT", "bid": "150.000", "ask": "150.010",
         "now": GEN.isoformat()},
        {"snap": snapshot(long_barrier="150.300"), "dir": "LONG", "bid": "150.000",
         "ask": "150.010", "now": GEN.isoformat()},
        {"snap": snapshot(balance="300000"), "dir": "LONG", "bid": "150.000",
         "ask": "150.010", "now": GEN.isoformat()},
        {"snap": snapshot(course="L25_1k"), "dir": "LONG", "bid": "150.000",
         "ask": "150.010", "now": GEN.isoformat()},
        {"snap": snapshot(risk="2.5"), "dir": "LONG", "bid": "150.000", "ask": "150.010",
         "now": GEN.isoformat()},
    ]
    check(cases)
    codes = [[s["code"] for s in r["stops"]] for r in run_js(cases)]
    for want, got in zip(["QUOTE_INVALID", "QUOTE_INVALID", "QUOTE_MISMATCH", "PLAN_EXPIRED",
                          "BOARD_EXCLUDED", "NET_RR_BELOW_MIN", "QTY_BELOW_MIN",
                          "SPEC_UNVERIFIED", "RISK_ABOVE_CAP"], codes):
        assert want in got, (want, got)


def test_the_deadline_matches_across_the_day():
    """23:31・01:30（JST）は止まる。日替わりは写しの寿命の中では跨がない。"""
    for h, m in ((14, 31), (15, 50), (16, 30)):            # UTC = JST 23:31 / 00:50 / 01:30
        gen = datetime(2026, 9, 29, h, m, tzinfo=UTC)
        s = snapshot(gen=gen)
        check([{"snap": s, "dir": d, "bid": "150.000", "ask": "150.010",
                "now": (gen + timedelta(minutes=1)).isoformat()} for d in ("LONG", "SHORT")])


def test_random_inputs_match():
    rng = random.Random(20261001)
    cases = []
    for _ in range(400):
        pair, base, unit, tick, pip, mid = rng.choice([
            ("USDJPY", "USD", 10000, "0.001", "0.01", 150.0),
            ("GBPJPY", "GBP", 10000, "0.001", "0.01", 208.0),
            ("ZARJPY", "ZAR", 100000, "0.001", "0.01", 8.5),
        ])
        atr = round(mid * rng.uniform(0.0003, 0.004), 3)
        close = round(mid + rng.uniform(-atr, atr), 3)
        spread = rng.choice([0.004, 0.009, 0.01, 0.02])
        bid = round(close + rng.uniform(-atr, atr), 3)
        ask = round(bid + spread, 3)
        def maybe(lo, hi):
            return None if rng.random() < 0.4 else f"{round(close + rng.uniform(lo, hi) * atr, 3):.3f}"
        snap = snapshot(
            pair=pair, base=base, unit=unit, tick=tick, pip=pip,
            close=f"{close:.3f}", atr=f"{atr:.3f}",
            long_swing=maybe(-4, -0.5), long_barrier=maybe(0.5, 8),
            short_swing=maybe(0.5, 4), short_barrier=maybe(-8, -0.5),
            balance=str(rng.choice([300000, 1000000, 3000000, 2799999, 12345678])),
            risk=rng.choice(["0.3", "0.5", "1", "1.7"]),
            excluded=rng.random() < 0.15)
        cases.append({"snap": snap, "dir": rng.choice(["LONG", "SHORT"]),
                      "bid": f"{bid:.3f}", "ask": f"{ask:.3f}",
                      "now": (GEN + timedelta(minutes=rng.randint(0, 25))).isoformat()})
    check(cases)
    assert sum(1 for r in run_js(cases) if r["status"] == "PLAN_OK") > 20   # 計画が出る例も十分ある


def test_a_snapshot_built_from_real_analysis_matches(cfg, mock_provider):
    """plan_snapshot.build が作った実物の写しでも一致する。"""
    from app.analysis import analyze_pair
    from app.board import build_board

    a = analyze_pair("USDJPY", mock_provider, cfg)
    m15 = mock_provider.get_candles("USDJPY", "M15", limit=2)
    now = GEN
    board = build_board(cfg, mock_provider, "USDJPY", a, now)
    snap = ps.build(cfg, "USDJPY", a, m15, board, now)
    assert snap is not None
    json.dumps(snap)                                     # そのまま JSON にできる
    close = float(snap["reference_close"])
    cases = [{"snap": snap, "dir": d, "bid": f"{close:.3f}", "ask": f"{close + 0.009:.3f}",
              "now": now.isoformat()} for d in ("LONG", "SHORT")]
    js = run_js(cases)
    for c, j in zip(cases, js):
        py, _ = run_py({**c, "snap": snap})
        assert [s["code"] for s in j["stops"]] == [s["code"] for s in py["stops"]]
        assert comparable_plan(j["plan"]) == comparable_plan(py["plan"])


# ------------------------------------------------ 円建てでない銘柄（2026-10-09）


def test_non_jpy_pairs_use_the_rates_in_the_snapshot():
    """EURUSD・USDCHF。損益は決済通貨→円、証拠金は基軸通貨→円。どちらも写しのレート。"""
    rng = random.Random(20261009)
    cases = []
    for _ in range(300):
        pair, base, quote, mid, rates = rng.choice([
            ("EURUSD", "EUR", "USD", 1.12, {"USDJPY": "158.252", "EURJPY": "177.314"}),
            ("GBPUSD", "GBP", "USD", 1.32, {"USDJPY": "158.252", "GBPJPY": "209.209"}),
            ("USDCHF", "USD", "CHF", 0.80, {"USDJPY": "158.252", "CHFJPY": "197.81"}),
        ])
        atr = round(mid * rng.uniform(0.0003, 0.003), 5)
        close = round(mid + rng.uniform(-atr, atr), 5)
        bid = round(close + rng.uniform(-atr, atr), 5)
        ask = round(bid + rng.choice([0.00014, 0.00028, 0.0003]), 5)
        snap = snapshot(pair=pair, base=base, quote=quote, unit=10000, tick="0.00001",
                        pip="0.0001", close=f"{close:.5f}", atr=f"{atr:.5f}", wide="0.0005",
                        balance=str(rng.choice([1000000, 3000000, 12345678])),
                        risk=rng.choice(["0.5", "1"]), rates=rates)
        cases.append({"snap": snap, "dir": rng.choice(["LONG", "SHORT"]),
                      "bid": f"{bid:.5f}", "ask": f"{ask:.5f}",
                      "now": (GEN + timedelta(minutes=rng.randint(0, 25))).isoformat()})
    check(cases)
    ok = [r for r in run_js(cases) if r["status"] == "PLAN_OK"]
    assert len(ok) > 20


def test_a_missing_rate_stops_the_plan_in_both():
    """円換算のレートが無ければ止める（1.0 では代用しない）。"""
    for rates in ({}, {"USDJPY": "158.252"}):            # 証拠金の EURJPY が無い場合も
        snap = snapshot(pair="EURUSD", base="EUR", quote="USD", tick="0.00001", pip="0.0001",
                        close="1.12000", atr="0.00100", wide="0.0005", rates=rates)
        cases = [{"snap": snap, "dir": "LONG", "bid": "1.12000", "ask": "1.12014",
                  "now": GEN.isoformat()}]
        check(cases)
        assert "CONVERSION_MISSING" in [s["code"] for s in run_js(cases)[0]["stops"]]


def test_the_loss_in_yen_uses_the_rate_with_stress():
    """EURUSD 買い：損失の円＝距離×通貨量×USDJPY×1.01（切り上げ）。"""
    snap = snapshot(pair="EURUSD", base="EUR", quote="USD", tick="0.00001", pip="0.0001",
                    close="1.12000", atr="0.00100", wide="0.0005", balance="3000000",
                    rates={"USDJPY": "150", "EURJPY": "168"})
    c = {"snap": snap, "dir": "LONG", "bid": "1.12000", "ask": "1.12014", "now": GEN.isoformat()}
    check([c])
    p = run_js([c])[0]["plan"]
    assert p is not None
    from decimal import Decimal, ROUND_CEILING
    dist = Decimal(p["entry"]) - Decimal(p["stop"]) + 2 * Decimal("0.00002")
    want = (dist * p["units"] * Decimal("150") * Decimal("1.01")).to_integral_value(ROUND_CEILING)
    assert p["loss_jpy"] == int(want)
