# -*- coding: utf-8 -*-
"""数日持つ型の検証（swing_lab）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import signal_lab as S
from app import swing_lab as W

UTC = timezone.utc


def _h1(days=10, base=150.0, drift=0.0):
    t0 = datetime(2026, 3, 1, 22, 0, tzinfo=UTC)               # 為替の1日の始まり（冬）
    ts = [t0 + timedelta(hours=i) for i in range(24 * days)]
    c = [base + drift * i for i in range(len(ts))]
    return S.Series(ts, list(c), [x + 0.02 for x in c], [x - 0.02 for x in c], list(c), 60)


def test_daily_bars_group_by_the_fx_day():
    d = W.daily_bars(_h1(5))
    assert len(d) == 5
    assert d[1].start - d[0].start == timedelta(days=1)


def test_swap_is_converted_to_price_units(cfg):
    tbl = cfg.swap["table"]
    v = W.swap_per_day(cfg, "USDJPY", True, None)
    assert abs(v - tbl["USDJPY"]["buy"] / 10000) < 1e-12
    e = W.swap_per_day(cfg, "EURUSD", True, 150.0)
    assert abs(e - tbl["EURUSD"]["buy"] / 10000 / 150.0) < 1e-12


def test_a_rising_market_pays_a_long_and_charges_a_short(cfg):
    h1 = _h1(30, drift=0.01)
    days = W.daily_bars(h1)
    up = W.simulate(cfg, "USDJPY", h1, days, 3, True, 0.5, 5, None, 0.0)
    dn = W.simulate(cfg, "USDJPY", h1, days, 3, False, 0.5, 5, None, 0.0)
    assert up["r"] > 0 > dn["r"]
    assert dn["outcome"] in ("TIME", "LOSS")
