# -*- coding: utf-8 -*-
"""計画の入力づくり（P2-2）と、画面の入口 ``/api/plan-calc``（P2-4）。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal as D

from fastapi.testclient import TestClient

from app import plan_calc as P
from app import plan_inputs as pi
from app.main import app


def test_the_instrument_comes_from_the_published_terms(cfg):
    """数量の単位はコースの取引要綱から。ZARJPY は10倍。呼値は推定。"""
    z = pi.instrument(cfg, "ZARJPY")
    assert z.contract_unit == 100000 and z.max_qty == 100
    assert z.course == "L25_10k" and z.verified_on == "2026-09-14"
    assert pi.instrument(cfg, "USDJPY").price_tick == D("0.001")
    assert pi.instrument(cfg, "EURUSD").price_tick == D("0.00001")


def test_assumptions_come_from_the_approved_values(cfg):
    c = pi.costs(cfg, "USDJPY")
    assert c.slippage_pips_per_side == D("0.2") and c.conversion_stress == D("0.01")
    assert c.wide_spread == D("0.05")


def test_an_excluded_board_is_carried_into_the_plan(cfg, mock_provider):
    from app.analysis import analyze_pair

    a = analyze_pair("USDJPY", mock_provider, cfg)
    m15 = mock_provider.get_candles("USDJPY", "M15", limit=2)
    close = a.indicators["M15"].close
    board = {"verdict": {"state": "excluded", "exclude": ["対立"]}}
    now = datetime.now(timezone.utc)
    i = pi.build(cfg, "USDJPY", P.LONG, D(str(round(close, 3))),
                 D(str(round(close, 3))) + D("0.009"), a, m15, board, now=now)
    assert i is not None
    assert "BOARD_EXCLUDED" in P.decide(i, now).codes


def test_the_endpoint_rejects_bad_input():
    c = TestClient(app)
    assert c.post("/api/plan-calc/USDJPY", json={"direction": "UP", "bid": "1", "ask": "2"}).status_code == 422
    assert c.post("/api/plan-calc/USDJPY", json={"direction": "LONG", "bid": "abc", "ask": "2"}).status_code == 422
    assert c.post("/api/plan-calc/XXXYYY", json={"direction": "LONG", "bid": "1", "ask": "2"}).status_code == 404


def test_the_endpoint_answers_with_codes_and_never_a_trade_signal():
    """結果は PLAN_OK か NO_TRADE。NO_TRADE は理由コードつき。BUY/SELL は返さない。"""
    c = TestClient(app)
    r = c.post("/api/plan-calc/USDJPY",
               json={"direction": "SHORT", "bid": "1.000", "ask": "1.010"}).json()
    assert r["status"] == "NO_TRADE"
    assert "QUOTE_MISMATCH" in [s["code"] for s in r["stops"]]       # 値が桁違い
    assert r["plan"] is None
    text = str(r)
    assert "BUY" not in text and "SELL" not in text
    assert any("推定" in d for d in r["disclaimers"])
