# -*- coding: utf-8 -*-
"""約2年の1時間足で、前半で見つけ後半で確かめる（long_lab）。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app import long_lab as LL
from app import signal_lab as S

UTC = timezone.utc


def test_decision_times_are_on_the_hour(cfg):
    ts = LL.decision_times(cfg, date(2026, 10, 5), date(2026, 10, 9))
    assert len(ts) == 15 and all(t.minute == 0 for t, _ in ts)
    ny = [t for t, s in ts if s == "newyork"][0]
    assert ny.hour == 13                                     # NY 08:30 現地（夏）＝12:30 UTC → 次の正時


def _series(n=600, step=0.01):
    t0 = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    ts = [t0 + timedelta(hours=i) for i in range(n)]
    c = [150 + (i % 50) * step for i in range(n)]
    return S.Series(ts, list(c), [x + 0.05 for x in c], [x - 0.05 for x in c], list(c), 60)


def test_run_does_not_use_bars_after_the_decision(cfg):
    s = _series()
    times = [(s.ts[400], "tokyo")]
    a = LL.run(cfg, {"USDJPY": s}, {}, times)
    cut = S.Series(s.ts[:401], s.o[:401], s.h[:401], s.l[:401], s.c[:401], 60)
    cut_rows = LL.run(cfg, {"USDJPY": cut}, {}, times)
    if a and cut_rows:                                       # 判断に使う値は先の足があっても同じ
        for k in ("close", "atr", "vol", "trend", "mom", "day"):
            assert a[0][k] == cut_rows[0][k], k


def test_evaluate_splits_before_and_after():
    def row(at, o):
        return {"at": at, "move": 0.5,
                "res": {1.5: {"long": (o, 1.5 if o == "WIN" else -1.0), "short": ("LOSS", -1.0)}}}
    rows = [row("2025-01-10T00:00:00+00:00", "WIN"), row("2025-12-10T00:00:00+00:00", "LOSS")]
    e = LL.evaluate(rows, lambda r: "up", 1.5)
    assert e["前半"]["n"] == 1 and e["前半"]["avg_r"] == 1.5
    assert e["後半"]["n"] == 1 and e["後半"]["avg_r"] == -1.0
