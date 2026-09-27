# -*- coding: utf-8 -*-
"""支援する銘柄の絞り込みと、朝の確認。

ここで確かめること。

1. **測っていないものを「支援できる」と言わない。** 未測定は unknown で、
   既定で focus にしない。
2. **区分は費用だけで決まる。** 勝てるかどうかでは分けていない。
3. **使えない朝に「使えます」と書かない。** 予定表が古い、合成データ、
   といった理由があれば ready を落とす。
4. **今日の見通しを書かない。** 優位性は測れていない。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import focus
from app.analysis import analyze_pair
from app.market_data import MockMarketDataProvider
from app.morning_brief import as_text, build

UTC = timezone.utc


# ------------------------------------------------ 銘柄の絞り込み


def test_an_unmeasured_pair_is_not_treated_as_supported(cfg):
    """**測る前のものを「支援できる」と言わない。**"""
    empty = cfg.model_copy(update={"focus": {}})
    assert focus.measured(empty) is False
    assert focus.tier_of(empty, "USDJPY") == focus.UNKNOWN
    assert focus.symbols(empty, "focus") == []


def test_the_tiers_come_from_the_measurement_file(cfg):
    """区分は測った結果から引く。**コードに銘柄名を書かない。**"""
    if not focus.measured(cfg):
        pytest.skip("まだ測っていません（scripts/measure_pair_value.py）")
    for symbol, row in focus.table(cfg).items():
        assert focus.tier_of(cfg, symbol) == row["tier"]
        assert row["tier"] in ("focus", "watch", "off")
        # **費用だけで分けている。** 勝率などの列は入れない。
        assert "win_rate" not in row
        assert "expectancy" not in row


def test_a_supported_pair_actually_passes_the_cost_filter(cfg):
    """focus は「強制条件を普段は通る」銘柄であること。

    ここが崩れると、支援すると言いながら出るのは見送りばかりになる。
    """
    if not focus.measured(cfg):
        pytest.skip("まだ測っていません")
    threshold = (cfg.focus.get("thresholds") or {}).get("focus", 0.7)
    for symbol in focus.symbols(cfg, "focus"):
        row = focus.table(cfg)[symbol]
        assert row["pass_rate"] >= threshold, symbol
        assert row["ratio_median"] <= cfg.filters["spread"]["max_ratio_of_atr"]


def test_the_view_says_it_does_not_rank_by_profitability(cfg):
    got = focus.as_dict(cfg)
    assert "勝てるかどうかでは分けていません" in got["note"]
    assert set(got["counts"]) <= {"focus", "watch", "off", focus.UNKNOWN}


# ------------------------------------------------ 朝の確認


def _brief(cfg, provider_name="csv", url="sqlite:///:memory:", results=None):
    return build(cfg, provider_name, url, results=results or [],
                 now=datetime.now(UTC))


def test_synthetic_data_means_the_morning_is_not_ready(cfg):
    """**合成データで「今日は使えます」と書かない。**"""
    got = _brief(cfg, provider_name="mock")
    assert got["ready"] is False
    assert any("合成データ" in b for b in got["blockers"])
    assert got["todo"], "やることが空です"


def test_the_brief_never_writes_a_market_view(cfg):
    """**今日の見通しを書かない。** 優位性は測れていない。"""
    got = _brief(cfg)
    text = as_text(got)
    assert "見通しは書きません" in got["note"]
    for word in ("買い目線", "売り目線", "上昇しそう", "下落しそう"):
        assert word not in text


def test_the_brief_only_lists_supported_pairs(cfg):
    """並べるのは支援する銘柄だけ。**対象外を混ぜない。**"""
    if not focus.measured(cfg):
        pytest.skip("まだ測っていません")
    provider = MockMarketDataProvider()
    results = [analyze_pair(s.symbol, provider, cfg)
               for s in cfg.enabled_pairs()]
    got = _brief(cfg, results=results)
    listed = {r["pair"] for r in got["focus"]}
    assert listed == set(focus.symbols(cfg, "focus"))


def test_the_brief_reads_as_one_page(cfg):
    """端末に出す形が壊れていないこと。**見出しが揃っていること。**"""
    text = as_text(_brief(cfg))
    for head in ("FX 朝の確認", "支援する銘柄", "これからの指標",
                 "昨日からの判断の変化"):
        assert head in text, head


def test_a_stale_calendar_blocks_the_morning(cfg, tmp_path):
    """**予定表が古ければ、今日は使えないと書く。**

    予定表が古いだけで3日まるごと見送りになっていたのに、気づくには
    銘柄の詳細を開いて理由を読むしかなかった。
    """
    import json

    from app import news

    old = news.CALENDAR_PATH
    path = tmp_path / "events.json"
    stale = datetime.now(UTC) - timedelta(hours=200)
    path.write_text(json.dumps({
        "fetched_at": stale.isoformat(), "source": "tests",
        "events": [{"title": "x", "currency": "USD", "impact": "Low",
                    "at": stale.isoformat()}],
    }), encoding="utf-8")
    news.CALENDAR_PATH = path
    news.clear_cache()
    try:
        got = _brief(cfg)
        assert got["ready"] is False
        assert any("予定表" in b for b in got["blockers"]), got["blockers"]
    finally:
        news.CALENDAR_PATH = old
        news.clear_cache()


def test_driver_freshness_ignores_the_closed_market(cfg, tmp_path, monkeypatch):
    """**閉場していた時間を「古さ」に数えない。**

    相関の材料も相場のデータなので、市場が閉じているあいだは新しい足が
    出ない。壁時計で測ると、週末じゅう「材料が古い」と言い続けることに
    なる。足の鮮度は engine 側で同じ考え方にしてある。
    """
    from datetime import datetime, timedelta, timezone

    from app import data_status

    drivers = tmp_path / "data" / "drivers"
    drivers.mkdir(parents=True)
    # 金曜の閉場直前の足（2026-09-18 21:00 UTC）
    friday = datetime(2026, 9, 18, 21, 0, tzinfo=timezone.utc)
    for name in (cfg.correlation.get("drivers") or {}):
        path = drivers / f"{name}_{cfg.correlation['timeframe']}.csv"
        lines = ["timestamp,open,high,low,close,volume"]
        for i in range(5):
            t = friday - timedelta(hours=4 - i)
            lines.append(f"{t.isoformat()},100,100,100,100,0")
        path.write_text("\n".join(lines), encoding="utf-8")

    monkeypatch.setattr(data_status, "PROJECT_ROOT", tmp_path)
    # 日曜の昼。壁時計なら36時間前だが、開いていた時間は0。
    sunday = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)
    got = data_status.collect(cfg, "csv", now=sunday)
    src = next(s for s in got["sources"] if s["key"] == "drivers")
    assert src["age_hours"] == 0.0, src
    assert src["state"] == "OK"


def test_a_closed_market_is_said_out_loud(cfg):
    """**閉まっているときに候補が出ないのは異常ではない。**

    伝えないと、週末に開いた利用者が「壊れている」と読む。
    """
    from app.morning_brief import as_text, build

    # 日曜の昼（再開は日曜21:00 UTC）
    closed = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)
    got = build(cfg, "csv", "sqlite:///:memory:", results=[], now=closed)
    assert got["market_open"] is False
    assert "市場が閉まっています" in as_text(got)

    # 火曜の昼は開いている
    open_day = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    got2 = build(cfg, "csv", "sqlite:///:memory:", results=[], now=open_day)
    assert got2["market_open"] is True
    assert "市場が閉まっています" not in as_text(got2)


def test_pending_chores_are_shown_even_on_a_good_morning(cfg):
    """**止まっていないことと、手当てが要らないことは別。**

    以前は使えないときだけ「やること」を出していたので、スワップ表が
    10日古いのに「今日は使えます」の一言で終わっていた。
    """
    from app.morning_brief import as_text, build

    got = build(cfg, "csv", "sqlite:///:memory:", results=[],
                now=datetime(2026, 9, 22, 12, 0, tzinfo=UTC))
    text = as_text(got)
    if got["todo"]:
        assert "手当てが要るもの" in text or "やること" in text
        for cmd in got["todo"]:
            assert cmd in text, cmd


def test_a_source_gap_is_not_reported_as_a_missed_fetch(cfg, tmp_path,
                                                        monkeypatch):
    """**「取り込み忘れ」と「配信元に穴がある」を分ける。**

    どちらも「足が古い」に見えるが、やることが正反対になる。前者は
    取り込めば直り、後者は取り込んでも直らない。実際に 2026-09-23 に
    Yahoo の M5・M15・H1 が同じ時間帯（19:25〜23:00 UTC）を丸ごと欠き、
    取り込んだ直後なのに H1 が 4.7 時間前になった。
    """
    from datetime import datetime, timedelta, timezone

    from app import data_status
    from app.config import get_settings

    now = datetime(2026, 9, 23, 23, 45, tzinfo=timezone.utc)
    old_bar = now - timedelta(hours=5)

    entry_tf = cfg.backtest["entry_timeframe"]
    bias_tf = cfg.role_timeframe("bias")
    for symbol in ("USDJPY", "GBPJPY"):
        for tf in (entry_tf, bias_tf):
            path = tmp_path / f"{symbol}_{tf}.csv"
            lines = ["timestamp,open,high,low,close,volume"]
            for i in range(5):
                t = old_bar - timedelta(hours=4 - i)
                lines.append(f"{t.isoformat()},150,150,150,150,0")
            path.write_text("\n".join(lines), encoding="utf-8")

    settings = get_settings()
    monkeypatch.setattr(type(settings), "csv_dir",
                        property(lambda self: str(tmp_path)))

    got = data_status.collect(cfg, "csv", now=now)
    bars = next(s for s in got["sources"] if s["key"] == "bars")
    assert bars["state"] == "STALE"
    # いま書いたばかりのファイルなので、原因は取り込み忘れではない
    assert any("配信元" in n for n in bars["notes"]), bars["notes"]
    assert "直りません" in " ".join(bars["notes"])
