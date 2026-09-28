# -*- coding: utf-8 -*-
"""試験の共通設定。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_trading_config           # noqa: E402
from app.market_data import MockMarketDataProvider  # noqa: E402


@pytest.fixture(scope="session")
def cfg():
    return get_trading_config()


@pytest.fixture
def mock_provider():
    return MockMarketDataProvider()


@pytest.fixture
def data_dir():
    return ROOT / "data"

# ---------------------------------------------------------------- 記録簿

# **試験で本番の記録簿に書かない。**
#
# 判断の記録（decisions）は既定で有効なので、API を叩く試験が
# database/fx.sqlite3 に模擬データの判断を書き込んでいた（2026-09-27 に
# 165件を確認）。score_journal.py は provider=mock を分けて数えるので
# 採点は汚れていないが、記録簿そのものは使う人のもの。試験は一時の
# 記録簿を使う。既に入っている行は消さない。
@pytest.fixture(autouse=True, scope="session")
def _scratch_database(tmp_path_factory):
    import os

    from app.config import reset_config_cache

    db = tmp_path_factory.mktemp("db") / "test.sqlite3"
    old = os.environ.get("FX_DATABASE_URL")
    os.environ["FX_DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
    reset_config_cache()
    yield
    if old is None:
        os.environ.pop("FX_DATABASE_URL", None)
    else:
        os.environ["FX_DATABASE_URL"] = old
    reset_config_cache()


# ---------------------------------------------------------------- 手仕舞いの刻限

# **試験を、走らせた時刻に依存させない。**
#
# 刻限を 01:00 にしてから（2026-09-27）、23:30〜日替わりは「建てない」
# 時間帯になった。時刻を渡さずに分析する試験は実際の時計を読むので、
# 夜中に走らせると安全性の試験がいっせいに落ちた（実際に 5件落ちた）。
# 以前の 06:00 でも 04:30〜06:00 は同じことが起きえた。
#
# 刻限の規則は**止めない**。規則を見る時刻だけ、その日の 14:00（日本時間）
# に固定する。刻限そのものを確かめる試験（test_day_trade・test_board）は
# 自分で時刻を渡しているので、差し替えを受けない（test_plan_calc も同じ）。
_OWN_CLOCK = {"test_day_trade", "test_board", "test_plan_calc"}


@pytest.fixture(autouse=True)
def _midday_for_the_deadline(request, monkeypatch):
    if request.module.__name__.rsplit(".", 1)[-1] in _OWN_CLOCK:
        yield
        return
    from datetime import datetime, timezone

    from app import day_trade

    original = day_trade.can_open
    today = datetime.now(timezone.utc).date()
    midday = datetime(today.year, today.month, today.day, 5, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(day_trade, "can_open", lambda cfg, now: original(cfg, midday))
    yield


# ---------------------------------------------------------------- 予定表

# **試験を、置いてあるデータの古さに依存させない。**
#
# 経済指標の予定表は36時間で古くなり、古ければ全銘柄を見送る（これは
# 意図した安全側の動き）。ところがその規則のせいで、**しばらく取り込みを
# していないと、相場つきや戦略の試験までいっせいに落ちる**ようになって
# いた。実際に落ちた（2026-09-18、予定表が67時間前）。
#
# 落ちた試験は予定表の鮮度を確かめるものではない。engine の判断を
# 確かめるものなので、予定表は毎回その場で作って渡す。
#
# **鮮度の規則そのものは緩めていない。** それを確かめる試験
# （tests/test_news.py）は自前の予定表を明示的に渡しており、ここの
# 差し替えを受けない。
@pytest.fixture(autouse=True, scope="session")
def _fresh_calendar(tmp_path_factory):
    import json
    from datetime import datetime, timedelta, timezone

    from app import news

    now = datetime.now(timezone.utc)
    path = tmp_path_factory.mktemp("calendar") / "events.json"
    path.write_text(json.dumps({
        "fetched_at": now.isoformat(),
        "source": "tests",
        # 前後に広く置く。**収録範囲の外を「指標なし」と読まないため。**
        "events": [
            {"title": "試験用", "currency": "USD", "impact": "Low",
             "at": (now + timedelta(days=d)).isoformat()}
            for d in (-7, 7)
        ],
    }), encoding="utf-8")

    original = news.CALENDAR_PATH
    news.CALENDAR_PATH = path
    news.clear_cache()
    yield
    news.CALENDAR_PATH = original
    news.clear_cache()
