# -*- coding: utf-8 -*-
"""残したボードを採点して、方式が役に立っているかを見る。

確かめたいのは3つだけ。

1. **材料がそろった（上・下）ときは、その向きに動いたか。** コイン投げなら
   半分。半分を大きく超えなければ、そろったことに意味は無い。
2. **FXモーニングブリーフの方向判定は、その向きに動いたか。**
3. **除外したときは、荒れていたか。** 除外したときの値動きが、そろった
   ときと変わらないなら、除外は見送りの言い訳になっているだけかもしれない。

時間帯（東京・ロンドン・ニューヨーク）ごとに、重ならない区間で測る
（``backend/app/sessions.py``）。東京の1枚を刻限まで測った数字も、それまでと
比べられるよう並べる。

**件数が少ないうちは結論を出さない。** 30件に届かない区分は、勝率を名乗らない。
1週間ぶんだけ見るときは ``scripts/weekly_report.py``。

使い方::

    set FX_CSV_DATA_DIR=./data/real
    .venv/Scripts/python.exe scripts/score_boards.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import board_log                                     # noqa: E402
from app.board_report import summary                         # noqa: E402
from app.config import get_settings                           # noqa: E402
from app.market_data import CSVMarketDataProvider             # noqa: E402


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    settings = get_settings()
    url = settings.resolved_database_url

    if not board_log.recent(url, limit=1):
        print("残したボードがまだありません（朝・ロンドン・ニューヨークの始まりに"
              "1銘柄1枚ずつ溜まります）。")
        return 0

    got = board_log.score_pending(url, CSVMarketDataProvider(settings.csv_dir))
    print(f"今回採点 {got['scored']} 件 / まだ刻限前か足が足りない {got['pending']} 件\n")

    done = [r for r in board_log.recent(url, limit=5000) if r.get("scored_at")]
    if not done:
        print("採点済みのボードはまだありません。")
        return 0
    print("\n".join(summary(done)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
