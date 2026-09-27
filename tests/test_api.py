# -*- coding: utf-8 -*-
"""API の試験。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_required_fields():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    for key in ("status", "version", "database", "market_data",
                "provider_state", "timestamp"):
        assert key in body, f"{key} がありません"
    assert body["status"] == "ok"


def test_pairs_lists_all_configured_pairs():
    r = client.get("/api/pairs")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 26
    assert len(body["pairs"]) == 26
    assert {p["symbol"] for p in body["pairs"]} >= {"USDJPY", "EURUSD", "ZARJPY"}


def test_pair_detail_and_unknown_pair():
    assert client.get("/api/pairs/USDJPY").status_code == 200
    assert client.get("/api/pairs/XXXYYY").status_code == 404


def test_analysis_returns_full_schema():
    r = client.get("/api/analysis/USDJPY")
    assert r.status_code == 200
    body = r.json()
    for key in ("pair", "timestamp", "direction", "signal", "score",
                "score_breakdown", "regime", "regime_score", "h1_bias",
                "m15_setup", "m5_context", "market_structure", "spread",
                "data_quality", "warnings", "reasons", "invalidation_reasons"):
        assert key in body, f"{key} がありません"
    assert body["signal"] in ("BUY", "SELL", "WAIT", "NO_TRADE")
    assert 0 <= body["score"] <= 100


def test_analysis_reasons_are_concrete_not_vague():
    """「AIが上がると考えている」のような文言を出さない。"""
    body = client.get("/api/analysis/USDJPY").json()
    text = " ".join(body["reasons"] + body["invalidation_reasons"])
    for banned in ("AI", "必ず", "確実", "保証", "絶対"):
        assert banned not in text, f"根拠のない言い回しが含まれています: {banned}"


def test_analysis_unknown_pair_is_404():
    assert client.get("/api/analysis/XXXYYY").status_code == 404


def test_ranking_returns_all_pairs_sorted():
    r = client.get("/api/ranking")
    assert r.status_code == 200
    body = r.json()
    assert body["analyzed"] == 26
    ranks = [e["rank"] for e in body["entries"]]
    assert ranks == sorted(ranks)
    assert "version" in body


def test_ranking_limit_works():
    body = client.get("/api/ranking?limit=5").json()
    assert len(body["entries"]) == 5


def test_provider_status_endpoint():
    body = client.get("/api/provider/status").json()
    assert body["name"] == "mock"
    assert body["state"] == "OK"


def test_dev_load_csv_rejects_missing_file():
    r = client.post("/api/dev/load-csv",
                    json={"pair": "USDJPY", "timeframe": "M30", "limit": 10})
    # 置き場に無ければ 400。存在すれば 200。どちらでも 500 にはしない。
    assert r.status_code in (200, 400)


def test_no_endpoint_returns_500_for_known_pairs():
    """どの銘柄でも 500 を返さない。壊れていても NO_TRADE で返す。"""
    for pair in ("USDJPY", "ZARJPY", "GBPNZD"):
        assert client.get(f"/api/analysis/{pair}").status_code == 200


def test_the_strategies_endpoint_lists_what_the_engine_actually_has():
    """**画面が戦略の名前や説明を自前で持たないようにする。**

    画面側に日本語名を書くと、engine 側と食い違ったときに気づけない。
    名前・説明・相場つきへの割り当ては、すべてここから返す。
    """
    from app.strategies import REGISTRY

    r = client.get("/api/strategies")
    assert r.status_code == 200
    body = r.json()
    got = {s["name"]: s for s in body["strategies"]}
    assert set(got) == set(REGISTRY), "一覧と REGISTRY が食い違っています"
    for name, st in REGISTRY.items():
        assert got[name]["label"] == st.label
        assert got[name]["scoring"] == st.scoring
    # 割り当てには試す順番が入っている（同じ相場つきで先後が分かること）
    assert any("(" in r_ for s in body["strategies"] for r_ in s["regimes"])
    # **「実装した」と「効くと分かった」を混ぜない。**
    assert "勝つ材料は出ていません" in body["measured"]


def test_the_analysis_says_which_strategy_decided_the_direction():
    """どの戦略で入ったかを返すこと。混ぜて集計しないために要る。"""
    from app.strategies import REGISTRY

    r = client.get("/api/analysis/USDJPY")
    assert r.status_code == 200
    body = r.json()
    assert "strategy" in body
    if body["strategy"] is not None:
        assert body["strategy"] in REGISTRY


def test_the_ranking_carries_the_strategy_too():
    r = client.get("/api/ranking?limit=5")
    assert r.status_code == 200
    for e in r.json()["entries"]:
        assert "strategy" in e


def test_candles_returns_everything_the_chart_draws():
    """**画面が指標を計算し直さなくて済むだけの値を返すこと。**

    画面側で計算すると、同じ指標の実装が2つになって静かに食い違う。
    十字線に出す ATR・ADX まで含めて、engine の値をそのまま渡す。
    """
    r = client.get("/api/candles/USDJPY?timeframe=M15&limit=250")
    assert r.status_code == 200
    body = r.json()
    n = len(body["candles"])
    assert n == 250
    for key in ("ema20", "ema50", "ema200", "bb_upper", "bb_lower",
                "support", "resistance"):
        assert key in body["overlays"], f"overlays に {key} がありません"
        assert len(body["overlays"][key]) == n, f"{key} の長さが足と違います"
    for key in ("rsi14", "macd", "macd_signal", "macd_hist"):
        assert key in body["sub"], f"sub に {key} がありません"
        assert len(body["sub"][key]) == n
    for key in ("atr14", "adx14"):
        assert key in body["readout"], f"readout に {key} がありません"
        assert len(body["readout"][key]) == n
    # 250本あれば EMA200 は末尾で値を持つ。**項目だけあって空にしない。**
    assert body["overlays"]["ema200"][-1] is not None


def test_data_status_tells_you_before_everything_stops():
    """**取り込みが古くなったことを、画面から分かるようにする。**

    予定表は36時間で古くなり、そこから先は全銘柄が見送りになる。理由は
    各銘柄の詳細の奥にしか出ないので、「昨日まで動いていたのに今日は
    全部 NO_TRADE」という壊れ方に見えていた。
    """
    r = client.get("/api/data-status")
    assert r.status_code == 200
    body = r.json()
    keys = {s["key"] for s in body["sources"]}
    assert keys == {"bars", "calendar", "drivers", "swap"}
    for s in body["sources"]:
        assert s["state"] in ("OK", "SOON", "STALE", "MISSING")
        assert s["refresh"], f"{s['key']}: 取り込み直す方法が空です"
    # 全体の状態は**いちばん悪いもの**に合わせる。良いほうに寄せない。
    order = {"OK": 0, "MISSING": 1, "SOON": 2, "STALE": 3}
    assert body["state"] == max(
        (s["state"] for s in body["sources"]), key=lambda x: order[x])
    # 建てられなくなる材料だけが blocking に入る
    cal = next(s for s in body["sources"] if s["key"] == "calendar")
    assert cal["blocks_trading"] is True
    assert ("calendar" in body["blocking"]) == (cal["state"] == "STALE")


# ============ この先の幅（向きの予想ではない）


def test_the_forward_band_is_only_served_for_the_timeframe_it_was_measured_on():
    """**M15 で測った倍率を H4 に当てない。**

    当てれば数字は出るが、それは測っていないものを測ったふりで出すこと
    になる。測っていない時間足では出さない。
    """
    from app.config import get_trading_config

    cfg = get_trading_config()
    measured = (cfg.forecast or {}).get("timeframe")
    assert measured, "config/forecast.json が測られていません"

    ok = client.get(f"/api/candles/USDJPY?timeframe={measured}&limit=250")
    assert ok.status_code == 200
    assert ok.json()["forecast"] is not None

    other = "H4" if measured != "H4" else "H1"
    off = client.get(f"/api/candles/USDJPY?timeframe={other}&limit=250")
    assert off.status_code == 200
    assert off.json()["forecast"] is None, (
        f"{other} は測っていないのに幅が返っています")


def test_the_forward_band_reports_the_coverage_it_actually_measured():
    """**「8割のはず」ではなく「測ったら◯%だった」を出す。**

    倍率を決めた期間と、覆い率を確かめた期間は別。ここが崩れると、
    当たって当然の数字を根拠として出すことになる。
    """
    from app.config import get_trading_config

    cfg = get_trading_config()
    fc = cfg.forecast or {}
    target = float(fc.get("quantile", 0.8))
    body = client.get(
        f"/api/candles/USDJPY?timeframe={fc['timeframe']}&limit=250").json()
    band = body["forecast"]
    assert band["levels"], "段が空です"
    for level in band["levels"]:
        rate = level["measured_coverage"]
        assert rate is not None, f"{level['bars']}本先の覆い率が空です"
        assert level["checked_on"] and level["checked_on"] > 1000, (
            "確かめた件数が少なすぎます")
        # 目標から大きく外れていたら、測り直しが要る合図として落とす
        assert abs(rate - target) < 0.1, (
            f"{level['bars']}本先の覆い率 {rate:.3f} が目標 {target} から"
            f"離れています。測り直してください")


def test_the_forward_band_widens_with_the_horizon():
    """先を見るほど幅は広がる。**狭まることはない。**"""
    from app.config import get_trading_config

    cfg = get_trading_config()
    band = client.get(
        f"/api/candles/USDJPY?timeframe={cfg.forecast['timeframe']}&limit=250"
    ).json()["forecast"]
    widths = [(l["high"] - l["low"], l["bars"]) for l in band["levels"]]
    assert widths == sorted(widths), "先の段のほうが狭くなっています"
    assert all(w > 0 for w, _ in widths)


def test_a_missing_measurement_means_no_band_rather_than_a_guess():
    """測っていなければ**描かない**。既定値で埋めない。"""
    from app.main import _forecast_band

    class Cfg:
        forecast: dict = {}

    assert _forecast_band(Cfg(), "M15", 150.0, 0.2) is None
    Cfg.forecast = {"timeframe": "M15", "horizons": {}}
    assert _forecast_band(Cfg(), "M15", 150.0, 0.2) is None


def test_data_status_says_whether_the_market_is_open():
    """**閉まっているときに「作り直せ」と言わないため。**

    作り直しても新しい気配値は出てこない。出せない理由が違うので、
    画面が言い分けられるように状態を返す。
    """
    r = client.get("/api/data-status")
    assert r.status_code == 200
    assert isinstance(r.json()["market_open"], bool)


def test_the_morning_brief_can_be_read_as_text():
    r = client.get("/api/morning-brief?format=text")
    assert r.status_code == 200
    assert "FX 朝の確認" in r.text
    # **見通しは書かない。**
    for word in ("買い目線", "売り目線"):
        assert word not in r.text
