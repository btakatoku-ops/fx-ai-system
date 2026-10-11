# -*- coding: utf-8 -*-
"""チャートのフィボナッチ・利確の目安・刻限の線（2026-10-11 利用者の指定）。"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))      # 同じ場所の試験の部品を使う

from app import board_page as P
from app import dashboard_data as D
from app import plan_calc as PC
from app import plan_snapshot as ps
from test_board_page import AT, board, extras
from test_plancalc_js import GEN, ONE_AM, snapshot

UTC = timezone.utc


def _bars(prices):
    t0 = datetime(2026, 10, 9, 0, 0, tzinfo=UTC)
    return [SimpleNamespace(timestamp=t0 + timedelta(minutes=15 * i), open=p, high=p + 0.1,
                            low=p - 0.1, close=p) for i, p in enumerate(prices)]


def test_fib_on_an_up_leg():
    f = D.fib(_bars([100 + i * 0.1 for i in range(20)]))       # 安値 → 高値
    assert f["direction"] == "up"
    rng = f["high"] - f["low"]
    lv = {(l["kind"], l["ratio"]): l["price"] for l in f["levels"]}
    assert abs(lv[("retrace", 0.618)] - (f["high"] - 0.618 * rng)) < 1e-9
    assert abs(lv[("extend", 1.618)] - (f["low"] + 1.618 * rng)) < 1e-9
    assert lv[("extend", 1.272)] > f["high"]


def test_fib_on_a_down_leg():
    f = D.fib(_bars([110 - i * 0.1 for i in range(20)]))
    assert f["direction"] == "down"
    lv = {(l["kind"], l["ratio"]): l["price"] for l in f["levels"]}
    assert lv[("extend", 1.272)] < f["low"] and lv[("retrace", 0.382)] > f["low"]


def test_the_preview_is_the_same_as_the_plan_button():
    """目安は計画ボタンと同じ計算（売値＝終値、買値＝平常時のスプレッドを足した値）。"""
    snap = snapshot()
    got = ps.preview(snap, "LONG", 0.009, GEN, ONE_AM)
    assert got["status"] == "PLAN_OK"
    i = ps.inputs_from_snapshot(snap, "LONG", "150.005", "150.014", GEN, ONE_AM)
    d = PC.decide(i, GEN)
    assert got["entry"] == str(d.plan.entry) and got["stop"] == str(d.plan.stop)
    assert [t["price"] for t in got["targets"]] == [str(t.price) for t in d.plan.targets]


def test_no_preview_on_an_excluded_board():
    got = ps.preview(snapshot(excluded=True), "LONG", 0.009, GEN, ONE_AM)
    assert got["status"] == "NO_TRADE" and got["stops"] and "targets" not in got


def test_the_page_draws_tp_and_fib_and_says_what_they_are():
    ex = extras()
    ch = ex["charts"]["GBPJPY"]
    ch["fib"] = D.fib(_bars([208.3 + i * 0.02 for i in range(20)]))
    b = board()
    b["tp_preview"] = {"status": "PLAN_OK", "direction": "LONG", "entry": "208.610", "stop": "208.400",
                       "stop_basis": "ATR の 2.0 倍",
                       "targets": [{"label": "TP1", "price": "208.930", "net_rr": "1.50", "capped_by": None},
                                   {"label": "TP2", "price": "209.140", "net_rr": "2.50", "capped_by": None}]}
    html = P.render([b], AT, extras=ex)
    assert 'class="tp"' in html and 'class="sl"' in html and "TP1 208.930" in html
    assert "買いで見た場合の利確の目安" in html and "MT4 の値ではありません" in html
    assert "Fib 予測レンジ" in html and "当たりやすさを測っていません" in html


def test_a_stopped_preview_says_why():
    b = board()
    b["tp_preview"] = {"status": "NO_TRADE", "direction": "SHORT", "stops": ["**刻限**まで 30 分しかありません"]}
    html = P.render([b], AT, extras=extras())
    assert "売りで見た場合の利確の目安は出しません" in html and "刻限まで 30 分" in html
    assert 'class="tp"' not in html


def test_the_deadline_line_is_at_the_deadline_not_the_right_edge():
    """刻限（01:00）の後にも足がある日（金曜の夜）は、横軸だけ伸ばす。"""
    ex = extras()
    ch = ex["charts"]["GBPJPY"]
    ch["deadline"] = "2026-09-28T01:00:00+00:00"                # 取引日の始まりから4時間
    ch["bars"] = [[m, 208.5, 208.3, 208.4, 208.4] for m in range(0, 8 * 60, 15)]   # 8時間ぶん
    html = P.render([board()], AT, extras=ex)
    x = float(re.search(r'<line class="dl" x1="([0-9.]+)"', html).group(1))
    assert abs(x - 1000 * 240 / (8 * 60)) < 1                   # 4時間 ÷ 8時間 の位置
