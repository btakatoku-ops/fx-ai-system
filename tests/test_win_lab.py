# -*- coding: utf-8 -*-
"""勝率だけを測る検証（win_lab）。根拠が無ければ 50% になる測り方か。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import signal_lab as S
from app import win_lab as W

UTC = timezone.utc


def _s(rows):
    t0 = datetime(2026, 1, 6, 0, 0, tzinfo=UTC)
    ts = [t0 + timedelta(hours=i) for i in range(len(rows))]
    o = [r[0] for r in rows]; h = [r[1] for r in rows]; l = [r[2] for r in rows]; c = [r[3] for r in rows]
    return S.Series(ts, o, h, l, c, 60)


def test_outcome_take_profit_stop_both_and_timeout():
    far = datetime(2030, 1, 1, tzinfo=UTC)
    assert W.outcome(_s([(100, 101.2, 99.9, 101)]), 0, 100, 1, far) == 1.0
    assert W.outcome(_s([(100, 100.1, 98.8, 99)]), 0, 100, 1, far) == 0.0
    assert W.outcome(_s([(100, 101.5, 98.5, 100)]), 0, 100, 1, far) == 0.5      # 足の中で両方 → 分からない
    assert W.outcome(_s([(100, 100.3, 99.8, 100.2)]), 0, 100, 1, far) == 1.0   # 触れずに終わる → 終値の向き
    s = _s([(100, 100.3, 99.8, 100.2), (100.2, 105, 100, 104)])
    assert W.outcome(s, 0, 100, 1, s.ts[1]) == 1.0                               # 刻限の後の足は見ない
    assert W.outcome(s, 0, 100, 1, s.ts[0]) is None


def _row(at, score, session="tokyo"):
    return {"at": at, "score": score, "session": session, "pair": "USDJPY"}


def test_random_direction_is_exactly_half():
    rows = [_row(datetime(2025, 1, 2, tzinfo=UTC) + timedelta(days=i), (i % 3) / 2) for i in range(60)]
    up = W.evaluate(rows, lambda r: "up")["前半"]
    down = W.evaluate(rows, lambda r: "down")["前半"]
    assert abs(up["win"] + down["win"] - 1.0) < 1e-9                             # 買いと売りは裏返し


def test_split_and_verdict():
    rows = ([_row(datetime(2025, 1, 2, tzinfo=UTC) + timedelta(hours=i), 1.0 if i % 10 < 6 else 0.0)
             for i in range(300)] +
            [_row(datetime(2025, 8, 1, tzinfo=UTC) + timedelta(hours=i), 1.0 if i % 10 < 6 else 0.0)
             for i in range(300)])
    e = W.evaluate(rows, lambda r: "up")
    assert e["前半"]["n"] == 300 and e["後半"]["n"] == 300
    assert abs(e["後半"]["win"] - 0.6) < 1e-9
    assert e["後半"]["lo"] < 0.6 < e["後半"]["hi"]                                # 幅は勝率を挟む
    assert W.verdict(e).startswith("◎")
    flat = [dict(r, score=0.5) for r in rows]
    assert W.verdict(W.evaluate(flat, lambda r: "up")) == ""


def test_week_first_groups_sunday_evening_with_monday():
    days = [datetime(2026, 10, 11, 21, tzinfo=UTC) + timedelta(days=i) for i in range(5)]   # 日曜夜〜木曜夜
    assert W._week_first(days, 4) == 0
