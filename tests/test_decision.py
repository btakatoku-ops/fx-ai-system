# -*- coding: utf-8 -*-
"""今日の FX の判断（decision）。決まった順に確かめ、答えを1つ出す。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import decision as D

J = timezone(timedelta(hours=9))
MON_10 = datetime(2026, 10, 19, 10, 0, tzinfo=J)          # 月曜 10:00（建てられる時刻）


def _b(pair="GBPJPY", state="aligned_up", plan=True, exclude=None, open_=True):
    b = {"pair": pair, "digits": 3, "facts": {"market_open": open_},
         "verdict": {"state": state, "exclude": exclude or []}}
    if state in ("aligned_up", "aligned_down"):
        b["tp_preview"] = ({"status": "PLAN_OK", "entry": "210.000", "stop": "209.700",
                            "targets": [{"price": "210.480"}]} if plan
                           else {"status": "NO_TRADE", "stops": ["必要証拠金が上限を超えます"]})
    return b


def _mr(pair, lean, signal=True, stale=False):
    return {"stale": stale, "horizon": "24時間（翌営業日）",
            "views": {pair: {"lean": lean, "confidence": 0.6, "signal": signal, "status": "シグナルあり"}}}


def test_a_candidate_when_aligned_and_a_plan_is_possible(cfg):
    d = D.decide([_b()], {"health": []}, MON_10, cfg)
    assert d["verdict"] == "candidate" and "GBPJPY 買い" in d["title"]
    assert d["candidates"][0]["entry"] == "210.000"


def test_market_radar_against_turns_it_into_a_skip(cfg):
    d = D.decide([_b(pair="USDJPY")], {"mr": _mr("USDJPY", "down")}, MON_10, cfg)
    assert d["verdict"] == "skip" and "逆の向き" in d["skipped"][0]["why"]
    same = D.decide([_b(pair="USDJPY")], {"mr": _mr("USDJPY", "up")}, MON_10, cfg)
    assert same["verdict"] == "candidate" and "同じ向き" in same["candidates"][0]["mr"]
    weak = D.decide([_b(pair="USDJPY")], {"mr": _mr("USDJPY", "down", signal=False)}, MON_10, cfg)
    assert weak["verdict"] == "candidate"                  # Market Radar 側がシグナルでなければ止めない
    old = D.decide([_b(pair="USDJPY")], {"mr": _mr("USDJPY", "down", stale=True)}, MON_10, cfg)
    assert old["verdict"] == "candidate"                   # 古い Market Radar は使わない


def test_skip_says_why_and_when_to_look_next(cfg):
    boards = [_b(state="excluded", exclude=["H1 と H4 の向きが逆です（上位足と下位足が競合）"]),
              _b(pair="USDJPY", state="excluded", exclude=["H1 と H4 の向きが逆です（上位足と下位足が競合）"])]
    d = D.decide(boards, {}, MON_10, cfg)
    assert d["verdict"] == "skip" and "見送り" in d["title"]
    assert "上位足と下位足の向きが食い違い（2銘柄）" in d["reasons"]
    assert d["next_look_label"].startswith("今日 ")


def test_no_plan_is_not_a_candidate(cfg):
    d = D.decide([_b(plan=False)], {}, MON_10, cfg)
    assert d["verdict"] == "skip" and "計画の条件" in d["skipped"][0]["why"]


def test_order_closed_then_stale_then_time(cfg):
    assert D.decide([_b(open_=False)], {}, MON_10, cfg)["verdict"] == "closed"
    stale = {"health": [{"key": "refresh", "label": "取り込み", "state": "warn", "detail": "40分前"}]}
    assert D.decide([_b()], stale, MON_10, cfg)["verdict"] == "stale"
    brief_only = {"health": [{"key": "brief", "label": "ブリーフ", "state": "warn", "detail": "古い"}]}
    assert D.decide([_b()], brief_only, MON_10, cfg)["verdict"] == "candidate"   # ブリーフだけなら止めない
    late = datetime(2026, 10, 20, 0, 30, tzinfo=J)                                # 刻限 01:00 の30分前
    assert D.decide([_b()], {}, late, cfg)["verdict"] == "wait"


def test_next_look():
    sat = datetime(2026, 10, 17, 12, 0, tzinfo=J)
    assert D.next_look(sat).astimezone(J).strftime("%m/%d %H:%M") == "10/19 08:45"   # 週末は月曜の朝
    noon = datetime(2026, 10, 19, 12, 0, tzinfo=J)
    assert D.next_look(noon).astimezone(J).strftime("%H:%M") == "16:15"              # ロンドン（夏）＋15分


def test_a_reason_is_counted_once_per_pair(cfg):
    b = _b(state="excluded", exclude=["M15 の最後の足が 3 時間前で、古すぎます", "M5 の最後の足が 3 時間前で、古すぎます"])
    d = D.decide([b], {}, MON_10, cfg)
    assert "足が古い（1銘柄）" in d["reasons"]
