# -*- coding: utf-8 -*-
"""朝のボードを残して、後で採点する。

**ボードの方式が本当に役に立つかは、残して測らないと分からない。**
「材料が4つ下にそろった日は下がったのか」「除外した日は荒れた日だったのか」
を、過去データの再現ではなく、実際に出したボードで確かめる。

## 決まり

1. **1銘柄・1日・1時間帯に1行。最初に出したものを残す。** その時間帯の
   始まりに見たボードが判断の材料だったので。後から見直したボードで上書き
   しない。時間帯は東京（朝の処理）・ロンドン・ニューヨーク（2026-10-02 から。
   ``sessions.py``）。
2. **閉まっている日と、模擬の相場は残さない。** 終わった日や作り物の値動きを
   採点しても意味がない。
3. **採点は刻限を過ぎてから。** その日のうちに閉じる前提なので、刻限までの
   値動きだけで測る。刻限より後の足は使わない。採点は2つ。
   - **刻限まで**（``move_adr`` など）: 記録から刻限まで。これまでと同じ。
   - **その時間帯だけ**（``seg_*``）: 記録から次の時間帯の始まりまで。
     3つの時間帯が同じ値動きを数えないよう、区間を重ねない。
4. **建てたかどうかは知らない。** ここで測るのは「ボードがそう言った日に、
   相場はどう動いたか」だけ。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .db import get_engine

log = logging.getLogger(__name__)

TABLE = "boards"

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pair          TEXT    NOT NULL,
    board_day     TEXT    NOT NULL,
    recorded_at   TEXT    NOT NULL,
    provider      TEXT    NOT NULL,
    price         REAL,
    adr           REAL,
    used_ratio    REAL,
    position      REAL,
    trend         TEXT,
    momentum      TEXT,
    fundamentals  TEXT,
    state         TEXT    NOT NULL,
    lean          TEXT,
    exclude       TEXT,
    cautions      TEXT,
    deadline      TEXT,
    close_at_deadline REAL,
    move_adr      REAL,
    best_adr      REAL,
    worst_adr     REAL,
    scored_at     TEXT,
    brief_date    TEXT,
    brief_lean    TEXT,
    brief_excluded INTEGER,
    session       TEXT    NOT NULL DEFAULT 'tokyo',
    segment_end   TEXT,
    seg_close     REAL,
    seg_move_adr  REAL,
    seg_best_adr  REAL,
    seg_worst_adr REAL,
    UNIQUE(pair, board_day, session)
);
CREATE INDEX IF NOT EXISTS idx_boards_open ON {TABLE}(scored_at, board_day);
"""


# 後から足した列。**既にある表にも足す（中身は消さない）。**
_ADDED = {"brief_date": "TEXT", "brief_lean": "TEXT", "brief_excluded": "INTEGER"}


def _rebuild_with_sessions(conn) -> None:
    """時間帯の列が無い表を作り直す。**中身は消さない。**

    一意の条件（銘柄・日 → 銘柄・日・時間帯）は SQLite では後から変えられない
    ので、新しい表へ写して入れ替える。それまでの行はすべて朝の処理が残した
    ものなので、時間帯は東京にする。
    """
    old = [r[1] for r in conn.execute(text(f"PRAGMA table_info({TABLE})"))]
    conn.execute(text(f"ALTER TABLE {TABLE} RENAME TO {TABLE}_old"))
    conn.execute(text("DROP INDEX IF EXISTS idx_boards_open"))
    for stmt in _SCHEMA.strip().split(";"):
        if stmt.strip():
            conn.execute(text(stmt))
    cols = ", ".join(old)
    conn.execute(text(f"INSERT INTO {TABLE} ({cols}) SELECT {cols} FROM {TABLE}_old"))
    conn.execute(text(f"DROP TABLE {TABLE}_old"))
    log.info("ボードの記録に時間帯の列を足しました（%d 列を写しました）", len(old))


def init(engine: Engine) -> None:
    with engine.begin() as conn:
        for stmt in _SCHEMA.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        have = {r[1] for r in conn.execute(text(f"PRAGMA table_info({TABLE})"))}
        for col, typ in _ADDED.items():
            if col not in have:
                conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {col} {typ}"))
        if "session" not in have:
            _rebuild_with_sessions(conn)


def record(board: Dict[str, Any], *, database_url: str,
           provider: str = "unknown", session: str = "tokyo") -> bool:
    """1枚残す。**その日・その時間帯の最初の1枚だけ。** 残したら True。

    残せなくてもボードは止めない（記録のために画面が出なくなるのは本末転倒）。
    """
    from .sessions import SESSIONS

    if session not in SESSIONS:
        raise ValueError(f"知らない時間帯です: {session!r}")
    f, v = board.get("facts") or {}, board.get("verdict") or {}
    if not f.get("market_open", True) or f.get("price") is None:
        return False
    if provider == "mock":
        return False                              # 模擬の相場を採点しても意味がない
    from .board import fx_day_start

    now = datetime.fromisoformat(board["generated_at"])
    # **その日の足がまだ無いボードは残さない。** 月曜の朝、取り込み前に作ると
    # 金曜の終値が「今日のボード」として残り、1日1枚の決まりで上書きもされない
    # （2026-09-28 に実際に起きた）。
    try:
        price_at = datetime.fromisoformat(str(f.get("price_at")))
    except (TypeError, ValueError):
        return False
    if price_at < fx_day_start(now):
        log.info("その日の足がまだ無いので、ボードを残しません %s", board.get("pair"))
        return False
    views = {x["key"]: x["view"] for x in board.get("factors") or []}

    from .day_trade import exit_deadline
    from .config import get_trading_config

    from .sessions import segment_end

    cfg = get_trading_config()
    deadline = exit_deadline(cfg.filters, now)
    seg_end = segment_end(session, now, deadline, cfg.board)
    # その日のブリーフの判定も並べて残す（ブリーフの方向判定も後で採点する）。
    # **今日のブリーフでなければ残さない。**
    br = board.get("brief") or {}
    fresh = br.get("age_days") == 0
    row = {
        "pair": board["pair"],
        "board_day": fx_day_start(now).isoformat(),
        "session": session,
        "segment_end": seg_end.isoformat() if seg_end else None,
        "recorded_at": now.isoformat(),
        "provider": provider,
        "price": f.get("price"),
        "adr": f.get("adr"),
        "used_ratio": f.get("used_ratio"),
        "position": f.get("position"),
        "trend": views.get("trend"),
        "momentum": views.get("momentum"),
        "fundamentals": views.get("fundamentals"),
        "state": v.get("state", "unknown"),
        "lean": v.get("lean"),
        "exclude": json.dumps(v.get("exclude") or [], ensure_ascii=False),
        "cautions": json.dumps(v.get("cautions") or [], ensure_ascii=False),
        "deadline": deadline.isoformat() if deadline else None,
        "brief_date": br.get("brief_date") if fresh else None,
        "brief_lean": br.get("lean") if fresh else None,
        "brief_excluded": (1 if br.get("excluded") else 0) if fresh else None,
    }
    try:
        engine = get_engine(database_url)
        init(engine)
        cols = list(row)
        with engine.begin() as conn:
            got = conn.execute(
                text(f"INSERT OR IGNORE INTO {TABLE} ({', '.join(cols)}) "
                     f"VALUES ({', '.join(':' + c for c in cols)})"), row)
        return bool(got.rowcount)
    except Exception as exc:                     # noqa: BLE001
        log.warning("ボードを残せません %s: %s", board.get("pair"), exc)
        return False


def recent(database_url: str, limit: int = 50) -> List[Dict[str, Any]]:
    try:
        engine = get_engine(database_url)
        init(engine)
        with engine.connect() as conn:
            rows = conn.execute(
                text(f"SELECT * FROM {TABLE} ORDER BY board_day DESC, pair, "
                     f"recorded_at LIMIT :n"), {"n": int(limit)}).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:                     # noqa: BLE001
        log.warning("ボードの記録を読めません: %s", exc)
        return []


def score_one(row: Dict[str, Any], bars, now: Optional[datetime] = None
              ) -> Optional[Dict[str, Any]]:
    """1行を採点する。**刻限を過ぎていなければ採点しない。**

    使う足は「記録した時刻より後、刻限より前」だけ。値動きは普段の1日の
    値幅（ADR）で割って、銘柄の違いをならす。そろった向きがあれば、その
    向きを正にする（下にそろった日に下がれば正）。
    """
    now = now or datetime.now(timezone.utc)
    try:
        start = datetime.fromisoformat(str(row["recorded_at"]))
        deadline = datetime.fromisoformat(str(row["deadline"]))
    except (TypeError, ValueError):
        return None
    if deadline > now:
        return None
    got = _measure(row, bars, start, deadline)
    if got is None:
        return None
    close, move, best, worst = got
    out = {"close_at_deadline": close, "move_adr": move,
           "best_adr": best, "worst_adr": worst,
           "seg_close": None, "seg_move_adr": None,
           "seg_best_adr": None, "seg_worst_adr": None}
    seg = _segment_end_of(row, deadline)
    if seg is not None:
        got = _measure(row, bars, start, seg)
        if got is not None:
            (out["seg_close"], out["seg_move_adr"],
             out["seg_best_adr"], out["seg_worst_adr"]) = got
    return out


def _segment_end_of(row: Dict[str, Any], deadline: datetime) -> Optional[datetime]:
    """その時間帯の区間の終わり。**列が空の古い行は、記録の時刻から求め直す。**"""
    try:
        if row.get("segment_end"):
            return datetime.fromisoformat(str(row["segment_end"]))
        from .sessions import segment_end

        return segment_end(row.get("session") or "tokyo",
                           datetime.fromisoformat(str(row["recorded_at"])),
                           deadline)
    except (TypeError, ValueError):
        return None


def _measure(row: Dict[str, Any], bars, start: datetime, end: datetime):
    """``start`` より後、``end`` より前の足だけで測る。足りなければ None。"""
    adr, price = row.get("adr"), row.get("price")
    if not adr or adr <= 0 or price is None:
        return None
    window = [b for b in bars if start < b.timestamp < end]
    if len(window) < 4:
        return None                               # 足りなければ採点しない
    sign = 1.0 if row.get("lean") == "up" else -1.0 if row.get("lean") == "down" else 1.0
    close = float(window[-1].close)
    highs = [float(b.high) for b in window]
    lows = [float(b.low) for b in window]
    up_max = (max(highs) - price) / adr
    down_max = (price - min(lows)) / adr
    move = (close - price) / adr * sign
    best, worst = (up_max, -down_max) if sign > 0 else (down_max, -up_max)
    return close, round(move, 4), round(best, 4), round(worst, 4)


def needs_scoring(row: Dict[str, Any]) -> bool:
    """まだ採点していないか、時間帯の採点だけ抜けている行か。"""
    return not row.get("scored_at") or row.get("seg_move_adr") is None


def save_score(database_url: str, row_id: int, got: Dict[str, Any]) -> None:
    engine = get_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            text(f"UPDATE {TABLE} SET close_at_deadline = :close_at_deadline, "
                 f"move_adr = :move_adr, best_adr = :best_adr, "
                 f"worst_adr = :worst_adr, seg_close = :seg_close, "
                 f"seg_move_adr = :seg_move_adr, seg_best_adr = :seg_best_adr, "
                 f"seg_worst_adr = :seg_worst_adr, scored_at = :now WHERE id = :id"),
            {**got, "now": datetime.now(timezone.utc).isoformat(), "id": row_id})


def score_pending(database_url: str, provider) -> Dict[str, int]:
    """採点できる行をすべて採点する。返り値は件数。

    **時間帯の採点だけ抜けている行も、刻限までの採点ごとやり直す。** 同じ足で
    測るので、刻限までの数字は変わらない。足が取り込みの範囲（約60日）から
    外れた行は、時間帯の採点が空のまま残る。
    """
    from .market_data import MarketDataError

    bars: Dict[str, List] = {}
    scored = pending = 0
    for r in recent(database_url, limit=5000):
        if not needs_scoring(r):
            continue
        pair = r["pair"]
        if pair not in bars:
            try:
                bars[pair] = list(provider.get_candles(pair, "M15", limit=5000).candles)
            except MarketDataError:
                bars[pair] = []
        got = score_one(r, bars[pair])
        if got is None or (r.get("scored_at") and got["seg_move_adr"] is None):
            pending += 1
            continue
        save_score(database_url, r["id"], got)
        scored += 1
    return {"scored": scored, "pending": pending}
