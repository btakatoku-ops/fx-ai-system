# -*- coding: utf-8 -*-
"""FXモーニングブリーフの取り込み。

1. **形が合わなければ何も取り込まない。** 推測で埋めない。
2. **日付はブリーフに書いてある日付。** 一覧の順番や取り込んだ日ではない。
3. **手で入れた見立てを、それより古いブリーフで上書きしない。**
4. **ブリーフの判定はアプリの除外の理由にしない。** 気をつけることに添えるだけ。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import board as B
from app import board_log
from app import brief_import as bi

ROOT = Path(__file__).resolve().parents[1]
JST = bi.JST


def _row(pair, vclass, vtext, sigs, summary="要約"):
    cells = "".join(
        f'<div><div class="k">{k}</div><div class="s {c}">{m}</div>'
        f'<div class="d">{d}</div></div>' for k, c, m, d in sigs)
    return (f'<div class="db-row"><div class="db-head"><span class="db-pair">{pair}</span>'
            f'<span class="verdict {vclass}">{vtext}</span><span>{summary}</span></div>'
            f'<div class="sig">{cells}</div></div>')


def brief_html(day="2026年9月24日（木）　08:00 JST", rows=None):
    rows = rows if rows is not None else [
        _row("GBP/USD", "v-dn", "▼ 下方向（発注）", [
            ("トレンド", "dn", "▼", "下降チャネル"),
            ("モメンタム", "dn", "▼", "RSI 50未満"),
            ("ファンダ", "dn", "▼", "Fedタカ派でドル高"),
            ("レンジ位置", "dn", "▼", "破断支持の下")]),
        _row("GBP/JPY", "v-cf", "■ 対立 → 除外", [
            ("トレンド", "dn", "▼", "209を試す"),
            ("モメンタム", "dn", "▼", "軟調"),
            ("ファンダ", "up", "▲", "金利差・円安は買い方向"),
            ("レンジ位置", "dn", "▼", "下端寄り")]),
    ]
    return (f'<html><body><header><div class="mast-date">{day}</div></header>'
            f'<section><div class="db">{"".join(rows)}</div></section></body></html>')


# ------------------------------------------------ 読み取り


def test_the_direction_board_is_read():
    d = bi.parse(brief_html())
    assert d["brief_at"] == "2026-09-24T08:00:00+09:00"
    g = d["pairs"]["GBPJPY"]
    assert g["lean"] == "conflict" and g["excluded"] is True
    assert g["signals"]["fundamentals"] == {
        "view": "up", "mark": "▲", "text": "金利差・円安は買い方向"}
    u = d["pairs"]["GBPUSD"]
    assert u["lean"] == "down" and u["excluded"] is False
    assert set(u["signals"]) == {"trend", "momentum", "fundamentals", "range"}


def test_a_brief_without_the_board_imports_nothing():
    """**形式の違う版は取り込まない。** 9/17 の版には方向性ボードが無い。"""
    with pytest.raises(bi.BriefFormatError):
        bi.parse(brief_html(rows=[]))
    with pytest.raises(bi.BriefFormatError):
        bi.parse("<div>方向性ボード</div>")                   # 日付も無い


def test_a_signal_whose_mark_and_colour_disagree_is_dropped():
    """色は上なのに印が▼、のようなものは**推測で決めない。**"""
    rows = [_row("USD/JPY", "v-fl", "■ 中立", [
        ("トレンド", "up", "▼", "食い違い"),
        ("ファンダ", "fl", "■", "介入警戒で相殺")])]
    s = bi.parse(brief_html(rows=rows))["pairs"]["USDJPY"]["signals"]
    assert "trend" not in s
    assert s["fundamentals"]["view"] == "neutral"


def test_the_newest_brief_is_chosen_by_its_own_date():
    """一覧の順番や更新日は、ブリーフの日付と一致しないことがある。"""
    old = bi.parse(brief_html("2026年9月17日（木）　08:20 JST"))
    new = bi.parse(brief_html("2026年9月24日（木）　08:00 JST"))
    assert bi.pick_newest([new, old])["brief_date"] == "2026-09-24"
    assert bi.pick_newest([old, new])["brief_date"] == "2026-09-24"


# ------------------------------------------------ 見立てへの反映


def test_brief_fundamentals_carry_their_source_and_date():
    views: dict = {}
    doc = bi.parse(brief_html())
    got = bi.merge_fundamentals(views, doc, "https://claude.ai/artifact/x",
                                ["GBPUSD", "GBPJPY"])
    assert "取り込みました" in got["GBPJPY"]
    v = views["GBPJPY"]
    assert v["view"] == "up" and v["as_of"] == "2026-09-24"
    assert v["source"].startswith("FXモーニングブリーフ 09/24 08:00")
    assert v["url"] == "https://claude.ai/artifact/x"


def test_a_newer_manual_view_is_kept():
    """**ブリーフより後に手で入れた見立てを消さない。**"""
    doc = bi.parse(brief_html())
    manual = {"view": "down", "note": "自分の見立て", "source": "自分",
              "as_of": "2026-09-24",
              "saved_at": datetime(2026, 9, 24, 0, 30, tzinfo=timezone.utc).isoformat()}
    views = {"GBPJPY": dict(manual)}
    got = bi.merge_fundamentals(views, doc, "", ["GBPJPY", "GBPUSD"])
    assert views["GBPJPY"]["view"] == "down"
    assert "新しい" in got["GBPJPY"]


def test_an_older_manual_view_is_replaced():
    doc = bi.parse(brief_html())
    views = {"GBPJPY": {"view": "down", "source": "自分", "as_of": "2026-09-20",
                        "saved_at": "2026-09-20T00:00:00+00:00"}}
    bi.merge_fundamentals(views, doc, "", ["GBPJPY"])
    assert views["GBPJPY"]["view"] == "up"


def test_pairs_the_app_does_not_know_are_skipped():
    views: dict = {}
    got = bi.merge_fundamentals(views, bi.parse(brief_html()), "", ["GBPJPY"])
    assert "GBPUSD" not in views
    assert "対象外" in got["GBPUSD"]


# ------------------------------------------------ スクリプト


def _run(tmp_path, html, *extra):
    sys.path.insert(0, str(ROOT / "scripts"))
    import import_brief

    src = tmp_path / "brief.html"
    src.write_text(html, encoding="utf-8")
    return import_brief.main([str(src), "--url", "https://claude.ai/artifact/x",
                              "--fundamentals", str(tmp_path / "f.json"),
                              "--briefs-dir", str(tmp_path / "briefs"), *extra])


def test_an_old_brief_is_not_used_as_today(tmp_path):
    """**古いブリーフを今日の見立てとして使わない。**"""
    assert _run(tmp_path, brief_html("2020年1月6日（月）　08:00 JST")) == 2
    assert not (tmp_path / "f.json").exists()


def test_todays_brief_is_imported(tmp_path):
    today = datetime.now(JST)
    html = brief_html(f"{today.year}年{today.month}月{today.day}日（月）　08:00 JST")
    assert _run(tmp_path, html) == 0
    views = json.loads((tmp_path / "f.json").read_text(encoding="utf-8"))["views"]
    assert views["GBPJPY"]["view"] == "up"
    latest = json.loads((tmp_path / "briefs" / "latest.json").read_text(encoding="utf-8"))
    assert latest["url"] == "https://claude.ai/artifact/x"
    assert (tmp_path / "briefs" / f"{today.date().isoformat()}.json").exists()


def test_a_broken_brief_leaves_files_untouched(tmp_path):
    (tmp_path / "f.json").write_text('{"views": {"X": 1}}', encoding="utf-8")
    assert _run(tmp_path, "<html>形が違う</html>", "--allow-old") == 2
    assert json.loads((tmp_path / "f.json").read_text(encoding="utf-8")) == {"views": {"X": 1}}


# ------------------------------------------------ ボードに添える


def _doc(date="2026-09-24"):
    d = bi.parse(brief_html())
    d["brief_date"] = date
    return d


def test_the_brief_is_dated_relative_to_today():
    now = datetime(2026, 9, 24, 3, 0, tzinfo=timezone.utc)      # 12:00 JST
    assert bi.for_pair(_doc(), "GBPJPY", now)["age_days"] == 0
    assert bi.for_pair(_doc(), "GBPJPY", now + timedelta(days=3))["age_days"] == 3
    assert bi.for_pair(_doc(), "EURUSD", now) is None


def test_a_brief_exclusion_is_a_caution_not_an_exclusion():
    """**ブリーフの判定はまだ測れていない。除外の理由にはしない。**"""
    v = {"lean": None, "exclude": [], "cautions": []}
    B._brief_cautions(v, {"age_days": 0, "lean": "conflict", "excluded": True})
    assert v["exclude"] == []
    assert any("ブリーフでは除外" in c for c in v["cautions"])


def test_an_opposite_brief_is_pointed_out():
    v = {"lean": "up", "exclude": [], "cautions": []}
    B._brief_cautions(v, {"age_days": 0, "lean": "down", "excluded": False})
    assert any("向きが逆" in c for c in v["cautions"])


def test_an_old_brief_adds_nothing():
    v = {"lean": "up", "exclude": [], "cautions": []}
    B._brief_cautions(v, {"age_days": 2, "lean": "down", "excluded": True})
    assert v["cautions"] == []


# ------------------------------------------------ 記録


def _board(brief):
    return {
        "pair": "GBPJPY",
        "generated_at": datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc).isoformat(),
        "facts": {"price": 209.5, "adr": 1.6, "market_open": True,
                  "price_at": datetime(2026, 9, 23, 23, 45, tzinfo=timezone.utc).isoformat()},
        "factors": [], "brief": brief,
        "verdict": {"state": "excluded", "lean": None, "exclude": ["x"], "cautions": []},
    }


def test_the_brief_lean_is_recorded_only_when_fresh(tmp_path):
    url = f"sqlite:///{(tmp_path / 'b.sqlite3').as_posix()}"
    board_log.record(_board({"age_days": 0, "brief_date": "2026-09-24",
                             "lean": "conflict", "excluded": True}), database_url=url)
    row = board_log.recent(url)[0]
    assert row["brief_lean"] == "conflict" and row["brief_excluded"] == 1

    url2 = f"sqlite:///{(tmp_path / 'c.sqlite3').as_posix()}"
    board_log.record(_board({"age_days": 3, "brief_date": "2026-09-21",
                             "lean": "down", "excluded": False}), database_url=url2)
    assert board_log.recent(url2)[0]["brief_lean"] is None


def test_an_existing_boards_table_gets_the_new_columns(tmp_path):
    """**前からある表の中身は消さずに、列だけ足す。**"""
    db = tmp_path / "old.sqlite3"
    # ブリーフの列を足す前の形
    old = "\n".join(line for line in board_log._SCHEMA.splitlines()
                    if "brief_" not in line)
    con = sqlite3.connect(db)
    con.executescript(old)
    con.execute("INSERT INTO boards (pair, board_day, recorded_at, provider, state) "
                "VALUES ('USDJPY', 'd', 'r', 'csv', 'mixed')")
    con.commit()
    con.close()
    rows = board_log.recent(f"sqlite:///{db.as_posix()}")
    assert len(rows) == 1 and "brief_lean" in rows[0]
