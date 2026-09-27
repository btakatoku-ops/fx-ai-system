# -*- coding: utf-8 -*-
"""データベース。Phase 1 では健全性の確認にしか使わない。

売買記録や成績の保存は後の段階なので、置き場だけ用意して繋がない。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

# **接続先ごとに持つ。** 以前は1つだけ覚えていて、最初に呼ばれた接続先を
# それ以降ずっと返していた。別の置き場を指定しても最初のものに書き込まれる
# ので、試験が本番の DB を汚しうるし、設定を変えても効かない。
_engines: Dict[str, Engine] = {}


def get_engine(database_url: str) -> Engine:
    engine = _engines.get(database_url)
    if engine is None:
        if database_url.startswith("sqlite:///"):
            rel = database_url.replace("sqlite:///", "", 1)
            Path(rel).resolve().parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(database_url, future=True)
        _engines[database_url] = engine
    return engine


def check_health(database_url: str) -> str:
    """繋がるかだけ確かめる。失敗しても例外は外へ出さない。"""
    try:
        with get_engine(database_url).connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:
        log.warning("データベースに繋がりません: %s", exc)
        return f"error: {exc}"
