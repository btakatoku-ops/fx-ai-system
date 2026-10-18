# -*- coding: utf-8 -*-
"""指標の候補を試す道具（signal_lab）。**未来を見ない**ことを中心に確かめる。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import signal_lab as S

UTC = timezone.utc
T0 = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)


def _series(closes, minutes=60):
    ts = [T0 + timedelta(minutes=minutes * i) for i in range(len(closes))]
    return S.Series(ts, closes, closes, closes, closes, minutes)


def test_only_closed_bars_are_used():
    """1時間足は 時刻＋60分 で確定。07:00 の時点で 07:00 の足はまだ使わない。"""
    s = _series([100, 101, 102, 103, 104, 105, 106, 107, 999])   # 08:00 の足が飛び値
    t = T0 + timedelta(hours=8)                                   # 08:00 ちょうど
    assert s.upto(t) == 8                                         # 07:00 の足（08:00 確定）まで
    assert S._change(s, t, 4) == 107 - 103                        # 999 は入らない


def test_stale_driver_is_not_used():
    s = _series([100, 101, 102, 103, 104, 105])
    assert S._change(s, T0 + timedelta(hours=30), 4) is None      # 最後の確定から12時間超


def test_close_only_driver_csv(tmp_path):
    (tmp_path / "DXY_H1.csv").write_text(
        "timestamp,close\n2026-10-05T00:00:00+00:00,99.1\n2026-10-05T01:00:00+00:00,\n"
        "2026-10-05T02:00:00+00:00,99.3\n", encoding="utf-8")
    d = S.load_driver("DXY", tmp_path)
    assert len(d.ts) == len(d.c) == 2                              # 空の行は捨てる（列はずれない）


def test_dxy_maps_to_the_dollar_side():
    ctx = {"t": T0 + timedelta(hours=8), "drivers": {"DXY": _series([1, 2, 3, 4, 5, 6, 7, 8])}}
    assert S.dxy_4h({"pair": "USDJPY"}, ctx) == "up"
    assert S.dxy_4h({"pair": "EURUSD"}, ctx) == "down"
    assert S.dxy_4h({"pair": "EURJPY"}, ctx) is None


def test_evaluate_compares_with_both_directions_at_the_same_times():
    rows = [{"at": f"2026-10-0{i}T07:00:00+00:00", "pair": "USDJPY", "lean": None, "move": 0.1,
             "long_outcome": "WIN", "long_r": 1.5, "short_outcome": "LOSS", "short_r": -1.0}
            for i in range(1, 5)]
    e = S.evaluate(rows, lambda r, c: "up", lambda r: {})
    assert e["signal"]["n"] == 4 and e["signal"]["avg_r"] == 1.5
    assert e["same_times"]["n"] == 8 and abs(e["same_times"]["avg_r"] - 0.25) < 1e-9
    assert e["edge_r"] == 1.25 and e["both_halves_better"]
    assert e["direction"]["rate"] == 1.0


def test_adx_uses_only_closed_h1_bars():
    h1 = _series([100.0] * 10)
    adx = [None, None, 10, 12, 15, 20, 26, 30, 99, 99]
    ctx = {"t": T0 + timedelta(hours=8), "h1": h1, "adx": adx}         # 07:00 の足（08:00 確定）まで
    assert S.h1_adx({}, ctx) == 30
    row = {"factors": {"trend": "up"}, "lean": "up"}
    assert S.adx_trend(row, ctx) == "up" and S.adx_lean(row, ctx) == "up"
    ctx["adx"] = [None] * 7 + [20, 99, 99]
    assert S.adx_trend(row, ctx) is None


def test_bb_squeeze_break_needs_a_recent_squeeze():
    n = 400
    m15 = _series([100.0] * n, minutes=15)
    up, lo = [101.0] * n, [99.0] * n
    width = [2.0] * n
    width[-3] = 0.1                                                    # 直近に収縮
    m15.c[-1] = 102.0                                                  # 上の帯を超えて引けた
    t = m15.close_ts[-1]
    ctx = {"t": t, "m15": m15, "bb": (up, lo, width)}
    assert S.bb_squeeze_break({}, ctx) == "up"
    ctx["bb"] = (up, lo, [2.0] * n)                                    # 収縮が無ければ数えない
    assert S.bb_squeeze_break({}, ctx) is None
