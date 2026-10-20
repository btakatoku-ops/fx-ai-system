# -*- coding: utf-8 -*-
"""押し目・戻り（参考・測定中）。決まりは win_lab と共通で、確定した足だけを使う。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app import board_page as P
from app import pullback
from app import win_lab as W

UTC = timezone.utc


def test_rule_is_shared_with_the_lab():
    assert W.pullback_lean("up", 38) == "up"
    assert W.pullback_lean("up", 45) is None
    assert W.pullback_lean("down", 62) == "down"
    assert W.pullback_lean(None, 20) is None
    assert W.daily_trend(110, 105, 100) == "up"
    assert W.daily_trend(110, 105, 106) is None                # 20日線が下向きなら流れなし


class _Prov:
    def __init__(self, bars):
        self.bars = bars

    def get_candles(self, symbol, tf, limit=300):
        return SimpleNamespace(candles=self.bars[-limit:])


def _bars(n_days=35, end=datetime(2026, 10, 20, 3, tzinfo=UTC), drop_last_hours=0):
    out = []
    t = end - timedelta(hours=24 * n_days)
    i = 0
    while t < end:
        if t.weekday() < 5:
            # ゆっくり上げて、最後の10時間は下げる（日足は上の流れ・1時間足の RSI は低い）
            px = 100 + i * 0.01 if t < end - timedelta(hours=10) else out[-1].close - 0.08
            out.append(SimpleNamespace(timestamp=t, open=px, high=px + 0.02, low=px - 0.02, close=px))
            i += 1
        t += timedelta(hours=1)
    return out


def test_state_finds_a_pullback_in_an_uptrend():
    now = datetime(2026, 10, 20, 3, 5, tzinfo=UTC)
    st = pullback.state(_Prov(_bars()), "EURUSD", now)
    assert st["daily"] == "up" and st["lean"] == "up" and st["rsi"] <= 40
    assert st["jpy"] is False and st["stale"] is False


def test_state_does_not_use_unfinished_bars():
    bars = _bars()
    now = bars[-1].timestamp + timedelta(minutes=30)         # 最後の足はまだ確定していない
    a = pullback.state(_Prov(bars), "EURUSD", now)
    b = pullback.state(_Prov(bars[:-1]), "EURUSD", now)
    assert a == b


def test_stats_reads_the_latest_results(tmp_path):
    g = {"前半": {"win": 0.54, "n": 1000, "lo": 0.5}, "後半": {"win": 0.58, "n": 900, "lo": 0.53}}
    j = {"前半": {"win": 0.506, "n": 400, "lo": 0.44}, "後半": {"win": 0.457, "n": 370, "lo": 0.40}}
    (tmp_path / "win-2026-10-20.json").write_text(json.dumps(
        {"first": "2024-01-21", "last": "2026-10-16", "split": "2025-07-01",
         "pullback_groups": {"jpy": j, "other": g}}), encoding="utf-8")
    (tmp_path / "win-m15-2026-10-20.json").write_text(json.dumps(
        {"first": "2026-07-29", "last": "2026-10-20",
         "pullback_groups": {"jpy": {"win": 0.469, "n": 228}, "other": {"win": 0.534, "n": 268}}}),
        encoding="utf-8")
    st = pullback.stats(tmp_path)
    assert st["long"]["other"]["b"] == 0.58 and st["m15"]["jpy"]["n"] == 228
    assert pullback.stats(tmp_path / "none") is None


def _st():
    return {"long": {"first": "2024-01-21", "last": "2026-10-16", "split": "2025-07-01",
                     "jpy": {"a": 0.506, "b": 0.457, "a_n": 399, "b_n": 369, "b_lo": 0.40},
                     "other": {"a": 0.541, "b": 0.578, "a_n": 1043, "b_n": 881, "b_lo": 0.53}},
            "m15": {"first": "2026-07-29", "last": "2026-10-20",
                    "jpy": {"win": 0.469, "n": 228}, "other": {"win": 0.534, "n": 268}}}


def test_note_says_it_does_not_work_for_yen_pairs():
    b = {"pair": "USDJPY", "verdict": {"state": "mixed"}}
    html = P._pb_note(b, {"lean": "up", "daily": "up", "rsi": 35, "jpy": True}, _st())
    assert "押し目買いの場面" in html and "円がらみでは効いていません" in html
    assert "判定（そろう・除外）と今日の判断には入れていません" in html


def test_note_for_other_pairs_and_opposite_board():
    b = {"pair": "EURUSD", "verdict": {"state": "aligned_up"}}
    html = P._pb_note(b, {"lean": "down", "daily": "down", "rsi": 65, "jpy": False}, _st())
    assert "戻り売りの場面" in html and "唯一" in html
    assert "逆の向き" in html
    none = P._pb_note(b, {"lean": None, "daily": None, "rsi": 50, "jpy": False}, None)
    assert "はっきりしません" in none
    assert P._pb_note(b, None, _st()) == ""


def test_note_states_the_cost_result():
    st = _st()
    st["cost"] = {"first": "2024-01-21", "last": "2026-10-16",
                  "other": {"a": -0.124, "b": -0.140, "b_hi": -0.05, "a0": -0.251, "b0": -0.261},
                  "jpy": {"a": 0.024, "b": -0.156, "b_hi": -0.02, "a0": -0.06, "b0": -0.086}}
    b = {"pair": "EURUSD", "verdict": {"state": "mixed"}}
    html = P._pb_note(b, {"lean": "up", "daily": "up", "rsi": 35, "jpy": False}, st)
    assert "費用込み" in html and "-0.14R" in html and "費用を超えていません" in html


def test_stats_reads_the_cost_result(tmp_path):
    e = {"a": {"avg": -0.12}, "b": {"avg": -0.14, "hi": -0.05}, "a0": {"avg": -0.25}, "b0": {"avg": -0.26}}
    (tmp_path / "win-cost-2026-10-20.json").write_text(json.dumps(
        {"long_period": ["2024-01-21", "2026-10-16"], "long": {"円以外_1_1.5": e, "円がらみ_1_1.5": e}}),
        encoding="utf-8")
    st = pullback.stats(tmp_path)
    assert st["cost"]["other"]["b"] == -0.14 and st["cost"]["jpy"]["a0"] == -0.25
