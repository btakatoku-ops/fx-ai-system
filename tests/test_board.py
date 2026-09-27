# -*- coding: utf-8 -*-
"""朝のボード。**予測はしない。事実を揃え、やめるべき時をはっきり言う。**

ここで確かめること。

1. **為替の1日はニューヨーク17時で替わる。** 日本時間の0時で切らない。
2. **配信元の穴で欠けた日を、値幅の小さい日として数えない。**
3. **時刻の揃っていない行・閉場中の足を、足として使わない。**
4. **ファンダは機械が作らない。** 無ければ「未入力」、古ければ判定に使わない。
5. **除外の理由は、強制条件と明示した決まりだけ。** 点数の説明や期限切れを
   除外の理由にしない。
6. **ボードは「発注」とは書かない。**
7. **記録は1銘柄1日1枚、採点は刻限までの足だけ。**
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import board as B
from app import board_log
from app.analysis import analyze_pair
from app.market_data import MockMarketDataProvider
from app.models import Candle

UTC = timezone.utc
JST = timezone(timedelta(hours=9))


def candles(start: datetime, minutes: int, n: int, base: float = 150.0,
            step: float = 0.0, spread: float = 0.1):
    return [
        Candle(timestamp=start + timedelta(minutes=minutes * i),
               open=base + step * i, high=base + step * i + spread,
               low=base + step * i - spread, close=base + step * i, volume=0)
        for i in range(n)
    ]


# ------------------------------------------------ 為替の1日


def test_the_fx_day_changes_at_new_york_five_pm():
    """**日本時間の0時で切らない。** 東京の朝が「前日」に入ってしまう。"""
    # 夏時間: 21:00 UTC（日本時間 06:00）で替わる
    assert B.fx_day_start(datetime(2026, 9, 22, 20, 59, tzinfo=UTC)) == \
        datetime(2026, 9, 21, 21, 0, tzinfo=UTC)
    assert B.fx_day_start(datetime(2026, 9, 22, 21, 0, tzinfo=UTC)) == \
        datetime(2026, 9, 22, 21, 0, tzinfo=UTC)
    # 冬: 22:00 UTC（日本時間 07:00）
    assert B.fx_day_start(datetime(2026, 12, 22, 21, 30, tzinfo=UTC)) == \
        datetime(2026, 12, 21, 22, 0, tzinfo=UTC)


# ------------------------------------------------ 事実


def _days_of_h1(first_day: datetime, days: int, bars_per_day: int = 24,
                spread: float = 0.5):
    out = []
    for d in range(days):
        start = first_day + timedelta(days=d)
        out += candles(start, 60, bars_per_day, spread=spread)
    return out


def test_adr_ignores_days_with_missing_bars(cfg):
    """**欠けた日を「値幅の小さい日」として数えない。**

    配信元に 19:25〜23:00 が丸ごと無い日があった。そのまま数えると、
    普段の値幅が小さく出て、今日の消化率が大きく出る。
    """
    start = datetime(2026, 9, 1, 21, 0, tzinfo=UTC)
    h1 = _days_of_h1(start, 10, spread=0.5)                 # 値幅 1.0 の日
    # 11日目は4本しかない（配信元の穴）。値幅も小さい
    h1 += candles(start + timedelta(days=10), 60, 4, spread=0.05)
    now = start + timedelta(days=12, hours=3)
    m15 = candles(now - timedelta(hours=2), 15, 8)
    f = B.facts_for(cfg, "USDJPY", h1, m15, now)
    # 欠けた日（値幅 0.1）が混ざれば 1.0 を下回る。週末の2日は閉場で数えない
    assert f.adr == pytest.approx(1.0, abs=1e-6)
    assert f.adr_days == 8


def test_misaligned_and_closed_market_rows_are_not_bars(cfg):
    """**配信元の「最後の気配」を足にしない。**

    22:59:00 のような行が末尾に付いていて、前日の高値と安値が同じ値に
    なった。夏時間の金曜 21:00 始まりの足（閉場後）も、新しい1日になった。
    """
    friday = datetime(2026, 9, 25, 0, 0, tzinfo=UTC)
    m15 = candles(friday, 15, 84, base=158.0, step=-0.02)   # 21:00 の手前まで
    junk = [
        Candle(timestamp=datetime(2026, 9, 25, 21, 0, tzinfo=UTC),
               open=157.2, high=157.2, low=157.2, close=157.2, volume=0),
        Candle(timestamp=datetime(2026, 9, 25, 22, 59, tzinfo=UTC),
               open=157.2, high=157.2, low=157.2, close=157.2, volume=0),
    ]
    sunday = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    f = B.facts_for(cfg, "USDJPY", [], m15 + junk, sunday)
    assert f.market_open is False
    assert f.today_high != f.today_low, "今日の高値と安値が同じになっています"
    assert f.day_note and "閉まっている" in f.day_note


def test_round_numbers_bracket_the_price(cfg):
    now = datetime(2026, 9, 22, 5, 0, tzinfo=UTC)
    m15 = candles(now - timedelta(hours=3), 15, 12, base=158.26)
    f = B.facts_for(cfg, "USDJPY", [], m15, now)
    assert f.round_below <= f.price < f.round_above
    assert f.round_above - f.round_below == pytest.approx(0.5)


# ------------------------------------------------ 要因


def test_fundamentals_are_never_invented(cfg):
    """**機械は作らない。** 無ければ「未入力」で、判定に数えない。"""
    now = datetime(2026, 9, 27, tzinfo=UTC)
    f = B.fundamentals_factor(cfg, "EURUSD", now, views={})
    assert f.view == B.NONE
    assert "未入力" in f.text


def test_old_fundamentals_are_shown_but_not_counted(cfg):
    """**古い見立てで一致・対立を判断しない。** 見せるが、数えない。"""
    now = datetime(2026, 9, 27, tzinfo=UTC)
    views = {"USDJPY": {"view": "down", "note": "x", "source": "s",
                        "as_of": "2026-09-01"}}
    f = B.fundamentals_factor(cfg, "USDJPY", now, views=views)
    assert f.stale is True
    assert f.view == B.NONE
    fresh = {"USDJPY": {**views["USDJPY"], "as_of": "2026-09-26"}}
    assert B.fundamentals_factor(cfg, "USDJPY", now, views=fresh).view == B.DOWN


# ------------------------------------------------ 判定


def _analysis(cfg, **over):
    a = analyze_pair("USDJPY", MockMarketDataProvider(), cfg)
    return a.model_copy(update={"filter_blocked": False,
                                "invalidation_reasons": [], **over})


def _facts(price=150.0, used=0.5, room_up=2.0, room_down=2.0):
    f = B.Facts(price=price, used_ratio=used, room_up_adr=room_up,
                room_down_adr=room_down)
    f.market_open = True
    return f


def _open_now():
    return datetime(2026, 9, 22, 5, 0, tzinfo=UTC)          # 火曜 14:00 JST


def test_technicals_against_fundamentals_is_excluded(cfg):
    """テクニカル▼ × ファンダ▲ は対立。**除外する。**"""
    factors = [B.Factor("trend", "トレンド", B.DOWN),
               B.Factor("momentum", "モメンタム", B.FLAT),
               B.Factor("fundamentals", "ファンダ", B.UP)]
    v = B.verdict_for(cfg, "GBPJPY", _analysis(cfg), _facts(), factors,
                      _open_now())
    assert v["state"] == "excluded"
    assert any("対立" in x for x in v["exclude"])


def test_aligned_factors_are_described_not_recommended(cfg):
    """**そろっても「発注」とは書かない。** 決めるのは使う人。"""
    factors = [B.Factor("trend", "トレンド", B.DOWN),
               B.Factor("momentum", "モメンタム", B.DOWN),
               B.Factor("fundamentals", "ファンダ", B.DOWN)]
    v = B.verdict_for(cfg, "GBPUSD", _analysis(cfg), _facts(), factors,
                      _open_now())
    assert v["state"] == "aligned_down"
    text = v["label"] + v["note"]
    for word in ("発注", "買い推奨", "売り推奨", "エントリー"):
        assert word not in text
    assert "決めるのはご自身" in v["note"]


def test_a_score_note_or_expiry_is_not_an_exclusion(cfg):
    """**点数の説明や期限切れを「除外の理由」にしない。**

    期限切れの判断は hard_blocked が立ち、「様子見の水準です」のような
    説明まで除外として出ていた。強制条件で止まったものだけを数える。
    """
    a = _analysis(cfg, hard_blocked=True, filter_blocked=False,
                  invalidation_reasons=["場面は整いつつありますが、まだ様子見の水準です",
                                        "この判断は有効期限が切れています（作り直しが必要）"])
    factors = [B.Factor("trend", "トレンド", B.FLAT)]
    v = B.verdict_for(cfg, "USDJPY", a, _facts(), factors, _open_now())
    assert v["exclude"] == []


def test_a_hard_filter_is_an_exclusion(cfg):
    a = _analysis(cfg, filter_blocked=True,
                  invalidation_reasons=["H1 と H4 の向きが逆です（上位足と下位足が競合）"])
    v = B.verdict_for(cfg, "USDJPY", a, _facts(), [], _open_now())
    assert v["state"] == "excluded"
    assert any("H1 と H4" in x for x in v["exclude"])


def test_the_intervention_zone_excludes_usdjpy(cfg):
    """手で入れた警戒帯の中なら除外する。**根拠も一緒に出す。**"""
    v = B.verdict_for(cfg, "USDJPY", _analysis(cfg), _facts(price=159.0), [],
                      _open_now())
    assert v["state"] == "excluded"
    assert any("介入警戒帯" in x for x in v["exclude"])


def test_a_day_that_already_ran_is_excluded(cfg):
    """今日すでに普段の1.2倍を超えて動いた日は、追いかけになりやすい。"""
    v = B.verdict_for(cfg, "GBPJPY", _analysis(cfg), _facts(used=1.4), [],
                      _open_now())
    assert any("動き切った" in x for x in v["exclude"])


def test_a_closed_market_does_not_talk_about_time(cfg):
    """**閉まっている日は、刻限や今日の値幅で除外しない。** 終わった日の話。"""
    f = _facts(used=1.4)
    f.market_open = False
    sunday = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    v = B.verdict_for(cfg, "GBPJPY", _analysis(cfg), f, [], sunday)
    assert not any("動き切った" in x or "刻限" in x for x in v["exclude"])
    assert any("閉まっています" in x for x in v["cautions"])


# ------------------------------------------------ 記録と採点


def _board(pair="USDJPY", at=None, open_=True, lean="down", state="aligned_down"):
    at = at or _open_now()
    return {
        "pair": pair, "generated_at": at.isoformat(),
        "facts": {"price": 150.0, "adr": 1.0, "used_ratio": 0.3,
                  "position": 0.5, "market_open": open_},
        "factors": [{"key": "trend", "view": "down"}],
        "verdict": {"state": state, "lean": lean, "exclude": [], "cautions": []},
    }


@pytest.fixture
def url(tmp_path):
    return f"sqlite:///{(tmp_path / 'boards.sqlite3').as_posix()}"


def test_only_the_first_board_of_the_day_is_kept(url):
    """**朝に見たものが、その日の判断の材料。** 後から上書きしない。"""
    t = _open_now()
    assert board_log.record(_board(at=t), database_url=url) is True
    assert board_log.record(_board(at=t + timedelta(hours=3), lean="up",
                                   state="aligned_up"),
                            database_url=url) is False
    rows = board_log.recent(url)
    assert len(rows) == 1
    assert rows[0]["lean"] == "down"


def test_a_closed_market_board_is_not_kept(url):
    assert board_log.record(_board(open_=False), database_url=url) is False
    assert board_log.recent(url) == []


def test_a_mock_market_board_is_not_kept(url):
    """**作り物の値動きを採点しない。**"""
    assert board_log.record(_board(), database_url=url, provider="mock") is False
    assert board_log.recent(url) == []


def test_tests_do_not_write_to_the_real_database():
    from app.config import get_settings

    assert "database/fx.sqlite3" not in get_settings().resolved_database_url


def test_scoring_only_uses_bars_before_the_deadline():
    """**刻限より後の足は使わない。** 持ち越さない前提なので。"""
    start = datetime(2026, 9, 22, 5, 0, tzinfo=UTC)
    deadline = start + timedelta(hours=4)
    row = {"recorded_at": start.isoformat(), "deadline": deadline.isoformat(),
           "adr": 1.0, "price": 150.0, "lean": "down"}
    # 刻限までは下げ、刻限の後に大きく上げる
    before = candles(start + timedelta(minutes=15), 15, 14, base=149.9,
                     step=-0.02)
    after = candles(deadline, 15, 8, base=152.0)
    got = board_log.score_one(row, before + after,
                              now=deadline + timedelta(hours=5))
    assert got is not None
    assert got["move_adr"] > 0, "下にそろった日に下げたのに、負になっています"
    assert got["close_at_deadline"] < 150.0


def test_scoring_waits_for_the_deadline():
    start = datetime(2026, 9, 22, 5, 0, tzinfo=UTC)
    row = {"recorded_at": start.isoformat(),
           "deadline": (start + timedelta(hours=4)).isoformat(),
           "adr": 1.0, "price": 150.0, "lean": "down"}
    got = board_log.score_one(row, candles(start, 15, 30),
                              now=start + timedelta(hours=1))
    assert got is None


# ------------------------------------------------ API


def test_the_board_api_never_says_order(cfg):
    from fastapi.testclient import TestClient

    from app.main import app

    r = TestClient(app).get("/api/board")
    assert r.status_code == 200
    for b in r.json()["boards"]:
        text = b["verdict"]["label"] + b["verdict"]["note"]
        assert "発注" not in text
        assert {f["key"] for f in b["factors"]} == {
            "trend", "momentum", "fundamentals"}


def test_fundamentals_need_a_source_and_are_dated_today(tmp_path,
                                                        monkeypatch):
    """**誰の見立てか分からないものは受け付けない。** 日付はこちらで付ける。"""
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(B, "FUNDAMENTALS_PATH", tmp_path / "f.json")
    c = TestClient(app)
    assert c.post("/api/fundamentals/USDJPY",
                  json={"view": "up", "source": ""}).status_code == 422
    assert c.post("/api/fundamentals/USDJPY",
                  json={"view": "buy", "source": "x"}).status_code == 422
    r = c.post("/api/fundamentals/USDJPY",
               json={"view": "down", "note": "テスト", "source": "自分の見立て"})
    assert r.status_code == 200
    saved = r.json()["saved"]
    assert saved["view"] == "down"
    assert saved["as_of"] == datetime.now(JST).date().isoformat()   # 日本時間の日付
    assert saved["saved_at"]
    assert B.load_fundamentals(tmp_path / "f.json")["USDJPY"]["source"] == "自分の見立て"
