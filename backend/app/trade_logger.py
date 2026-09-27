# -*- coding: utf-8 -*-
"""画面に出た判断を残す。

**なぜ要るか。** これまで優位性を測れていたのは過去データの再現だけで、
**実際に画面へ出た判断を後から検証する手段が無かった**。良くなったかどうか
すら測れない、という状態だった。ここが埋まらないと改善のしようがない。

## 何を残すか

``analyze()`` が返したものをそのまま残す。**あとで作り直さない。**
作り直すと、設定を変えた後に「当時どう判断したか」が変わってしまう。

## いつ残すか

**同じ判断を何度も残さない。** 画面は30秒ごとに読み直すので、そのまま
書くと1日に数万行たまり、しかも中身はほとんど同じになる。

残すのは**判断が変わったとき**だけ。同じ銘柄で、シグナル・向き・区分・
相場つき・戦略・強制条件のどれかが変われば新しい行にする。変わって
いなければ、最後の行の ``last_seen`` と回数だけ更新する。

## 何を残さないか

**発注はしない。建玉も持たない。** ここに残るのは「こう判断した」という
記録だけで、実際に建てたかどうかは分からない。取り違えないよう、表の
名前も ``decisions``（判断）にしてある。

## 結果の突き合わせ

``outcome`` 系の列は最初は空で、``scripts/score_journal.py`` が後から
埋める。**判断した瞬間には結果は分からない。** 空のまま置いておき、
時間が経ってから、その時刻より後の足だけを使って埋める。
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

TABLE = "decisions"

# 判断が「同じ」かどうかを決める列。ここが変わったら新しい行にする。
#
# **足の時刻は入れない。** 入れると、同じ BUY が10本続いただけで10行に
# なる。26銘柄 × 1日96本で、何も変わっていない日でも数千行たまる。
# 見たいのは「いつ判断が変わったか」なので、変わった時だけ1行にする。
FINGERPRINT = ("signal", "direction", "quality", "regime", "strategy",
               "hard_blocked")

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pair          TEXT    NOT NULL,
    decided_at    TEXT    NOT NULL,
    last_seen     TEXT    NOT NULL,
    seen_count    INTEGER NOT NULL DEFAULT 1,
    provider      TEXT    NOT NULL,
    signal        TEXT    NOT NULL,
    direction     TEXT    NOT NULL,
    quality       TEXT    NOT NULL,
    score         REAL    NOT NULL,
    regime        TEXT    NOT NULL,
    strategy      TEXT,
    hard_blocked  INTEGER NOT NULL DEFAULT 0,
    data_quality  TEXT,
    valid_until   TEXT,
    spread_pips   REAL,
    atr           REAL,
    entry_low     REAL,
    entry_high    REAL,
    stop          REAL,
    target        REAL,
    target_r      REAL,
    reason        TEXT,
    detail        TEXT,
    outcome       TEXT,
    outcome_at    TEXT,
    r_multiple    REAL,
    scored_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_pair ON {TABLE}(pair, decided_at);
CREATE INDEX IF NOT EXISTS idx_decisions_open ON {TABLE}(outcome, decided_at);
"""


def init(engine: Engine) -> None:
    """表を用意する。**何度呼んでも壊れない。**"""
    with engine.begin() as conn:
        for stmt in _SCHEMA.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return None


def _row_from(result, plan, provider: str) -> Dict[str, Any]:
    """``AnalysisResult``（と計画）から、残す1行を作る。"""
    m15 = (result.indicators or {}).get("M15")
    entry_low = entry_high = stop = target = target_r = None
    if plan is not None and getattr(plan, "ok", False):
        entry_low, entry_high = plan.entry_low, plan.entry_high
        stop = plan.stop
        if plan.targets:
            target = plan.targets[0].price
            target_r = plan.targets[0].r_multiple

    return {
        "pair": result.pair,
        "provider": provider,
        "signal": result.signal.value,
        "direction": result.direction.value,
        "quality": result.quality.value,
        "score": float(result.score),
        "regime": result.regime.value,
        "strategy": result.strategy,
        "hard_blocked": 1 if result.hard_blocked else 0,
        "data_quality": result.data_quality.value,
        "valid_until": _iso(result.valid_until),
        "spread_pips": (round(result.spread.spread_pips, 3)
                        if result.spread else None),
        "atr": getattr(m15, "atr14", None) if m15 else None,
        "entry_low": entry_low, "entry_high": entry_high,
        "stop": stop, "target": target, "target_r": target_r,
        # 先頭の理由だけ列に出す。全文は detail に入れる。
        "reason": (result.invalidation_reasons or result.reasons or [None])[0],
        "detail": json.dumps({
            "reasons": (result.reasons or [])[:6],
            "invalidation": (result.invalidation_reasons or [])[:6],
            "breakdown": result.score_breakdown.model_dump(),
        }, ensure_ascii=False),
    }


def record(result, plan=None, *, database_url: str,
           provider: str = "unknown",
           now: Optional[datetime] = None) -> Optional[int]:
    """1件の判断を残す。同じ判断が続いているなら回数だけ増やす。

    戻り値は新しく作った行の id。作らなかった（同じ判断の続き）ときは
    ``None``。**残せなくても分析は止めない。** 記録のために判断が
    出せなくなるのは本末転倒なので、失敗は警告にとどめる。
    """
    stamp = _iso(now or datetime.now(timezone.utc))
    row = _row_from(result, plan, provider)
    try:
        engine = get_engine(database_url)
        init(engine)
        with engine.begin() as conn:
            last = conn.execute(
                text(f"SELECT * FROM {TABLE} WHERE pair = :pair "
                     f"ORDER BY decided_at DESC, id DESC LIMIT 1"),
                {"pair": row["pair"]},
            ).mappings().first()

            if last is not None and all(
                    (last[k] if k != "hard_blocked" else int(last[k]))
                    == row[k] for k in FINGERPRINT):
                conn.execute(
                    text(f"UPDATE {TABLE} SET last_seen = :seen, "
                         f"seen_count = seen_count + 1 WHERE id = :id"),
                    {"seen": stamp, "id": last["id"]})
                return None

            cols = list(row) + ["decided_at", "last_seen"]
            values = dict(row, decided_at=stamp, last_seen=stamp)
            conn.execute(
                text(f"INSERT INTO {TABLE} ({', '.join(cols)}) "
                     f"VALUES ({', '.join(':' + c for c in cols)})"),
                values)
            new_id = conn.execute(
                text("SELECT last_insert_rowid()")).scalar()
        return int(new_id) if new_id is not None else None
    except Exception as exc:                     # noqa: BLE001
        log.warning("判断を記録できません %s: %s", row.get("pair"), exc)
        return None


def recent(database_url: str, limit: int = 50,
           pair: Optional[str] = None) -> List[Dict[str, Any]]:
    """新しいものから順に読む。"""
    try:
        engine = get_engine(database_url)
        init(engine)
        where = "WHERE pair = :pair" if pair else ""
        with engine.connect() as conn:
            rows = conn.execute(
                text(f"SELECT * FROM {TABLE} {where} "
                     f"ORDER BY decided_at DESC, id DESC LIMIT :limit"),
                {"limit": int(limit), **({"pair": pair.upper()} if pair else {})},
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:                     # noqa: BLE001
        log.warning("判断の記録を読めません: %s", exc)
        return []


def summary(database_url: str) -> Dict[str, Any]:
    """どれだけ残っているか。**結果が出ている件数は別に数える。**"""
    try:
        engine = get_engine(database_url)
        init(engine)
        with engine.connect() as conn:
            total = conn.execute(
                text(f"SELECT COUNT(*) FROM {TABLE}")).scalar() or 0
            actionable = conn.execute(
                text(f"SELECT COUNT(*) FROM {TABLE} "
                     f"WHERE signal IN ('BUY','SELL')")).scalar() or 0
            scored = conn.execute(
                text(f"SELECT COUNT(*) FROM {TABLE} "
                     f"WHERE outcome IS NOT NULL")).scalar() or 0
            first = conn.execute(
                text(f"SELECT MIN(decided_at) FROM {TABLE}")).scalar()
            last = conn.execute(
                text(f"SELECT MAX(last_seen) FROM {TABLE}")).scalar()
            by_signal = {
                r["signal"]: r["n"] for r in conn.execute(
                    text(f"SELECT signal, COUNT(*) AS n FROM {TABLE} "
                         f"GROUP BY signal")).mappings()
            }
        return {
            "total": int(total), "actionable": int(actionable),
            "scored": int(scored), "first_at": first, "last_at": last,
            "by_signal": by_signal,
            # **「勝率」をここで出さない。** 結果を突き合わせるのは
            # scripts/score_journal.py の仕事で、まだ動かしていないかも
            # しれない。件数だけ出して、判断は読む側に委ねる。
            "note": ("残っているのは「こう判断した」という記録だけです。"
                     "実際に建てたかどうかは含みません。"),
        }
    except Exception as exc:                     # noqa: BLE001
        log.warning("判断の記録をまとめられません: %s", exc)
        return {"total": 0, "actionable": 0, "scored": 0,
                "first_at": None, "last_at": None, "by_signal": {},
                "note": f"読めません: {exc}"}

def plan_for(result, cfg, provider):
    """記録用に、そのときの Entry / SL / TP を作る。

    **後から結果と突き合わせるには、そのとき出していた値が要る。**
    ここで作り損ねると、記録はできても採点できない（実際そうなっていた。
    呼び出し側が build_plan を違う引数で呼んでいて、例外を握り潰していた）。

    BUY / SELL 以外では作らない。失敗しても ``None`` を返し、記録自体は
    続ける。**ただし黙って捨てず、警告に残す。**
    """
    if result.signal.value not in ("BUY", "SELL"):
        return None
    try:
        from .entry_engine import collect_rates
        from .trade_plan import build_plan

        spec = cfg.pair(result.pair)
        rates, missing = collect_rates(
            spec, provider, cfg, cfg.account.get("quote_currency", "JPY"))
        if missing:
            log.warning("換算レートが足りず、記録用の計画を作れません %s: %s",
                        result.pair, ",".join(missing))
            return None
        return build_plan(result, spec, cfg, rates)
    except Exception as exc:                     # noqa: BLE001
        log.warning("記録用の計画を作れません %s: %s", result.pair, exc)
        return None
