# -*- coding: utf-8 -*-
"""FXモーニングブリーフを取り込む。

ブリーフは claude.ai のアーティファクトなので、このアプリからは直接読めない。
平日朝の定期タスク（Claude）がアーティファクトを読んで HTML を手元に保存し、
その場所をこのスクリプトに渡す。**読み取りそのものはここで機械的に行う。**
（Claude に要約させると、書いていないことが混ざりうるため）

使い方::

    .venv/Scripts/python.exe scripts/import_brief.py 保存したHTML [...] --url アーティファクトのURL

- HTML は何枚渡してもよい。**ブリーフに書かれた日付がいちばん新しいもの**を使う。
  （アーティファクトの一覧の順番や更新日は、ブリーフの日付と一致しないことがある）
- 今日（日本時間）のブリーフでなければ取り込まない（``--allow-old`` で許す）。
- 方向性ボードが無い版なら、何も取り込まずに終了コード 2。

取り込むもの:

- ``data/fundamentals.json`` … 銘柄ごとのファンダの見立て（出典・日付つき）。
  手で入れた見立ての方が新しければ残す。
- ``data/briefs/YYYY-MM-DD.json`` と ``latest.json`` … ブリーフの判定と4シグナル。
  ボードに「ブリーフではこう」と並べ、後で採点に使う。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import board as board_mod                            # noqa: E402
from app import brief_import as bi                            # noqa: E402
from app.config import get_trading_config                     # noqa: E402


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("html", nargs="+", help="保存したブリーフの HTML")
    ap.add_argument("--url", action="append", default=[],
                    help="アーティファクトの URL（HTML と同じ順に。省略可）")
    ap.add_argument("--allow-old", action="store_true",
                    help="今日のブリーフでなくても取り込む")
    ap.add_argument("--fundamentals", default=str(board_mod.FUNDAMENTALS_PATH))
    ap.add_argument("--briefs-dir", default=str(bi.BRIEFS_DIR))
    a = ap.parse_args(argv)

    docs: List[dict] = []
    for i, path in enumerate(a.html):
        url = a.url[i] if i < len(a.url) else ""
        try:
            doc = bi.parse(Path(path).read_text(encoding="utf-8"))
        except (OSError, bi.BriefFormatError) as exc:
            print(f"× {path}: {exc}")
            continue
        doc["url"] = url
        docs.append(doc)
        print(f"・{doc['brief_at'][:16]} のブリーフ（{len(doc['pairs'])} 銘柄）: {path}")

    doc = bi.pick_newest(docs)
    if doc is None:
        print("方向性ボードを読めたブリーフがありません。**何も取り込みません。**")
        return 2

    today = datetime.now(bi.JST).date().isoformat()
    if doc["brief_date"] != today and not a.allow_old:
        print(f"いちばん新しいブリーフが {doc['brief_date']} 付けで、今日（{today}）の"
              f"ものではありません。**古い見立てを今日のものとして使わないため、"
              f"取り込みません。**")
        return 2

    cfg = get_trading_config()
    known = [p.symbol for p in cfg.pairs]
    fpath = Path(a.fundamentals)
    import json
    data = (json.loads(fpath.read_text(encoding="utf-8"))
            if fpath.exists() else {"views": {}})
    views = data.setdefault("views", {})
    result = bi.merge_fundamentals(views, doc, doc.get("url", ""), known)
    bi.write_json(fpath, data)

    doc["imported_at"] = datetime.now(timezone.utc).isoformat()
    bdir = Path(a.briefs_dir)
    bi.write_json(bdir / f"{doc['brief_date']}.json", doc)
    bi.write_json(bdir / bi.LATEST, doc)

    print(f"\n{doc['brief_at'][:16]} のブリーフを取り込みました。")
    ja = {"trend": "トレンド", "momentum": "モメンタム",
          "fundamentals": "ファンダ", "range": "レンジ"}
    lean_ja = {"up": "▲上", "down": "▼下", "neutral": "■中立", "conflict": "■対立"}
    for sym, msg in result.items():
        p = doc["pairs"][sym]
        marks = " ".join(f"{ja.get(k, k)}{v['mark']}" for k, v in p["signals"].items())
        print(f"  {sym}: ファンダ {msg} ／ ブリーフの判定 {lean_ja.get(p['lean'], '—')}"
              f"{'（除外・見送り）' if p['excluded'] else ''} ／ {marks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
