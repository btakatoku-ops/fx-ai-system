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
    """**生きた画面ではない。** いつ時点の写しかを一番上に出し、古さを数える。"""
    html = P.render([board()], AT)
    assert "2026/09/28 09:00 時点の写し" in html
    assert 'data-generated="2026-09-28T00:00:00+00:00"' in html
    assert 'data-valid="20"' in html


def test_the_page_is_not_indexed_and_loads_nothing_from_outside():
    """スクリプトはページの中に書いたものだけ。外から何も読み込まない。"""
    html = P.render([board()], AT, snapshots={"GBPJPY": {"pair": "GBPJPY"}})
    assert 'content="noindex,nofollow"' in html
    assert " src=" not in html and "http://" not in html
    assert "https://" not in html


def test_the_plan_calculator_is_inside_the_page():
    """**PC のサーバーに頼らず、ページだけで計画を出す。**"""
    html = P.render([board()], AT, snapshots={"GBPJPY": {"pair": "GBPJPY", "x": "</script>"}})
    assert 'class="plan" data-pair="GBPJPY"' in html
    assert "window.PlanCalc" in html and "window.SNAPS=" in html
    assert "</script>\"" not in html and "<\/script>" in html   # JSON の中の閉じタグは無害化
    assert P.render([board()], AT).count('class="plan"') == 0      # 写しが無ければ欄も出さない


def test_the_brief_link_and_quote_are_not_published():
    """ブリーフ本体へのリンクと、ブリーフの文面の引用は載せない。"""
    html = P.render([board()], AT)
    assert "claude.ai" not in html and "SECRET" not in html
    assert "発注しない" not in html


def test_the_page_never_tells_you_to_trade():
    html = P.render([board()], AT)
    body = html.replace("このページから発注はできません", "").replace("発注はしません", "")
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


# ------------------------------------------------ ダッシュボードの欄（2026-10-09）


def extras():
    return {
        "charts": {"GBPJPY": {
            "day_start": "2026-09-27T21:00:00+00:00", "deadline": "2026-09-28T16:00:00+00:00",
            "open": 208.4, "adr": 1.6, "last_bar_at": "2026-09-27T23:45:00+00:00",
            "bars": [[0, 208.5, 208.3, 208.4], [15, 208.6, 208.35, 208.55], [30, 208.7, 208.5, 208.6]]}},
        "tape": [{"recorded_at": "2026-09-27T23:42:00+00:00", "session": "tokyo", "pair": "GBPJPY",
                  "state": "excluded", "lean": None, "brief_lean": "conflict", "price": 208.4,
                  "scored": True, "move": -0.21},
                 {"recorded_at": "2026-09-28T07:00:00+00:00", "session": "london", "pair": "GBPJPY",
                  "state": "aligned_up", "lean": "up", "brief_lean": "up", "price": 208.6,
                  "scored": False, "move": None}],
        "score": {"recorded": 2, "scored": 1, "aligned": {"n": 3, "hits": 1, "rate": None},
                  "brief": {"n": 0, "hits": 0, "rate": None}, "excluded": 1,
                  "excluded_swing": 0.58, "min_samples": 30},
        "events": [{"at": "2026-09-28T14:00:00+00:00", "title": "<b>CPI</b>",
                    "currency": "USD", "impact": "High"}],
    }


def test_the_dashboard_panels_show_real_data():
    html = P.render([board()], AT, extras=extras())
    for title in ("SIGNAL MATRIX", "RANGE", "BOARD TAPE", "SCORE", "FEED", "EVENTS"):
        assert title in html, title
    assert '<svg class="chart"' in html and "<polyline" in html     # 実際の値動き
    assert "待ち" in html and "-0.21" in html                        # 採点済みと未採点
    assert "30件まで割合は出しません" in html                          # 件数が少ないうちは割合を出さない
    assert "&lt;b&gt;CPI&lt;/b&gt;" in html                            # 指標名もエスケープ


def test_the_dashboard_still_loads_nothing_from_outside():
    html = P.render([board()], AT, extras=extras(), snapshots={"GBPJPY": {"pair": "GBPJPY"}})
    assert "http://" not in html and "https://" not in html and " src=" not in html


BAND = {"close": 208.6, "atr": 0.12, "quantile": 0.8, "coverage_min": 0.8017,
        "coverage_max": 0.8069, "measured_at": "2026-09-18T00:11:09+09:00",
        "levels": [{"bars": b, "low": 208.6 - k * 0.12, "high": 208.6 + k * 0.12, "coverage": 0.80}
                   for b, k in ((1, 0.667), (2, 0.928), (4, 1.302), (8, 1.838), (16, 2.609), (32, 3.949))]}


def test_the_band_is_the_measured_range_not_direction():
    """**帯は実データで測った幅だけ。** 測った覆い率を書く。上下どちらに動くかは示さない。"""
    ex = extras()
    ex["charts"]["GBPJPY"]["band"] = BAND
    html = P.render([board()], AT, extras=ex)
    assert 'class="cone"' in html
    assert "80〜81% が収まった範囲" in html and "向きは示さない" in html


def test_no_measurement_means_no_band():
    """**測っていなければ描かない。** 推測の形（始値からの √時間）は使わない。"""
    html = P.render([board()], AT, extras=extras())       # band 無し
    assert 'class="cone"' not in html
    assert "測っていないので描きません" in html


def test_the_band_stops_at_the_measured_horizon_and_the_deadline():
    """最後の足から 32本（8時間）より先と、刻限より先は描かない。"""
    import re

    ex = extras()
    ch = ex["charts"]["GBPJPY"]
    ch["band"] = BAND
    ch["deadline"] = "2026-09-28T01:00:00+00:00"            # 取引日の始まりから4時間
    html = P.render([board()], AT, extras=ex)
    pts = re.search(r'<polygon class="cone" points="([^"]+)"', html).group(1).split()
    xs = [float(p.split(",")[0]) for p in pts]
    dl = float(re.search(r'<line class="dl" x1="([0-9.]+)"', html).group(1))
    assert max(xs) <= dl + 0.01                             # 刻限を越えない


def test_forecast_band_uses_the_measured_multiples(cfg, mock_provider):
    from app import dashboard_data as dd

    bars = mock_provider.get_candles("USDJPY", "M15", limit=200).candles
    b = dd.forecast_band(cfg, bars)
    assert b is not None
    k32 = cfg.forecast["horizons"]["32"]["atr_multiple"]
    top = [lv for lv in b["levels"] if lv["bars"] == 32][0]
    assert abs((top["high"] - b["close"]) - k32 * b["atr"]) < 1e-9
    assert b["coverage_min"] >= 0.79                        # 測った覆い率をそのまま持つ


def test_forecast_band_refuses_other_timeframes(cfg, mock_provider):
    """M15 で測った倍率を他の足に当てない（測っていないものを測ったふりで出さない）。"""
    from types import SimpleNamespace

    from app import dashboard_data as dd

    bars = mock_provider.get_candles("USDJPY", "M15", limit=200).candles
    other = SimpleNamespace(forecast={**cfg.forecast, "timeframe": "H1"})
    assert dd.forecast_band(other, bars) is None


def test_the_page_works_without_extras_or_chart():
    html = P.render([board()], AT)
    assert "値動きの足がありません" in html and "GBPJPY" in html


def test_balance_and_risk_can_be_entered_and_stay_on_the_device():
    """資金とリスクは入れられる。**この端末にだけ残し、送らない**（ページは誰でも見られる）。"""
    snap = {"pair": "GBPJPY", "account": {"balance": "1000000", "risk_pct": "1.0", "max_risk_pct": "2.0"}}
    html = P.render([board()], AT, snapshots={"GBPJPY": snap})
    assert 'id="acct-balance"' in html and 'id="acct-risk"' in html
    assert "どこにも送りません" in html and "上限は 2.0%" in html
    assert "localStorage" in html and "fetch(" not in html and "XMLHttpRequest" not in html


# ------------------------------------------------ 比べられる欄にする（2026-10-09）


def test_the_tape_shows_the_session_segment_and_the_deadline_separately():
    """**記録の「その後」は2つ。** その時間帯の終わりまでと、刻限まで。混ぜない。"""
    ex = extras()
    ex["tape"][0]["seg_move"] = 0.12
    ex["tape"][1]["seg_move"] = None
    html = P.render([board()], AT, extras=ex)
    assert "<th>区切りまで</th><th>刻限まで</th>" in html
    assert "+0.12" in html and "→ロンドン" in html and "→NY" in html
    assert "-0.21" in html


def test_seg_move_uses_the_same_sign_as_move():
    from app import dashboard_data as D
    r = {"lean": "down", "move_adr": 0.3, "seg_move_adr": 0.1}
    assert D._move(r) == -0.3 and D._move(r, "seg_move_adr") == -0.1
    assert D._move({"lean": "up", "seg_move_adr": 0.1}, "seg_move_adr") == 0.1
    assert D._move({"lean": "up"}, "seg_move_adr") is None


def test_the_range_column_says_it_is_not_compared():
    html = P.render([board()], AT, extras=extras())
    assert "レンジは アプリ＝20日の位置％ ／ ブリーフ＝向き で、比べていない" in html


def test_a_watch_pair_is_marked_and_gets_no_plan():
    """様子を見る銘柄は並べるが、前には出さない（計画のボタンを出さない）。"""
    w = board()
    w["pair"], w["tier"] = "EURJPY", "watch"
    html = P.render([board(), w], AT, extras=extras(),
                    snapshots={"GBPJPY": {"pair": "GBPJPY", "account": {}}})
    assert "様子を見る・計画なし" in html
    assert 'data-pair="GBPJPY"' in html and 'data-pair="EURJPY"' not in html


def test_the_page_lists_focus_then_watch(cfg):
    from app import dashboard_page, focus as F
    focus, watch = dashboard_page.targets(cfg)
    assert not set(focus) & set(watch)
    assert watch == [s for s in F.symbols(cfg, "watch") if s not in focus]


# ------------------------------------------------ チャート（ローソク足・価格の目盛り）


def test_the_chart_is_candles_with_a_price_axis_and_no_text_inside_the_svg():
    """**SVG は引き伸ばすので、文字を入れない**（携帯で歪む）。文字は HTML で重ねる。"""
    import re

    ex = extras()
    ex["charts"]["GBPJPY"]["bars"] = [[0, 208.5, 208.3, 208.45, 208.4],
                                      [15, 208.6, 208.35, 208.4, 208.55]]
    html = P.render([board()], AT, extras=ex)
    svg = re.search(r'<svg class="chart".*?</svg>', html, re.S).group(0)
    assert "<text" not in svg
    assert 'class="bd u"' in svg and 'class="bd d"' in svg        # 陽線と陰線
    assert 'class="yax"' in html and 'class="last' in html        # 価格の目盛りと最後の値の札
    assert 'class="xax"' in html and "06:00" in html              # 時刻（日本時間）


def test_old_bars_without_the_open_still_draw():
    html = P.render([board()], AT, extras=extras())               # [分, 高, 安, 終] だけ
    assert 'class="bd' in html and "値動きの足がありません" not in html


def test_price_ticks_are_round_numbers():
    assert P._ticks(208.31, 209.07) == [208.4, 208.6, 208.8, 209.0]
    assert P._ticks(1.0, 1.0) == []
