# -*- coding: utf-8 -*-
"""取引時間中に取り込み直す銘柄を、カンマ区切りで1行に出す（refresh_intraday.ps1 が使う）。

- 既定（core）… 円建ての3本 ＋ 非円建ての選択（いまの時間帯の。まだ無ければ前の選択）
  ＋ その円換算に要る銘柄（USDJPY など）。**毎回取り込む。**
- ``--extra`` … いまの時間帯の選択がまだ無い時だけ、残りの候補とその円換算。選ぶには
  候補の8割以上の足が新しい必要があるため。**無ければ空行。**

2つに分けるのは、候補をまとめて取り込んで Yahoo の制限（429）に掛かった時に、
いつもの銘柄まで古くならないようにするため（2026-10-10 に実際に掛かった）。
**選ぶことはしない**（選ぶのは公開ページ作り）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import daily_pick                                     # noqa: E402
from app.config import get_trading_config                      # noqa: E402


def _saved() -> dict:
    try:
        return json.loads(daily_pick.PICK_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def core(cfg, saved: dict) -> list:
    out = list(daily_pick.settings(cfg)["jpy"])
    picked = [s for s in saved.get("pairs") or [] if isinstance(s, str)]
    for s in picked + daily_pick.conversion_symbols(picked):
        if s not in out:
            out.append(s)
    return out


def extra(cfg, saved: dict, now: datetime) -> list:
    if saved.get("key") == daily_pick.slot_key(cfg, now):
        return []
    have = set(core(cfg, saved))
    cands = daily_pick.candidates(cfg)
    out = []
    for s in cands + daily_pick.conversion_symbols(cands):
        if s not in have and s not in out:
            out.append(s)
    return out


if __name__ == "__main__":
    cfg = get_trading_config()
    saved = _saved()
    if "--extra" in sys.argv[1:]:
        print(",".join(extra(cfg, saved, datetime.now(timezone.utc))))
    else:
        print(",".join(core(cfg, saved)))
