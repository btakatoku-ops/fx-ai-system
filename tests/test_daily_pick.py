# -*- coding: utf-8 -*-
"""ダッシュボードの非円建て3本（取引日ごとに選ぶ）と、円換算レート。"""
from __future__ import annotations

from datetime import timedelta

from app import daily_pick as DP


def _now(provider):
    return provider.get_candles("EURUSD", "M15", limit=1).candles[-1].timestamp + timedelta(minutes=5)


def test_candidates_are_the_non_jpy_pairs(cfg):
    c = DP.candidates(cfg)
    assert "EURUSD" in c and "GBPUSD" in c
    assert not [s for s in c if "JPY" in s]


def test_reach_is_room_over_the_distance_to_tp1(cfg, mock_provider):
    now = _now(mock_provider)
    r = DP.reach(cfg, mock_provider, "EURUSD", now)
    assert "reach" in r, r
    assert abs(r["reach"] - r["room_pips"] / r["tp1_pips"]) < 0.01
    assert r["tp1_pips"] > r["spread_pips"]                    # 費用込みの距離


def test_the_pick_is_the_top_three(cfg, mock_provider):
    got = DP.compute(cfg, mock_provider, _now(mock_provider))
    ranked = [r for r in got["table"] if "reach" in r]
    assert got["pairs"] == [r["pair"] for r in ranked[:3]]
    # 費用の条件を満たすもの → 満たさないもの、それぞれの中で届きやすい順
    for flag in (True, False):
        part = [r["reach"] for r in ranked if r["cost_ok"] is flag]
        assert part == sorted(part, reverse=True)


def test_stale_pairs_are_not_candidates(cfg, mock_provider):
    later = _now(mock_provider) + timedelta(hours=5)
    r = DP.reach(cfg, mock_provider, "EURUSD", later)
    assert "reach" not in r and "古い" in r["reason"]


def test_the_pick_is_made_once_per_session(cfg, mock_provider, tmp_path, monkeypatch):
    path = tmp_path / "pick.json"
    now = _now(mock_provider)
    first = DP.today(cfg, mock_provider, now, path)
    assert first["status"] == "today" and path.exists() and first["key"] == DP.slot_key(cfg, now)
    # 同じ時間帯のうちは、足が古くなっても選び直さない
    monkeypatch.setattr(DP, "slot_key", lambda c, t: first["key"])
    again = DP.today(cfg, mock_provider, now + timedelta(hours=3), path)
    assert again["pairs"] == first["pairs"] and again["picked_at"] == first["picked_at"]
    # 次の時間帯に入れば選び直す（足が新しければ）
    monkeypatch.setattr(DP, "slot_key", lambda c, t: "next")
    later = DP.today(cfg, mock_provider, now + timedelta(minutes=1), path)
    assert later["picked_at"] != first["picked_at"]


def test_sessions_follow_the_london_and_newyork_opens(cfg):
    from datetime import datetime, timezone
    at = lambda h, m: datetime(2026, 10, 9, h, m, tzinfo=timezone.utc)   # 英米とも夏時間
    assert DP._slot(cfg, at(0, 0)) == "tokyo"          # 09:00 JST
    assert DP._slot(cfg, at(6, 59)) == "tokyo"         # 15:59 JST
    assert DP._slot(cfg, at(7, 0)) == "london"         # 16:00 JST
    assert DP._slot(cfg, at(12, 30)) == "newyork"      # 21:30 JST


def test_a_pick_needs_most_candidates_fresh(cfg, mock_provider, monkeypatch):
    """前の3本だけ足が新しい、という状態では選ばない（同じ3本が選ばれ続けるため）。"""
    now = _now(mock_provider)
    real = DP.reach
    fresh = set(DP.candidates(cfg)[:3])
    monkeypatch.setattr(DP, "reach", lambda c, p, s, t: real(c, p, s, t) if s in fresh
                        else {"pair": s, "reason": "足が古い"})
    assert DP.compute(cfg, mock_provider, now)["complete"] is False


def test_without_fresh_candidates_the_previous_pick_is_kept(cfg, mock_provider, tmp_path):
    path = tmp_path / "pick.json"
    now = _now(mock_provider)
    first = DP.today(cfg, mock_provider, now, path)
    nextday = now + timedelta(days=1)                           # 足は古いまま
    got = DP.today(cfg, mock_provider, nextday, path)
    assert got["status"] == "previous" and got["pairs"] == first["pairs"]
    nothing = DP.today(cfg, mock_provider, nextday, tmp_path / "none.json")
    assert nothing["status"] == "none"


def test_conversion_symbols():
    assert DP.conversion_symbols(["EURUSD"]) == ["USDJPY", "EURJPY"]
    assert DP.conversion_symbols(["USDCHF", "EURUSD"]) == ["CHFJPY", "USDJPY", "EURJPY"]
    assert DP.conversion_symbols(["USDJPY"]) == ["USDJPY"]


def test_stale_rates_are_left_out(cfg, mock_provider):
    now = mock_provider.get_candles("USDJPY", "M5", limit=1).candles[-1].timestamp
    fresh = DP.conversion_rates(cfg, mock_provider, ["EURUSD"], now + timedelta(minutes=5))
    assert set(fresh) == {"USDJPY", "EURJPY"}
    stale = DP.conversion_rates(cfg, mock_provider, ["EURUSD"], now + timedelta(minutes=40))
    assert stale == {}                                         # 古ければ入れない（計画は止まる）


def test_the_trading_day_is_named_by_the_japan_date():
    """23:00 JST（14:00 UTC）は、その日の朝（日本時間）に始まった取引日。"""
    from datetime import datetime, timezone
    assert DP._day(datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)) == "2026-10-09"
    assert DP._day(datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)) == "2026-10-09"   # 05:00 JST はまだ前の日
    assert DP._day(datetime(2026, 10, 9, 22, 30, tzinfo=timezone.utc)) == "2026-10-10"  # 07:30 JST は次の日


def test_pairs_within_the_cost_rule_come_first(cfg, mock_provider):
    """エンジンの費用の条件（スプレッド÷ATR が上限以下）を満たす銘柄を先に選ぶ。"""
    got = DP.compute(cfg, mock_provider, _now(mock_provider))
    ranked = [r for r in got["table"] if "reach" in r]
    flags = [r["cost_ok"] for r in ranked]
    assert flags == sorted(flags, reverse=True)                 # True が先
    lim = cfg.filters["spread"]["max_ratio_of_atr"]
    for r in ranked:
        assert r["cost_ok"] == (r["spread_atr"] <= lim + 1e-3 and
                                r["spread_pips"] <= cfg.filters["spread"]["max_pips"].get(
                                    r["pair"], cfg.filters["spread"]["max_pips"]["default"]))


def test_the_page_warns_when_a_pick_is_over_the_cost_rule():
    from datetime import datetime, timezone
    from app import board_page as P
    pick = {"status": "today", "fx_day": "2026-10-09", "picked_at": "2026-10-09T00:00:00+00:00",
            "pairs": ["EURCAD"],
            "table": [{"pair": "EURCAD", "reach": 1.1, "tp1_pips": 50, "room_pips": 55,
                       "spread_pips": 4.2, "spread_atr": 0.27, "cost_ok": False}]}
    html = P._pick(pick)
    assert "EURCAD は条件を超えたまま" in html and "計画は止まります" in html and "27%" in html


def test_pairs_that_never_met_the_cost_rule_are_not_candidates(cfg):
    """focus.json の pass_rate が 0 の銘柄は候補にしない（取り込みを増やすだけなので）。"""
    rows = cfg.focus["pairs"]
    c = DP.candidates(cfg)
    assert c and all(float(rows[s]["pass_rate"]) > 0 for s in c)
    assert "EURUSD" in c and "AUDUSD" not in c                  # AUDUSD は実測 0


def _pairs_script():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "dashboard_pairs.py"
    spec = importlib.util.spec_from_file_location("dashboard_pairs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_refresh_list_is_split_into_core_and_extra(cfg):
    from datetime import datetime, timezone
    m = _pairs_script()
    now = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
    saved = {"key": DP.slot_key(cfg, now), "pairs": ["EURUSD", "AUDCHF", "USDCAD"]}
    core = m.core(cfg, saved)
    assert core[:3] == ["USDJPY", "GBPJPY", "EURJPY"]
    assert {"EURUSD", "AUDCHF", "USDCAD", "AUDJPY", "CHFJPY", "CADJPY"} <= set(core)
    assert m.extra(cfg, saved, now) == []                      # この時間帯は選び済み
    later = m.extra(cfg, {**saved, "key": "old"}, now)          # まだ選んでいない
    assert later and not set(later) & set(core)
    assert set(DP.candidates(cfg)) <= set(core) | set(later)
