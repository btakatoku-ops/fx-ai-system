# -*- coding: utf-8 -*-
"""業者のスワップ表を CSV から取り込む。

**数字はこちらで作らない。** 業者の表を写したものだけを入れる。
スワップは毎日変わるうえ、買い持ちと売り持ちで額も符号も違う。
推測で埋めれば、損益の計算が静かに狂う。

CSV の形（``data/swap_template.csv`` を雛形に使う）::

    symbol,buy,sell
    USDJPY,251,-311
    EURJPY,-95,55

- ``buy``  : 買い持ちを1日持ち越したときの円（1Lot = 1万通貨）
- ``sell`` : 売り持ちを1日持ち越したときの円
- 空欄の行は飛ばす（分かる銘柄だけ入れればよい）

使い方::

    python scripts/import_swap.py --csv mine.csv \\
        --source "外為オンライン 店頭FX スワップポイント" --as-of 2026-09-12

``--source`` と ``--as-of`` は**必須**。出所と日付の無いスワップ表は、
数字が合っているかどうかを後から確かめられないので受け付けない。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

CONFIG = ROOT / "config" / "swap.json"

# 常識の範囲。1Lot1日で1万円動くスワップは無い。桁の打ち間違いを弾く。
SANE_LIMIT = 10000.0


def parse_rows(path: Path, known: set) -> Tuple[Dict[str, Dict[str, float]],
                                                List[str]]:
    """CSV を読む。おかしい行は**黙って直さず**、理由を集めて返す。"""
    table: Dict[str, Dict[str, float]] = {}
    problems: List[str] = []

    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = {"symbol", "buy", "sell"} - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(
                f"見出しが足りません: {', '.join(sorted(missing))}\n"
                f"  期待する形: symbol,buy,sell")

        for i, row in enumerate(reader, start=2):
            sym = (row.get("symbol") or "").strip().upper()
            if not sym:
                continue
            raw_buy = (row.get("buy") or "").strip()
            raw_sell = (row.get("sell") or "").strip()
            if not raw_buy and not raw_sell:
                continue                      # 未入力の行は飛ばす

            if sym not in known:
                problems.append(f"{i}行目: {sym} は設定にない銘柄です")
                continue

            vals: Dict[str, float] = {}
            bad = False
            for key, raw in (("buy", raw_buy), ("sell", raw_sell)):
                if not raw:
                    problems.append(f"{i}行目: {sym} の {key} が空です"
                                    f"（片側だけでは向きを判定できません）")
                    bad = True
                    continue
                try:
                    v = float(raw.replace(",", "").replace("円", ""))
                except ValueError:
                    problems.append(f"{i}行目: {sym} の {key} が数値ではありません"
                                    f"（{raw!r}）")
                    bad = True
                    continue
                if abs(v) > SANE_LIMIT:
                    problems.append(
                        f"{i}行目: {sym} の {key} が {v:,.0f} 円で、"
                        f"1Lot1日としては大きすぎます（桁の確認を）")
                    bad = True
                    continue
                vals[key] = v
            if bad:
                continue

            if sym in table:
                problems.append(f"{i}行目: {sym} が重複しています")
                continue
            table[sym] = vals
    return table, problems


def check_signs(table: Dict[str, Dict[str, float]]) -> List[str]:
    """符号の筋を確かめる。**勝手に直さず、気づかせる。**

    買い持ちと売り持ちが同符号で両方プラスなら、どちらを持っても
    もらえることになる。業者はそうしないので、写し間違いの疑いが濃い。
    """
    notes: List[str] = []
    for sym, v in sorted(table.items()):
        if v["buy"] > 0 and v["sell"] > 0:
            notes.append(f"{sym}: 買いも売りも受取（+{v['buy']:g} / "
                         f"+{v['sell']:g}）。写し間違いの可能性があります")
        if v["buy"] == 0 and v["sell"] == 0:
            notes.append(f"{sym}: 両方0です。未対応なら行を消してください")
    return notes


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    from app.config import get_trading_config

    ap = argparse.ArgumentParser(
        description="業者のスワップ表を CSV から取り込む（推測値は入れない）")
    ap.add_argument("--csv", required=True, help="読み込む CSV")
    ap.add_argument("--source", required=True,
                    help="出所（例: 外為オンライン 店頭FX スワップポイント）")
    ap.add_argument("--as-of", required=True, dest="as_of",
                    help="その表の日付 YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true",
                    help="書き込まずに内容だけ確かめる")
    a = ap.parse_args(argv)

    try:
        as_of = datetime.strptime(a.as_of, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(f"--as-of の形式が違います: {a.as_of}（YYYY-MM-DD）")
    if as_of > date.today():
        raise SystemExit(f"--as-of が未来の日付です: {as_of}")

    path = Path(a.csv)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        raise SystemExit(f"CSV が見つかりません: {path}")

    cfg = get_trading_config()
    known = {p.symbol for p in cfg.pairs}

    table, problems = parse_rows(path, known)
    notes = check_signs(table)

    print(f"読み込み: {path}")
    print(f"出所    : {a.source}")
    print(f"日付    : {as_of}")
    print(f"取り込めた銘柄: {len(table)} / 設定 {len(known)}")
    if table:
        print()
        print(f"{'銘柄':<10}{'買い持ち':>10}{'売り持ち':>10}")
        for sym, v in sorted(table.items()):
            print(f"{sym:<10}{v['buy']:>10,.0f}{v['sell']:>10,.0f}")

    if problems:
        print()
        print(f"飛ばした行 {len(problems)} 件:")
        for p in problems:
            print(f"  - {p}")
    if notes:
        print()
        print("確認してほしい点:")
        for n in notes:
            print(f"  ! {n}")

    if not table:
        print()
        print("取り込める行がありませんでした。書き込みません。")
        return 1

    missing = sorted(known - set(table))
    if missing:
        print()
        print(f"表に無い銘柄 {len(missing)} 件: {', '.join(missing)}")
        print("  → これらは計画で「不明」のままになります（0円にはしません）")

    if a.dry_run:
        print()
        print("--dry-run なので書き込みませんでした。")
        return 0

    current = json.loads(CONFIG.read_text(encoding="utf-8"))
    current["available"] = True
    current["source"] = a.source
    current["as_of"] = as_of.isoformat()
    current["table"] = {k: {"buy": v["buy"], "sell": v["sell"]}
                        for k, v in sorted(table.items())}
    CONFIG.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print()
    print(f"書き込みました: {CONFIG}")
    print("計画のスワップ欄が「不明」から実額に変わります。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
