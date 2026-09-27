# -*- coding: utf-8 -*-
"""足の検証・供給元・CSV 読み込みの試験。"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.market_data import (
    CSVMarketDataProvider,
    ExternalAPIProvider,
    FutureBrokerProvider,
    MarketDataError,
    MockMarketDataProvider,
    MT4MarketDataProvider,
)
from app.config import PROJECT_ROOT
from app.models import Candle, CandleSeries, ProviderState
from app.synthetic import broken_candle_rows, generate_series

# 同梱の CSV。起動位置に左右されないよう基点から解決する。
DATA_DIR = PROJECT_ROOT / "data"


# ---------------------------------------------------------------- 足の検証

@pytest.mark.parametrize("row", broken_candle_rows())
def test_broken_candles_are_rejected_not_repaired(row):
    """壊れた足は黙って直さず弾く。直すと後から追えなくなる。"""
    with pytest.raises(ValidationError):
        Candle(**row)


def test_valid_candle_is_accepted():
    c = Candle(timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
               open=150.0, high=151.0, low=149.0, close=150.5)
    assert c.high >= c.close >= c.low


def test_naive_timestamp_is_treated_as_utc():
    c = Candle(timestamp=datetime(2026, 1, 1), open=1, high=1, low=1, close=1)
    assert c.timestamp.tzinfo is timezone.utc


def test_series_rejects_unsorted_candles():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    a = Candle(timestamp=base, open=1, high=1, low=1, close=1)
    b = Candle(timestamp=base - timedelta(hours=1), open=1, high=1, low=1, close=1)
    with pytest.raises(ValidationError):
        CandleSeries(pair="USDJPY", timeframe="H1", candles=[a, b])


def test_series_rejects_duplicate_timestamps():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    a = Candle(timestamp=base, open=1, high=1, low=1, close=1)
    with pytest.raises(ValidationError):
        CandleSeries(pair="USDJPY", timeframe="H1", candles=[a, a])


# ---------------------------------------------------------------- 供給元

def test_mock_provider_is_deterministic():
    """同じ指定なら必ず同じ足。試験が揺れると原因を追えない。"""
    a = MockMarketDataProvider(scenario="range").get_candles("USDJPY", "H1", 200)
    b = MockMarketDataProvider(scenario="range").get_candles("USDJPY", "H1", 200)
    assert a.closes == b.closes


def test_mock_provider_states_it_is_synthetic():
    st = MockMarketDataProvider().provider_status()
    assert st.state is ProviderState.OK
    assert "合成" in st.detail


def test_mock_provider_rejects_unknown_timeframe():
    with pytest.raises(MarketDataError):
        MockMarketDataProvider().get_candles("USDJPY", "M3", 100)


def test_mock_provider_rejects_unknown_pair():
    with pytest.raises(KeyError):
        MockMarketDataProvider().get_candles("XXXYYY", "H1", 100)


def test_spread_is_positive():
    s = MockMarketDataProvider().get_spread("USDJPY")
    assert s.spread_price > 0
    assert s.spread_pips > 0


@pytest.mark.parametrize("provider_cls", [MT4MarketDataProvider,
                                          ExternalAPIProvider,
                                          FutureBrokerProvider])
def test_unimplemented_providers_raise_clearly(provider_cls):
    """繋いでいない供給元は、動くふりをせず失敗する。"""
    p = provider_cls()
    assert p.provider_status().state is ProviderState.NOT_IMPLEMENTED
    with pytest.raises(NotImplementedError):
        p.get_candles("USDJPY", "H1", 10)
    with pytest.raises(NotImplementedError):
        p.get_latest_price("USDJPY")


# ---------------------------------------------------------------- CSV

def _write_csv(path, rows, header="timestamp,open,high,low,close,volume"):
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")


def test_csv_loads_valid_rows(tmp_path):
    rows = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(10):
        t = (base + timedelta(hours=i)).isoformat()
        rows.append(f"{t},150.0,151.0,149.0,150.5,1000")
    _write_csv(tmp_path / "USDJPY_H1.csv", rows)
    series = CSVMarketDataProvider(tmp_path).get_candles("USDJPY", "H1", 100)
    assert len(series) == 10
    assert series.pair == "USDJPY"


def test_csv_skips_broken_rows_and_reports_them(tmp_path):
    """壊れた行は飛ばして記録する。黙って直さない。"""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [f"{(base + timedelta(hours=0)).isoformat()},150.0,151.0,149.0,150.5,1000",
            f"{(base + timedelta(hours=1)).isoformat()},150.0,149.0,151.0,150.5,1000",  # 高安が逆
            f"{(base + timedelta(hours=2)).isoformat()},150.0,151.0,149.0,150.5,1000"]
    _write_csv(tmp_path / "USDJPY_H1.csv", rows)
    p = CSVMarketDataProvider(tmp_path)
    series = p.get_candles("USDJPY", "H1", 100)
    assert len(series) == 2
    assert len(p.last_errors) == 1


def test_csv_missing_file_raises():
    """置き場にない足は、黙って空を返さず落とす。

    もとはここで USDJPY_M30 を指していたが、その CSV は実在するので
    「ファイルが無いとき」の試験になっていなかった。D1 は置いていない。
    """
    with pytest.raises(MarketDataError):
        CSVMarketDataProvider(DATA_DIR).get_candles("USDJPY", "D1", 10)


def test_csv_keeps_valid_rows_when_one_row_is_broken():
    """壊れた行は捨てて、残りは返す。捨てたことは last_errors に残す。"""
    p = CSVMarketDataProvider(DATA_DIR)
    series = p.get_candles("USDJPY", "M30", 10)
    assert len(series.candles) == 2          # 3行のうち high<low の1行を落とす
    assert p.provider_status().detail


def test_csv_path_traversal_is_blocked(tmp_path):
    """置き場の外は読ませない。"""
    p = CSVMarketDataProvider(tmp_path)
    with pytest.raises(KeyError):
        p.get_candles("../../etc/passwd", "H1", 10)


def test_csv_status_reports_missing_directory(tmp_path):
    st = CSVMarketDataProvider(tmp_path / "nope").provider_status()
    assert st.state is ProviderState.OFFLINE


def test_generate_series_rejects_unknown_scenario():
    with pytest.raises(ValueError):
        generate_series("USDJPY", "H1", 10, scenario="nonsense")


# ------------------------------------------------------------ 置き場の解決

def test_relative_paths_anchor_to_project_root():
    """相対指定は起動位置ではなくプロジェクト基点に付く。

    backend/ から uvicorn を起動すると backend/database/ と backend/logs/ が
    別にでき、同じ設定なのに中身が違う状態を実際に作ってしまった。
    CSV の置き場も同様で、backend/data を探して全銘柄が読めなくなる。
    """
    from app.config import Settings

    s = Settings()
    assert Path(s.csv_dir) == PROJECT_ROOT / "data"
    assert Path(s.log_dir) == PROJECT_ROOT / "logs"
    assert s.resolved_database_url.endswith("/database/fx.sqlite3")
    assert Path(s.resolved_database_url.replace("sqlite:///", "", 1)).is_absolute()


def test_absolute_and_memory_database_urls_are_untouched():
    from app.config import Settings

    assert Settings(database_url="sqlite:///:memory:").resolved_database_url == (
        "sqlite:///:memory:")
    other = "postgresql+psycopg://host/db"
    assert Settings(database_url=other).resolved_database_url == other


def test_dotenv_does_not_override_existing_environment(tmp_path, monkeypatch):
    """.env は環境にある値を上書きしない。

    本番で外から渡した設定を、置き忘れの .env が黙って差し替えるほうが危ない。
    """
    from app.config import load_dotenv

    env = tmp_path / ".env"
    env.write_text(
        '# 注記\nFX_ENV=fromfile\nFX_LOG_LEVEL="DEBUG"\n\nbroken line\n',
        encoding="utf-8")
    monkeypatch.setenv("FX_ENV", "fromshell")
    monkeypatch.delenv("FX_LOG_LEVEL", raising=False)

    load_dotenv(env)

    import os
    assert os.environ["FX_ENV"] == "fromshell"     # 環境が勝つ
    assert os.environ["FX_LOG_LEVEL"] == "DEBUG"   # 無い分だけ補う（引用符は外す）


def test_spread_table_uses_real_broker_values_not_a_flat_guess():
    """銘柄ごとに実勢のスプレッドを使うこと。

    **一律 1.2pips で置いてはいけない。** GBPNZD は実測10pips、USDJPY は
    0.9pips で10倍以上ちがう。一律に置くと、広い銘柄の成績を実際より
    ずっと良く見せてしまう。
    """
    from app.config import get_trading_config

    cfg = get_trading_config()
    usdjpy = cfg.spread_price("USDJPY") / cfg.pair("USDJPY").pip
    gbpnzd = cfg.spread_price("GBPNZD") / cfg.pair("GBPNZD").pip
    assert usdjpy == pytest.approx(0.9, abs=0.05)
    assert gbpnzd == pytest.approx(10.0, abs=0.05)
    assert gbpnzd > usdjpy * 5

    # 広がったときのほうが狭くなることはない
    for sym in ("USDJPY", "EURJPY", "GBPNZD"):
        assert cfg.spread_price(sym, wide=True) >= cfg.spread_price(sym)


def test_pairs_missing_from_the_spread_table_get_a_conservative_default():
    """表に無い銘柄は推測になる。**小さめではなく大きめに倒す。**"""
    from app.config import get_trading_config

    cfg = get_trading_config()
    for sym in ("TRYJPY", "MXNJPY"):
        pips = cfg.spread_price(sym) / cfg.pair(sym).pip
        assert pips == pytest.approx(cfg.spreads["default_pips"])
        assert pips >= 3.0


def test_csv_is_not_reparsed_when_nothing_changed(tmp_path, cfg):
    """**同じファイルを何度も読み直さない。**

    以前は呼ばれるたびに全行を読んでいた。1銘柄で4つの時間足、さらに
    気配値でもう1回。26銘柄を並べると130回ぶんになり、M5 は17,000行
    あるので一覧の表示に60秒かかっていた。実勢データに切り替えた瞬間に
    画面が使えなくなる、という形で出た。
    """
    from datetime import datetime, timedelta, timezone

    from app.market_data import CSVMarketDataProvider

    path = tmp_path / "USDJPY_M15.csv"
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    lines = ["timestamp,open,high,low,close,volume"]
    for i in range(300):
        t = base + timedelta(minutes=15 * i)
        lines.append(f"{t.isoformat()},150.0,150.1,149.9,150.05,0")
    path.write_text("\n".join(lines), encoding="utf-8")

    provider = CSVMarketDataProvider(tmp_path)
    reads = {"n": 0}
    original = path.open

    first = provider.get_candles("USDJPY", "M15", limit=100)
    import app.market_data as md

    before = len(md._CSV_CACHE)
    second = provider.get_candles("USDJPY", "M15", limit=100)
    assert len(md._CSV_CACHE) == before, "読むたびに覚え直しています"
    assert [c.timestamp for c in first.candles] == [
        c.timestamp for c in second.candles]


def test_csv_is_reread_after_the_file_changes(tmp_path):
    """**取り込み直したら読み直すこと。** 古い中身を返さない。"""
    from datetime import datetime, timedelta, timezone

    from app.market_data import CSVMarketDataProvider

    path = tmp_path / "USDJPY_M15.csv"
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)

    def write(n):
        lines = ["timestamp,open,high,low,close,volume"]
        for i in range(n):
            t = base + timedelta(minutes=15 * i)
            lines.append(f"{t.isoformat()},150.0,150.1,149.9,150.05,0")
        path.write_text("\n".join(lines), encoding="utf-8")

    provider = CSVMarketDataProvider(tmp_path)
    write(100)
    assert len(provider.get_candles("USDJPY", "M15", limit=500).candles) == 100
    write(150)
    assert len(provider.get_candles("USDJPY", "M15", limit=500).candles) == 150
