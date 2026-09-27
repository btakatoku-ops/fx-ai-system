# -*- coding: utf-8 -*-
"""外為オンラインの月次スワップ表（PDF）から CSV を作る。

業者が公開している「スワップポイント一覧表」を読んで、
``scripts/import_swap.py`` が食べられる CSV に直すだけの道具。
**数字はこちらで作らない。** 表に載っている値をそのまま写す。

表の作り
--------

::

    通貨ペア USD/JPY EUR/JPY ...
    日付 付与日数 売 買 付与日数 売 買 ...
    2026 / 09 / 11 金 1 -120 95 1 -90 80 ...

- **値は「付与日数ぶんの合計」**。水曜は3日分、連休前は4日分がまとめて
  付くので、1日あたりに直すには付与日数で割る。
- **付与日数は銘柄ごとに違う。** 同じ日でも USD/CAD が1日、MXN/JPY が
  2日ということがある。「付与日数1の日だけ拾う」という選び方をすると、
  その日の一部の銘柄を丸ごと落とす。
- 表には先の日付も並ぶが、まだ決まっていない日は ``-`` になっている。
  **決まっていない日を0として読まない。**
- 単位は PDF の注記どおり「1万通貨あたり」。設定の ``units_per_lot`` と同じ。

使い方::

    python scripts/fetch_swap_gaitame.py --out swap.csv
    python scripts/import_swap.py --csv swap.csv \\
        --source "外為オンライン スワップポイント一覧表 2026年9月" --as-of 2026-09-11
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# ブラウザのふりはしない。素直に名乗る。
UA = "fx-ai-system/0.1 (personal research; contact via repository owner)"
BASE = "https://www.gaitameonline.com/pdf/swap{year:04d}{month:02d}.pdf"

_DATE_ROW = re.compile(
    r"^(\d{4})\s*/\s*(\d{1,2})\s*/\s*(\d{1,2})\s*(\S)\s+(.*)$")


def download(year: int, month: int, dest: Path) -> Path:
    url = BASE.format(year=year, month=month)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    dest.write_bytes(data)
    print(f"取得: {url} ({len(data):,} バイト)")
    return dest


def page_pairs(text: str) -> List[str]:
    """見出しから、そのページに並ぶ通貨ペアを取る。"""
    for line in text.splitlines():
        if line.startswith("通貨ペア"):
            raw = line.replace("通貨ペア", "").split()
            return [p.replace("/", "").upper() for p in raw if "/" in p]
    return []


def parse_page(text: str) -> Tuple[List[str], Dict[date, Dict[str, Tuple[int, float, float]]]]:
    """1ページを読む。返すのは (通貨ペア, {日付: {銘柄: (付与日数, 売, 買)}})。"""
    pairs = page_pairs(text)
    rows: Dict[date, Dict[str, Tuple[int, float, float]]] = {}
    if not pairs:
        return pairs, rows
    n = len(pairs)

    for line in text.splitlines():
        m = _DATE_ROW.match(line.strip())
        if not m:
            continue
        y, mo, d, _wd, rest = m.groups()
        try:
            day = date(int(y), int(mo), int(d))
        except ValueError:
            continue
        tokens = rest.split()
        if len(tokens) != n * 3:
            continue                 # 土日や未確定の行は形が違う。飛ばす
        row: Dict[str, Tuple[int, float, float]] = {}
        ok = True
        for i, sym in enumerate(pairs):
            chunk = tokens[i * 3: i * 3 + 3]
            if any(c == "-" for c in chunk):
                ok = False
                break
            try:
                days = int(chunk[0])
                sell = float(chunk[1].replace(",", ""))
                buy = float(chunk[2].replace(",", ""))
            except ValueError:
                ok = False
                break
            row[sym] = (days, sell, buy)
        if ok:
            rows[day] = row
    return pairs, rows


def per_day_rates(
    pages: List[Tuple[List[str], Dict[date, Dict[str, Tuple[int, float, float]]]]],
    on_or_before: Optional[date] = None,
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, date], List[str]]:
    """銘柄ごとに、いちばん新しい日の値を1日あたりに直す。

    **付与日数で割る。** 表の値は「付与日数ぶんの合計」で、水曜は3日分、
    連休前は4日分がまとめて付く。そのまま1日あたりとして読むと3〜4倍になる。

    **付与日数は銘柄ごとに違う。** 同じ 2026/09/11 でも、USD/CAD は1日で
    MXN/JPY は2日だった。「付与日数が1の日だけ拾う」という選び方をすると、
    その日の MXN/JPY を丸ごと落とす。実際に落として気づいた。

    付与日数0の日は「その日は付かない」という意味なので、1日あたりの
    レートの手がかりにはならない。飛ばす。
    """
    latest: Dict[str, Tuple[date, float, float]] = {}
    notes: List[str] = []

    for _pairs, rows in pages:
        for day, row in rows.items():
            if on_or_before and day > on_or_before:
                continue
            for sym, (days, sell, buy) in row.items():
                if days <= 0:
                    continue          # その日は付かない
                if sym in latest and latest[sym][0] >= day:
                    continue
                latest[sym] = (day, buy / days, sell / days)

    table = {sym: {"buy": round(b, 2), "sell": round(s, 2)}
             for sym, (_d, b, s) in latest.items()}
    as_of = {sym: d for sym, (d, _b, _s) in latest.items()}

    if as_of:
        days_used = sorted(set(as_of.values()))
        if len(days_used) > 1:
            notes.append(
                f"銘柄によって採用した日付が違います（{days_used[0]}〜{days_used[-1]}）。"
                f"その日に値が出ていない銘柄は、直近の出ている日を使っています")
    return table, as_of, notes


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    from app.config import get_trading_config

    today = date.today()
    ap = argparse.ArgumentParser(
        description="外為オンラインの月次スワップ表（PDF）から CSV を作る")
    ap.add_argument("--year", type=int, default=today.year)
    ap.add_argument("--month", type=int, default=today.month)
    ap.add_argument("--out", default="swap.csv", help="書き出す CSV")
    ap.add_argument("--pdf", default=None,
                    help="ダウンロード済みの PDF を使う（取得しない）")
    a = ap.parse_args(argv)

    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit("pypdf が要ります: pip install pypdf")

    if a.pdf:
        pdf_path = Path(a.pdf)
        if not pdf_path.is_absolute():
            pdf_path = ROOT / pdf_path
    else:
        pdf_path = ROOT / f".cache_swap{a.year:04d}{a.month:02d}.pdf"
        download(a.year, a.month, pdf_path)

    reader = PdfReader(str(pdf_path))
    pages = [parse_page(pg.extract_text() or "") for pg in reader.pages]
    all_pairs = [s for pairs, _ in pages for s in pairs]
    print(f"表にある通貨ペア: {len(all_pairs)} 件")

    # 単位の注記を確かめる。**1万通貨あたりでなければ、そのまま使えない。**
    full_text = "\n".join((pg.extract_text() or "") for pg in reader.pages)
    if "1万通貨あたり" not in full_text:
        raise SystemExit(
            "PDF に「1万通貨あたり」の注記が見当たりません。単位が変わった"
            "可能性があるので、そのままでは取り込めません。表を確認してください")
    print("単位: 1万通貨あたり（注記を確認）")

    table, as_of_by_pair, notes = per_day_rates(pages, on_or_before=today)
    if not table:
        raise SystemExit("値の入った日が見つかりませんでした")
    as_of = max(as_of_by_pair.values())
    print(f"採用した日付: {min(as_of_by_pair.values())} 〜 {as_of}"
          f"（付与日数で割って1日あたりに直しています）")
    for n in notes:
        print(f"  ! {n}")

    cfg = get_trading_config()
    known = {p.symbol for p in cfg.pairs}
    usable = {k: v for k, v in table.items() if k in known}
    unknown = sorted(set(table) - known)
    missing = sorted(known - set(table))

    out = Path(a.out)
    if not out.is_absolute():
        out = ROOT / out
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "buy", "sell"])
        for sym in sorted(usable):
            w.writerow([sym, f"{usable[sym]['buy']:g}", f"{usable[sym]['sell']:g}"])

    print(f"書き出し: {out}（{len(usable)} 銘柄）")
    if unknown:
        print(f"設定に無い銘柄（書き出していません）: {', '.join(unknown)}")
    if missing:
        print(f"表に無い銘柄: {', '.join(missing)}")

    print()
    print("次に、これを取り込んでください:")
    print(f'  python scripts/import_swap.py --csv {a.out} \\')
    print(f'      --source "外為オンライン スワップポイント一覧表 '
          f'{a.year}年{a.month}月" --as-of {as_of}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
