# -*- coding: utf-8 -*-
"""指標の候補を試す（backend/app/signal_lab.py）。先に scripts/replay_boards.py を走らせておく。

    .venv/Scripts/python.exe scripts/signal_lab.py                 # いちばん新しい再生を使う
    .venv/Scripts/python.exe scripts/signal_lab.py --replay logs/replay/2026-10-18.json

結果は logs/replay/<日付>-signals.md に書く。**過去の数え上げで、予測ではない。**
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import board_replay as R                              # noqa: E402
from app import signal_lab as S                                # noqa: E402
from app.board import fx_day_start                             # noqa: E402
from app.config import get_settings, get_trading_config        # noqa: E402
from app.indicator_engine import adx as adx_series             # noqa: E402
from app.indicator_engine import atr as atr_series             # noqa: E402
from app.indicator_engine import bollinger                     # noqa: E402
from app.market_data import build_provider                     # noqa: E402

JST = timezone(timedelta(hours=9))
SESSION_JA = {"tokyo": "東京 08:42", "london": "ロンドンの始まり", "newyork": "NY の始まり"}


def _pct(x):
    return "—" if x is None else f"{x * 100:.0f}%"


def _r(x):
    return "—" if x is None else f"{x:+.2f}"


def _rng(s):
    return "—" if s.get("win_lo") is None else f"{s['win_lo'] * 100:.0f}〜{s['win_hi'] * 100:.0f}%"


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--replay", help="replay_boards.py の JSON（省略時はいちばん新しいもの）")
    a = ap.parse_args(argv)
    archive = ROOT / "logs" / "replay" / "archive.json"
    src = (Path(a.replay) if a.replay else archive if archive.exists()
           else max((ROOT / "logs" / "replay").glob("20??-??-??.json")))
    rows: List[Dict] = json.loads(src.read_text(encoding="utf-8"))["rows"]
    rows = [r for r in rows if "long_outcome" in r]

    cfg = get_trading_config()
    provider = build_provider(get_settings())
    pairs = sorted({r["pair"] for r in rows})
    m15c, m15s, atrh, h1s, adxs, bbs = {}, {}, {}, {}, {}, {}
    for p in pairs:
        h = provider.get_candles(p, "H1", limit=100000).candles
        h1s[p] = S.from_candles(h, 60)
        adxs[p] = adx_series(h1s[p].h, h1s[p].l, h1s[p].c, 14)
        c = provider.get_candles(p, "M15", limit=100000).candles
        m15c[p], m15s[p] = c, S.from_candles(c, 15)
        av = atr_series([float(x.high) for x in c], [float(x.low) for x in c],
                        [float(x.close) for x in c], 14)
        pts = [(m15s[p].close_ts[i], v) for i, v in enumerate(av) if v]
        atrh[p] = ([t for t, _ in pts], [v for _, v in pts])
        _mid, up, lo, width = bollinger(m15s[p].c, 20, 2.0)
        bbs[p] = (up, lo, width)
    drivers = {n: S.load_driver(n) for n in ("DXY", "VIX", "US10Y")}

    def ctx_for(r):
        return {"t": datetime.fromisoformat(r["at"]), "m15": m15s[r["pair"]],
                "drivers": drivers, "atr_hist": atrh[r["pair"]],
                "h1": h1s[r["pair"]], "adx": adxs[r["pair"]], "bb": bbs[r["pair"]]}

    L: List[str] = [f"# 指標の候補を試す（{src.name} の {len(rows)} 時刻）", "",
                    "向きを言う候補は、その向きで建てた結果を数え、**同じ時刻に向きを問わず建てた場合**と比べる。"
                    "「差」がプラスで、95%の幅が十分に狭く、**前半・後半の両方で**上回るものだけが候補として残る。",
                    "TP1 は損切りの1.5倍なので、TP1 の割合が40%を超えるまでは費用を取り戻せない。", "",
                    "## 向きを言う候補", "",
                    "| 候補 | 件数 | TP1 の割合（95%の幅） | 平均 R | 同じ時刻・向きを問わず | 差 | 前半 / 後半 | 向きの的中（刻限まで） |",
                    "|---|---|---|---|---|---|---|---|"]
    results = {}
    for key, (label, fn) in S.CANDIDATES.items():
        e = S.evaluate(rows, fn, ctx_for)
        results[key] = e
        s, b = e["signal"], e["same_times"]
        mark = " ◎" if e["both_halves_better"] and (e["edge_r"] or 0) > 0 else ""
        d = e["direction"]
        L.append(f"| {label}{mark} | {s['n']} | {_pct(s['win_rate'])}（{_rng(s)}） | {_r(s['avg_r'])} | "
                 f"{_r(b['avg_r'])} | {_r(e['edge_r'])} | {_r(e['first_half']['avg_r'])} / "
                 f"{_r(e['second_half']['avg_r'])} | {_pct(d['rate'])}（{d['n']}件） |")

    # 東京の値幅をロンドンで抜ける（時刻が違うので別に数える）
    first = min(datetime.fromisoformat(r["at"]) for r in rows)
    last = max(datetime.fromisoformat(r["at"]) for r in rows)
    days, d0 = [], fx_day_start(first)
    while d0 <= last:
        if (d0 + timedelta(hours=12)).weekday() < 5:
            days.append(d0)
        d0 = fx_day_start(d0 + timedelta(hours=30))
    bo = []
    for p in pairs:
        bo += [(p,) + x for x in S.tokyo_breakout(cfg, p, m15c[p], days)]
    bo.sort(key=lambda x: x[1])
    sig = R._stats([(x[2], x[3]) for x in bo])
    same = R._stats([(x[2], x[3]) for x in bo] + [(x[4], x[5]) for x in bo])
    half = len(bo) // 2
    h1, h2 = R._stats([(x[2], x[3]) for x in bo[:half]]), R._stats([(x[2], x[3]) for x in bo[half:]])
    edge = (sig["avg_r"] - same["avg_r"]) if sig["n"] else None
    L.append(f"| 東京の値幅をロンドンの最初の3時間で抜けた向き | {sig['n']} | {_pct(sig['win_rate'])}（{_rng(sig)}） | "
             f"{_r(sig['avg_r'])} | {_r(same['avg_r'])} | {_r(edge)} | {_r(h1['avg_r'])} / {_r(h2['avg_r'])} | — |")

    L += ["", "◎＝差がプラスで、前半・後半の両方で上回った（それでも件数が少なければ偶然の可能性は残る）。", "",
          "## 向きを言わない指標（場面の選び方）", "",
          "### 時間帯", "", "| 時間帯 | 向きを問わず：件数・TP1・平均 R | ボードがそろった（除外なし）：件数・TP1・平均 R |",
          "|---|---|---|"]
    by_s = {}
    for r in rows:
        by_s.setdefault(r["session"], []).append(r)
    for sname in ("tokyo", "london", "newyork"):
        rs = by_s.get(sname, [])
        b = R._stats([(r["long_outcome"], r["long_r"]) for r in rs] + [(r["short_outcome"], r["short_r"]) for r in rs])
        ln = [r for r in rs if r["lean"] in ("up", "down") and not r["excluded"]]
        s = R._stats([(r[("long" if r["lean"] == "up" else "short") + "_outcome"],
                       r[("long" if r["lean"] == "up" else "short") + "_r"]) for r in ln])
        L.append(f"| {SESSION_JA[sname]} | {b['n']}・{_pct(b['win_rate'])}・{_r(b['avg_r'])} | "
                 f"{s['n']}・{_pct(s['win_rate'])}・{_r(s['avg_r'])} |")

    L += ["", "### 値動きの大きさ（いまの ATR ÷ 直近20日の中央値）", "",
          "| 区分 | 向きを問わず：件数・TP1・平均 R | ボードがそろった：件数・TP1・平均 R |", "|---|---|---|"]
    edges, labels = (0.8, 1.2), ("普段より小さい（0.8倍未満）", "普段どおり", "普段より大きい（1.2倍以上）")
    vb = S.by_bucket(rows, S.vol_ratio, ctx_for, edges, labels)
    vl = S.by_bucket(rows, S.vol_ratio, ctx_for, edges, labels, only_lean=True)
    for lab in labels:
        b, s = vb[lab], vl[lab]
        L.append(f"| {lab} | {b['n']}・{_pct(b['win_rate'])}・{_r(b['avg_r'])} | "
                 f"{s['n']}・{_pct(s['win_rate'])}・{_r(s['avg_r'])} |")

    for title, key, edges2, labels2 in (
            ("H1 の ADX（トレンドの強さ）", S.h1_adx, (20.0, 25.0), ("20未満（弱い）", "20〜25", "25以上（強い）")),
            ("ボリンジャーバンドの幅（直近10日の中での位置）", S.bb_width_pct, (0.2, 0.8),
             ("狭い（下位20%）", "ふつう", "広い（上位20%）"))):
        L += ["", f"### {title}", "", "| 区分 | 向きを問わず：件数・TP1・平均 R | ボードがそろった：件数・TP1・平均 R |",
              "|---|---|---|"]
        xb = S.by_bucket(rows, key, ctx_for, edges2, labels2)
        xl = S.by_bucket(rows, key, ctx_for, edges2, labels2, only_lean=True)
        for lab in labels2:
            b, s2 = xb[lab], xl[lab]
            L.append(f"| {lab} | {b['n']}・{_pct(b['win_rate'])}・{_r(b['avg_r'])} | "
                     f"{s2['n']}・{_pct(s2['win_rate'])}・{_r(s2['avg_r'])} |")

    L += ["", "「向きを問わず」の平均 R は、費用（スプレッド・滑り込み）がどれだけ重いかを表す。"
          "これが0に近い場面ほど、向きが当たったときに費用で消えにくい。"]

    # 利確の倍率を変えて測る（**測るだけ。計画の規則は変えない**）
    multiples = (1.0, 1.5, 2.0, 3.0)
    sim = R.resim(cfg, rows, m15c, multiples)
    vr = {f"{r['pair']}|{r['at']}": S.vol_ratio(r, ctx_for(r)) for r in rows}
    dx = {f"{r['pair']}|{r['at']}": S.dxy_4h(r, ctx_for(r)) for r in rows}

    def pick_side(lean):
        return "long" if lean == "up" else "short"

    groups = {
        "向きを問わず": lambda r, k: ["long", "short"],
        "ボードがそろった（除外なし）": lambda r, k: ([pick_side(r["lean"])]
                                       if r["lean"] in ("up", "down") and not r["excluded"] else []),
        "ドル指数の向き": lambda r, k: [pick_side(dx[k])] if dx.get(k) else [],
        "値動きが大きい（向きを問わず）": lambda r, k: (["long", "short"]
                                       if (vr.get(k) or 0) >= 1.2 else []),
    }
    L += ["", "## 利確の倍率を変えたら（同じ時刻・同じ建値・同じ損切り）", "",
          "**測るだけで、計画の規則（いまは1.5倍）は変えていない。** 時間切れを除けば、"
          "TP の割合が「損益分岐」を超えて初めて費用を取り戻せる。", "",
          "| 利確の倍率 | 損益分岐 | " + " | ".join(groups) + " |",
          "|---|---|" + "---|" * len(groups)]
    for m in multiples:
        cells = []
        for fn in groups.values():
            res = []
            for r in rows:
                k = f"{r['pair']}|{r['at']}"
                if k not in sim:
                    continue
                for side in fn(r, k):
                    res.append(sim[k][m][side])
            s = R._stats(res)
            cells.append(f"{s['n']}件・TP {_pct(s['win_rate'])}・{_r(s['avg_r'])}")
        L.append(f"| {m:g} 倍 | {1 / (1 + m) * 100:.0f}% | " + " | ".join(cells) + " |")
    out = ROOT / "logs" / "replay" / f"{datetime.now(JST):%Y-%m-%d}-signals.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    (out.with_suffix(".json")).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(L))
    print(f"\n書きました: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
