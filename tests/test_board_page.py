# -*- coding: utf-8 -*-
"""外から見る朝のボード（GitHub Pages）。**このページは誰でも見られる。**"""
from __future__ import annotations

from datetime import datetime, timezone

from app import board_page as P


def board(**over):
    b = {
        "pair": "GBPJPY", "digits": 3,
        "facts": {"price": 208.247, "adr": 1.69, "used_ratio": 1.24,
                  "today_low": 208.0, "today_high": 210.08, "today_change_pct": -0.81,
                  "range_low": 207.08, "range_high": 217.05, "position": 0.12,
                  "room_up_adr": 5.2, "room_down_adr": 0.7, "market_open": True,
                  "deadline_jst": "09/29 01:00", "minutes_to_deadline": 990,
                  "levels": [{"price": 208.5, "label": "キリ番", "kind": "round"},
                             {"price": 208.0, "label": "キリ番", "kind": "round"}]},
        "factors": [{"key": "trend", "label": "トレンド", "view": "down", "mark": "▼",
                     "text": "H4 と H1 が下"},
                    {"key": "fundamentals", "label": "ファンダ", "view": "up", "mark": "▲",
                     "text": "金利差", "source": "FXモーニングブリーフ 09/25 08:00 JST",
                     "as_of": "2026-09-25"}],
        "verdict": {"state": "excluded", "label": "除外", "note": "決めるのはご自身です。",
                    "exclude": ["テクニカルとファンダが逆を向いています（対立）"],
                    "cautions": []},
        "brief": {"brief_date": "2026-09-25", "age_days": 0, "lean": "conflict",
                  "excluded": True, "url": "https://claude.ai/artifact/SECRET",
                  "summary": "発注しない", "signals": {"trend": {"view": "down", "mark": "▼"}}},
    }
    b.update(over)
    return b


AT = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)


def test_the_page_says_when_it_was_made():
    """**生きた画面ではない。** いつ時点の写しかを一番上に出す。"""
    html = P.render([board()], AT)
    assert "2026/09/28 09:00 時点の写しです" in html


def test_the_page_is_not_indexed_and_loads_nothing_from_outside():
    html = P.render([board()], AT)
    assert 'content="noindex,nofollow"' in html
    assert "<script" not in html and "http://" not in html
    assert "https://" not in html                   # 外から何も読み込まない


def test_the_brief_link_and_quote_are_not_published():
    """ブリーフ本体へのリンクと、ブリーフの文面の引用は載せない。"""
    html = P.render([board()], AT)
    assert "claude.ai" not in html and "SECRET" not in html
    assert "発注しない" not in html


def test_the_page_never_tells_you_to_trade():
    html = P.render([board()], AT)
    body = html.replace("このページから発注はできません", "")
    for w in ("発注", "買い推奨", "売り推奨", "エントリー"):
        assert w not in body


def test_text_is_escaped():
    b = board()
    b["factors"][0]["text"] = "<script>alert(1)</script>"
    assert "<script>alert" not in P.render([b], AT)


def test_missing_numbers_do_not_break_the_page():
    b = board()
    for k in ("price", "adr", "used_ratio", "range_low", "range_high", "position",
              "today_low", "today_high", "today_change_pct"):
        b["facts"][k] = None
    b["brief"] = None
    html = P.render([b], AT)
    assert "GBPJPY" in html and "—" in html


def test_a_passed_deadline_is_stated():
    b = board()
    b["facts"]["minutes_to_deadline"] = -30
    assert "過ぎました。日替わりまで建てません" in P.render([b], AT)
