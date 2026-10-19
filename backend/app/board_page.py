# -*- coding: utf-8 -*-
"""公開ページ（GitHub Pages）。1枚のダッシュボードにする。

このページは**誰でも見られる**（リポジトリが公開なので、Pages も公開）。

- 個人の情報・ブリーフ本体へのリンクと文面は載せない。検索エンジンには載せない（noindex）。
- **いつ時点の写しかを一番上に出す。** 生きた画面ではない。古さを数えて表示する。
- 「発注」「買い」「売り」とは書かない。ボードの言葉のまま。
- 外から読み込むものは無し（文字・色・図・計画の計算はすべてこのファイルの中）。
- **計画もこのページだけで出す**（2026-10-01）。計算は static/plancalc.cjs
  （Python と一致を試験で確認）。写しが古ければ（20分）計画は出さない。
- **見た目はダッシュボード**（2026-10-09、利用者の指定した画面にならう）。ただし**どの欄も
  実際のデータだけ**。飾りの図は作らない。チャートの帯は実データで測った「この先の値動きの幅」で、向きは示さない。
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Dict, List, Optional

JST = timezone(timedelta(hours=9))

_STATE_CLASS = {"excluded": "excluded", "aligned_up": "up",
                "aligned_down": "down", "mixed": "mixed"}
_STATE_MARK = {"excluded": "✕", "aligned_up": "▲", "aligned_down": "▼", "mixed": "■"}
_MARK = {"up": "▲", "down": "▼", "neutral": "■", "none": "—", None: "—"}
_BRIEF_LEAN = {"up": "▲ 上方向", "down": "▼ 下方向", "neutral": "■ 中立",
               "conflict": "■ 対立"}
_SESSION = {"tokyo": "東京", "london": "ロンドン", "newyork": "NY"}
_SEG_END = {"tokyo": "→ロンドン", "london": "→NY", "newyork": "→刻限"}
_TIER_CHIP = {"watch": "費用：様子を見る区分", "off": "費用：重い区分"}
_KEYS = (("trend", "トレンド"), ("momentum", "モメンタム"),
         ("fundamentals", "ファンダ"), ("range", "レンジ"))


def _e(x) -> str:
    """HTML に入れる文字。端末向けの強調（**）は外す。"""
    return escape(("" if x is None else str(x)).replace("**", ""))


def _fmt(x: Optional[float], d: int) -> str:
    return "—" if x is None else f"{x:.{d}f}"


def _jst(iso: Optional[str], fmt: str = "%H:%M") -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).astimezone(JST).strftime(fmt)
    except ValueError:
        return "—"


def near_levels(facts: Dict, n: int = 2):
    """いまの値に近い水準を、上と下から n 本ずつ（距離は ADR で）。"""
    p, adr = facts.get("price"), facts.get("adr")
    if p is None:
        return [], []
    def dist(x):
        return (x - p) / adr if adr else None
    lv = [dict(l, adr=dist(l["price"])) for l in facts.get("levels") or []]
    above = sorted([l for l in lv if l["price"] > p], key=lambda l: l["price"])[:n]
    below = sorted([l for l in lv if l["price"] <= p], key=lambda l: -l["price"])[:n]
    return above, below


# ------------------------------------------------------------ チャート（SVG）


def _ticks(lo: float, hi: float, n: int = 4) -> List[float]:
    """価格の目盛り。1・2・2.5・5 × 10^k の切りのよい刻みで、n 本前後。"""
    span = hi - lo
    if span <= 0:
        return []
    raw = span / n
    k = 10 ** math.floor(math.log10(raw))
    step = next(m * k for m in (1, 2, 2.5, 5, 10) if m * k >= raw)
    out, i = [], math.ceil(lo / step)
    while i * step <= hi + 1e-12:
        out.append(round(i * step, 10))
        i += 1
    return out


def _chart(b: Dict, ch: Optional[Dict]) -> str:
    """その取引日の M15 のローソク足と、**この先の値動きの幅**（実データで測った約8割の範囲。
    向きは示さない）。

    **図形は SVG、文字はすべて HTML。** SVG は枠いっぱいに引き伸ばす（縦横比を保たない）ので、
    文字を SVG に入れると携帯で歪む（2026-10-09 まで歪んでいた）。位置は % で合わせる。
    """
    f, d = b["facts"], b.get("digits", 3)
    if not ch or not ch.get("bars"):
        return '<div class="nochart">値動きの足がありません</div>'
    W, H = 1000, 100                     # 図形の座標（枠いっぱいに引き伸ばす）
    bars = ch["bars"]
    start = datetime.fromisoformat(ch["day_start"])
    # 刻限の位置と、横軸の長さは別。**刻限より後の足**（閉場前の金曜の夜など）があれば
    # 横軸はそこまで伸ばすが、刻限の線は刻限に引く（2026-10-11 まで右端に引いていた）
    dl_min = 19 * 60
    if ch.get("deadline"):
        dl_min = max(60, int((datetime.fromisoformat(ch["deadline"]) - start).total_seconds() // 60))
    end_min = max(dl_min, bars[-1][0] + 15)
    op = ch.get("open")

    lo = min(x[2] for x in bars)
    hi = max(x[1] for x in bars)
    # この先の幅：最後の足から、測った本数（15分×本数）ぶんだけ。**刻限より先と、
    # 測った範囲（32本）より先は描かない。**
    cone = []
    band = ch.get("band")
    if band and band.get("levels"):
        t0 = bars[-1][0]
        cone.append((t0, band["close"], band["close"]))
        for lv in band["levels"]:
            t = t0 + 15 * lv["bars"]
            if t > dl_min:
                break
            cone.append((t, lv["high"], lv["low"]))
        if len(cone) > 1:
            lo, hi = min(lo, min(c[2] for c in cone)), max(hi, max(c[1] for c in cone))
        else:
            cone = []
    span = (hi - lo) or 1e-6
    keep = [l for l in (f.get("levels") or [])
            if lo - 0.15 * span <= l["price"] <= hi + 0.15 * span]
    for l in keep:
        lo, hi = min(lo, l["price"]), max(hi, l["price"])
    # 利確の目安（そろった向きがある時だけ）。建値・損切り・TP1 は必ず枠に入れ、TP2・3 は近ければ
    tp = b.get("tp_preview") if (b.get("tp_preview") or {}).get("status") == "PLAN_OK" else None
    tp_lines = []
    if tp:
        span = (hi - lo) or 1e-6
        must = [float(tp["stop"]), float(tp["targets"][0]["price"])]
        lo, hi = min([lo] + must), max([hi] + must)
        tp_lines = [("sl", "損切り", float(tp["stop"]))]
        for t in tp["targets"]:
            v = float(t["price"])
            if t is tp["targets"][0] or lo - 0.35 * span <= v <= hi + 0.35 * span:
                tp_lines.append(("tp", t["label"], v))
                lo, hi = min(lo, v), max(hi, v)
    # フィボナッチ（直近24時間）。枠の近くの線だけ引き、全部の値は図の下に書く
    fb = ch.get("fib") or {}
    span = (hi - lo) or 1e-6
    fib_lines = [l for l in fb.get("levels") or []
                 if lo - 0.1 * span <= l["price"] <= hi + 0.1 * span]
    for l in fib_lines:
        lo, hi = min(lo, l["price"]), max(hi, l["price"])
    ext = [l["price"] for l in fb.get("levels") or [] if l["kind"] == "extend"]
    # H1 の EMA20・50（トレンドの根拠）。大きく離れた値で枠をつぶさないよう、近いものだけ入れる
    span = (hi - lo) or 1e-6
    ema_pts = [p for p in ch.get("ema") or [] if p[0] <= end_min]
    for _t, a, c in ema_pts:
        for v in (a, c):
            if lo - 0.5 * span <= v <= hi + 0.5 * span:
                lo, hi = min(lo, v), max(hi, v)
    pad = (hi - lo) * 0.06 or 1e-6
    lo, hi = lo - pad, hi + pad

    def X(t):
        return W * t / end_min

    def Y(p):
        return H * (hi - p) / (hi - lo)

    def px(v):                           # 図形の座標 → 枠の中の %
        return f"{v / W * 100:.2f}%"

    def py(v):
        return f"{v / H * 100:.2f}%"

    svg = [f'<svg class="chart" viewBox="0 0 {W} {H}" preserveAspectRatio="none" aria-hidden="true">']
    html, yax, xax = [], [], []
    for v in _ticks(lo, hi):
        y = Y(v)
        svg.append(f'<line class="g" x1="0" y1="{y:.2f}" x2="{W}" y2="{y:.2f}"/>')
        if abs(y - Y(bars[-1][3])) > 7:               # 最後の値の札と重なる目盛りは書かない
            yax.append(f'<span style="top:{py(y)}">{_fmt(v, d)}</span>')
    for h in range(0, end_min // 60 + 1, 3):     # 時刻の目盛り（日本時間、3時間ごと）
        x = X(h * 60)
        svg.append(f'<line class="g" x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{H}"/>')
        lab = (start + timedelta(hours=h)).astimezone(JST).strftime("%H:%M")
        xax.append(f'<span style="left:{px(x)}">{lab}</span>')
    if op is not None:
        svg.append(f'<line class="openl" x1="0" y1="{Y(op):.2f}" x2="{W}" y2="{Y(op):.2f}"/>')
    if cone:
        up = " ".join(f"{X(t):.1f},{Y(u):.2f}" for t, u, _ in cone)
        dn = " ".join(f"{X(t):.1f},{Y(v):.2f}" for t, _, v in reversed(cone))
        svg.append(f'<polygon class="cone" points="{up} {dn}"/>')
    for l in keep:
        y = Y(l["price"])
        svg.append(f'<line class="lv {_e(l.get("kind"))}" x1="0" y1="{y:.2f}" x2="{W}" y2="{y:.2f}"/>')
        below = " below" if y < 9 else ""             # 上の端では線の下に書く（切れないように）
        html.append(f'<span class="lvt {_e(l.get("kind"))}{below}" style="top:{py(y)}">'
                    f'{_e(l.get("label"))} {_fmt(l["price"], d)}</span>')
    for k, idx in (("ema20", 1), ("ema50", 2)):  # H1 の EMA（確定した時刻に点を置く）
        pts = " ".join(f"{X(p[0]):.1f},{Y(p[idx]):.2f}" for p in ema_pts)
        if len(ema_pts) >= 2:
            svg.append(f'<polyline class="{k}" points="{pts}"/>')
    if len(ext) == 2:                            # 伸びの帯（127.2〜161.8%）。枠の中だけ塗る
        y1, y2 = sorted((Y(ext[0]), Y(ext[1])))
        y1, y2 = max(y1, 0), min(y2, H)
        if y2 > y1:
            svg.append(f'<rect class="fibz" x="0" y="{y1:.2f}" width="{W}" height="{y2 - y1:.2f}"/>')
    for l in fib_lines:
        y = Y(l["price"])
        svg.append(f'<line class="fib" x1="0" y1="{y:.2f}" x2="{W}" y2="{y:.2f}"/>')
        html.append(f'<span class="fbt{" below" if y < 9 else ""}" style="top:{py(y)}">Fib {l["ratio"] * 100:.1f}% '
                    f'{_fmt(l["price"], d)}</span>')
    for k, lab, v in tp_lines:
        y = Y(v)
        svg.append(f'<line class="{k}" x1="0" y1="{y:.2f}" x2="{W}" y2="{y:.2f}"/>')
        html.append(f'<span class="tpt {k}{" below" if y < 9 else ""}" style="top:{py(y)}">{_e(lab)} {_fmt(v, d)}</span>')
    # ローソク足。始値が無い古い写しは、1つ前の終値で代える
    bw = max(1.0, W * 15 / end_min * 0.62)
    prev = op
    for x in bars:
        t, h_, l_, c = x[0], x[1], x[2], x[3]
        o = x[4] if len(x) > 4 else (prev if prev is not None else c)
        prev = c
        k = "u" if c >= o else "d"
        xc = X(t + 7.5)
        top, bot = Y(max(o, c)), Y(min(o, c))
        svg.append(f'<line class="wk {k}" x1="{xc:.1f}" y1="{Y(h_):.2f}" x2="{xc:.1f}" y2="{Y(l_):.2f}"/>'
                   f'<rect class="bd {k}" x="{xc - bw / 2:.1f}" y="{top:.2f}" width="{bw:.1f}" '
                   f'height="{max(bot - top, 0.35):.2f}"/>')
    # 終値の線（ローソクが細い携帯でも流れが追えるように、薄く重ねる）
    path = " ".join(f"{X(x[0] + 7.5):.1f},{Y(x[3]):.2f}" for x in bars)
    last_t, last_c = bars[-1][0], bars[-1][3]
    cls = "up" if op is not None and last_c >= op else "down"
    svg.append(f'<polyline class="px {cls}" points="{path}"/>')
    svg.append(f'<line class="lastl {cls}" x1="0" y1="{Y(last_c):.2f}" x2="{W}" y2="{Y(last_c):.2f}"/>')
    dlx = X(dl_min)
    svg.append(f'<line class="dl" x1="{dlx:.1f}" y1="0" x2="{dlx:.1f}" y2="{H}"/>')
    svg.append("</svg>")
    html.append(f'<span class="dlt" style="left:{px(dlx)}">刻限</span>')
    html.append(f'<span class="dot {cls}" style="left:{px(X(last_t + 7.5))};top:{py(Y(last_c))}"></span>')
    yax.append(f'<span class="last {cls}" style="top:{py(Y(last_c))}">{_fmt(last_c, d)}</span>')
    if ema_pts:
        e20, e50 = ema_pts[-1][1], ema_pts[-1][2]
        html.append(f'<span class="emat">H1 EMA20 <b class="e20">{_fmt(e20, d)}</b> '
                    f'EMA50 <b class="e50">{_fmt(e50, d)}</b> '
                    f'{"（20が上）" if e20 > e50 else "（20が下）"}</span>')
    label = f'{_e(b["pair"])} のその日の値動き（15分足）。最後 {_fmt(last_c, d)}'
    return (_chart_main(svg, html, yax, xax, label)
            + _osc_strips(ch, X, end_min))


def _chart_main(svg, html, yax, xax, label) -> str:
    return (f'<div class="chartbox" role="img" aria-label="{label}"><div class="plot">{"".join(svg)}'
            f'{"".join(html)}</div><div class="yax">{"".join(yax)}</div>'
            f'<div class="xax">{"".join(xax)}</div></div>')


def _osc_strips(ch: Dict, X, end_min: int) -> str:
    """チャートの下に RSI と MACD の小窓（ボードの「モメンタム」の根拠）。横軸はチャートと同じ。

    RSI が上の線（55）以上で MACD が0より上なら ▲、下の線（45）以下で0より下なら ▼、
    それ以外は ■（board.momentum_factor と同じ決まり）。"""
    osc = [p for p in ch.get("osc") or [] if p[1] is not None and p[2] is not None and p[0] <= end_min]
    if len(osc) < 2:
        return ""
    W, H = 1000, 100
    up_at, down_at = ch.get("rsi_up", 55), ch.get("rsi_down", 45)

    def ry(v):                                   # RSI は 0〜100 を枠に合わせる（20〜80 を主に）
        return H * (80 - max(20, min(80, v))) / 60

    pts = " ".join(f"{X(t + 7.5):.1f},{ry(r):.2f}" for t, r, _ in osc)
    rsi_svg = (f'<svg class="chart" viewBox="0 0 {W} {H}" preserveAspectRatio="none" aria-hidden="true">'
               f'<rect class="rsin" x="0" y="{ry(up_at):.2f}" width="{W}" height="{ry(down_at) - ry(up_at):.2f}"/>'
               f'<line class="g" x1="0" y1="{ry(50):.2f}" x2="{W}" y2="{ry(50):.2f}"/>'
               f'<polyline class="rsil" points="{pts}"/></svg>')
    last_r = osc[-1][1]
    rk = "up" if last_r >= up_at else "down" if last_r <= down_at else "mid"
    rsi_ax = (f'<span style="top:{ry(up_at) / H * 100:.1f}%">{up_at:g}</span>'
              f'<span style="top:{ry(down_at) / H * 100:.1f}%">{down_at:g}</span>'
              f'<span class="last {rk}" style="top:{ry(last_r) / H * 100:.1f}%">{last_r:.0f}</span>')
    m = max(abs(h) for _, _, h in osc) or 1e-9
    bw = max(1.0, W * 15 / end_min * 0.62)
    bars = "".join(
        f'<rect class="mh {"u" if h >= 0 else "d"}" x="{X(t + 7.5) - bw / 2:.1f}" '
        f'y="{50 - max(h, 0) / m * 46:.2f}" width="{bw:.1f}" height="{max(abs(h) / m * 46, 0.4):.2f}"/>'
        for t, _, h in osc)
    macd_svg = (f'<svg class="chart" viewBox="0 0 {W} {H}" preserveAspectRatio="none" aria-hidden="true">'
                f'<line class="g" x1="0" y1="50" x2="{W}" y2="50"/>{bars}</svg>')
    last_h = osc[-1][2]
    mk = "up" if last_h > 0 else "down"
    return (f'<div class="oscbox" aria-label="RSI と MACD（M15）">'
            f'<div class="plot">{rsi_svg}<span class="osct">RSI(14)<i class="wide"> 帯＝{down_at:g}〜{up_at:g}（そろっていない側）</i></span></div>'
            f'<div class="yax">{rsi_ax}</div>'
            f'<div class="plot">{macd_svg}<span class="osct">MACD ヒストグラム</span></div>'
            f'<div class="yax"><span class="last {mk}" style="top:50%">{"0より上" if last_h > 0 else "0より下"}</span></div>'
            f'</div>')


def _dxy_note(b: Dict, dxy: Optional[Dict]) -> str:
    """ドル指数（DXY）の直近4時間の向きを、その銘柄のドルの側に当てて見せる。**参考・測定中。**

    ドルを含まない銘柄（GBPJPY・EURJPY など）には出さない。判定（そろう・除外）には入れない。"""
    pair = b["pair"]
    if not dxy or dxy.get("change") is None or "USD" not in (pair[:3], pair[3:]):
        return ""
    ch = dxy["change"]
    usd = "ドル高" if ch > 0 else "ドル安" if ch < 0 else "変わらず"
    if ch == 0:
        mark, cls = "■", "mid"
    else:
        up = (ch > 0) == pair.startswith("USD")       # USDxxx はドル高で上、xxxUSD はドル高で下
        mark, cls = ("▲", "up") if up else ("▼", "down")
    sp = dxy.get("spark") or []
    svg = ""
    if len(sp) >= 2:
        lo_, hi_ = min(sp), max(sp)
        rng = (hi_ - lo_) or 1e-9
        pts = [(i * 120 / (len(sp) - 1), 26 - (v - lo_) / rng * 24) for i, v in enumerate(sp)]
        k = max(0, len(pts) - 1 - DXY_SPARK_TAIL)
        svg = ('<svg class="dxysp" viewBox="0 0 120 28" aria-hidden="true">'
               f'<polyline class="all" points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}"/>'
               f'<polyline class="tail {cls}" points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts[k:])}"/></svg>')
    pct = dxy.get("pct")
    stale = (f'<span class="warnword">（最後の足が{_jst(dxy.get("last_at"), "%m/%d %H:%M")}で古い）</span>'
             if dxy.get("stale") else "")
    st = dxy.get("stats")
    stats = ""
    if st:
        stats = (f'<br><span class="muted">過去の再生（{_e(st["measured"])} 時点・{st["n"]}件）：向きの的中 '
                 f'{st["dir_rate"] * 100:.0f}%（95%の幅 {st["dir_lo"] * 100:.0f}〜{st["dir_hi"] * 100:.0f}%）、'
                 f'TP1 {st["win_rate"] * 100:.0f}%・平均 {st["avg_r"]:+.2f}R（同じ時刻・向きを問わず {st["base_r"]:+.2f}R）。'
                 f'<b>単独では費用を取り戻せていません。</b>毎週土曜に測り直します。</span>')
    lg = dxy.get("long")
    if lg and lg.get("a_dir") is not None and lg.get("b_dir") is not None:
        works = (lg["a_edge"] or 0) > 0 and (lg["b_edge"] or 0) > 0
        stats += (f'<br><span class="{"muted" if works else "warnword"}">約2年の検証（{_e(lg["first"])}〜{_e(lg["last"])}、'
                  f'{_e(lg["split"])} の前と後に分けて）：向きの的中 {lg["a_dir"] * 100:.0f}%・{lg["b_dir"] * 100:.0f}%、'
                  f'向きを問わず建てた場合との差 {lg["a_edge"]:+.2f}R・{lg["b_edge"]:+.2f}R。'
                  + ("前後とも上回った。" if works else
                     "<b>長い期間では効果が確認できません</b>（直近8週の良さは、その時期の相場による可能性が高い）。")
                  + '</span>')
    return (f'<div class="lvnote dxy"><div class="dxyrow"><div><b>ドル指数（DXY）の直近4時間</b> '
            f'<b class="n {"up" if ch > 0 else "down" if ch < 0 else ""}">{ch:+.3f}'
            f'{"" if pct is None else f"（{pct:+.2f}%）"}</b> {usd}{stale}<br>'
            f'この銘柄では <b class="dxymark {cls}">{mark}</b>'
            f'<span class="muted">（{_e(pair)} は{"ドル高で上" if pair.startswith("USD") else "ドル高で下"}）</span>'
            f'</div>{svg}</div>'
            f'<span class="muted">参考・測定中。ボードの判定（そろう・除外）には入れていません。'
            f'太い線＝直近4時間（24時間の動きの中で）。</span>{stats}</div>')


DXY_SPARK_TAIL = 4


def _vol_note(ch: Optional[Dict]) -> str:
    """値動きの大きさ（いまの ATR ÷ 20日の中央値）。**向きではなく、費用の重さの目安。**"""
    v = (ch or {}).get("vol")
    if not v:
        return ""
    r = v["ratio"]
    label = {"high": "普段より大きい：費用の負担が軽い",
             "low": "普段より小さい：費用の負担が重い",
             "normal": "普段どおり"}[v["kind"]]
    pos = max(0.0, min(1.0, r / 2.0)) * 100
    return (f'<div class="lvnote vol {v["kind"]}"><b>値動きの大きさ</b> '
            f'<b class="n">{r:.2f}倍</b>（いまの ATR ÷ 直近{v["days"]:g}日の中央値）— {label}'
            f'<div class="vgauge"><span class="vlo"></span><span class="vhi"></span>'
            f'<i style="left:{pos:.1f}%"></i></div>'
            f'<span class="muted">過去の再生（1,876時刻）では、1.2倍以上の場面は向きを問わず平均 −0.10R、'
            f'普段どおりは −0.23R（費用の割合が小さくなるため）。向きは示しません。</span></div>')


# ------------------------------------------------------------ 欄


def _tp_note(b: Dict) -> str:
    """そろった向きがある時の、利確の目安（チャートの緑＝利確・赤＝損切りの線）。"""
    tp = b.get("tp_preview")
    if not tp:
        return ""
    d = b.get("digits", 3)
    long = tp.get("direction") == "LONG"
    head = "▲ 上でそろっています。買いで見た場合" if long else "▼ 下でそろっています。売りで見た場合"
    if tp.get("status") != "PLAN_OK":
        why = "・".join(_e(str(x).replace("**", "")) for x in tp.get("stops") or []) or "理由の記載なし"
        return f'<div class="lvnote tpbox{"" if long else " short"}"><b>{head}の利確の目安は出しません</b>：{why}</div>'
    tg = " ／ ".join(f'{_e(t["label"])} <b class="n">{_fmt(float(t["price"]), d)}</b>'
                     f'<span class="muted">（費用込み {_e(t["net_rr"])} 倍'
                     f'{"・" + _e(t["capped_by"]) + "の手前" if t.get("capped_by") else ""}）</span>'
                     for t in tp["targets"])
    return (f'<div class="lvnote tpbox{"" if long else " short"}"><b>{head}の利確の目安</b>：'
            f'建値 <b class="n">{_fmt(float(tp["entry"]), d)}</b> ／ 損切り <b class="n">'
            f'{_fmt(float(tp["stop"]), d)}</b><span class="muted">（{_e(tp["stop_basis"])}）</span> ／ {tg}'
            f'<br><span class="muted">計画ボタンと同じ計算。売値＝最後の終値、買値＝平常時のスプレッドを足した仮の値で、'
            f'MT4 の値ではありません。実際の値は計画ボタンに MT4 の売値・買値を入れて出してください。'
            f'儲かる根拠ではありません。</span></div>')


def _fib_note(b: Dict, ch: Optional[Dict]) -> str:
    """フィボナッチの値（図に入りきらない線も含めて全部）。"""
    fb = (ch or {}).get("fib")
    if not fb:
        return ""
    d = b.get("digits", 3)
    up = fb["direction"] == "up"
    ret = " ／ ".join(f'{l["ratio"] * 100:.1f}% <b class="n">{_fmt(l["price"], d)}</b>'
                      for l in fb["levels"] if l["kind"] == "retrace")
    ext = " ／ ".join(f'{l["ratio"] * 100:.1f}% <b class="n">{_fmt(l["price"], d)}</b>'
                      for l in fb["levels"] if l["kind"] == "extend")
    return (f'<div class="lvnote"><b class="fibk">Fib 予測レンジ</b>'
            f'<span class="muted">（直近24時間 安値 {_fmt(fb["low"], d)} → 高値 {_fmt(fb["high"], d)}、'
            f'{"上げ" if up else "下げ"}の脚）</span><br>'
            f'{"押し" if up else "戻り"}：{ret}<br>伸び（紫の帯）：{ext}'
            f'<br><span class="muted">フィボナッチは慣習の線で、このアプリでは当たりやすさを測っていません。'
            f'向きの予測ではありません。</span></div>')


def _band_note(ch: Optional[Dict]) -> str:
    """帯の説明。**測った数字をそのまま書く。** 測っていなければ帯は無いと書く。"""
    band = (ch or {}).get("band")
    if not band:
        return "この先の幅は測っていないので描きません"
    lo, hi = band.get("coverage_min"), band.get("coverage_max")
    cov = (f"{lo * 100:.0f}〜{hi * 100:.0f}%" if lo is not None and hi is not None else "約8割")
    return (f"帯＝この先8時間までの値動きの幅（実データで測り、{cov} が収まった範囲。"
            f"向きは示さない）")


def _chip(state: str, label: str) -> str:
    c = _STATE_CLASS.get(state, "mixed")
    return f'<span class="chip {c}">{_STATE_MARK.get(state, "■")} {_e(label)}</span>'


def _pair_panel(b: Dict, ch: Optional[Dict], dxy: Optional[Dict] = None, mr: Optional[Dict] = None) -> str:
    f, v, d = b["facts"], b["verdict"], b.get("digits", 3)
    st = v.get("state", "mixed")
    chg = f.get("today_change_pct")
    chg_html = ("" if chg is None else
                f'<span class="chg {"up" if chg >= 0 else "down"}">'
                f'{"+" if chg >= 0 else ""}{chg:.2f}%</span>')
    # 印：今日選んだ非円建て／費用の区分（実測。支援する区分には付けない）
    watch = ""
    if b.get("group") == "pick":
        watch += '<span class="chip sm pick">今日の選択</span> '
    if b.get("tier") in _TIER_CHIP:
        watch += f'<span class="chip sm watch">{_TIER_CHIP[b["tier"]]}</span> '
    head = (f'<div class="ph"><h2>◆ {_e(b["pair"])} · M15 · その日の値動き</h2>{watch}'
            f'{_chip(st, v.get("label", ""))}</div>')
    price = (f'<div class="pxrow"><span class="big">{_fmt(f.get("price"), d)}</span>{chg_html}'
             f'<span class="muted">普段の値幅 {_fmt(f.get("adr"), d)} ／ {_band_note(ch)}</span></div>')
    note = f'<p class="muted note">{_e(f["day_note"])}</p>' if f.get("day_note") else ""
    cells = []
    for fac in b.get("factors") or []:
        src = ""
        if fac.get("key") == "fundamentals":
            src = (f'<span class="src">{_e(fac.get("source") or "出典なし")}'
                   f'{" ／ 古いので数えていません" if fac.get("stale") else ""}</span>')
        cells.append(f'<div class="f v-{_e(fac.get("view"))}"><span class="lab">{_e(fac.get("label"))}'
                     f'</span><span class="mk">{_e(fac.get("mark"))}</span>'
                     f'<span class="tx">{_e(fac.get("text"))}</span>{src}</div>')
    reasons = ""
    for title, items, cls in (("除外の理由", v.get("exclude"), "ex"),
                              ("気をつけること", v.get("cautions"), "ca")):
        if items:
            reasons += (f'<div class="why {cls}"><b>{title}</b><ul>'
                        + "".join(f"<li>{_e(x)}</li>" for x in items) + "</ul></div>")
    facts = []
    if f.get("market_open") and f.get("deadline_jst"):
        m = f.get("minutes_to_deadline")
        left = ("" if m is None else
                f'（あと {m // 60} 時間{m % 60} 分）' if m > 0 else "（過ぎました。日替わりまで建てません）")
        facts.append(f'刻限 <b>{_e(f["deadline_jst"])}</b><span class="muted">{left}</span>')
    facts.append("指標 " + (f'<b>{_e(f["next_event"])}</b>' if f.get("next_event")
                           else f'<span class="muted">{_e(f.get("event_note") or "—")}</span>'))
    return (f'<section class="panel pair {_STATE_CLASS.get(st, "mixed")}">{head}{price}{note}'
            f'{_chart(b, ch)}{_vol_note(ch)}{_mr_note(b, mr)}{_dxy_note(b, dxy)}{_tp_note(b)}{_fib_note(b, ch)}<div class="fs">{"".join(cells)}</div>{reasons}'
            f'<ul class="facts">{"".join(f"<li>{x}</li>" for x in facts)}</ul></section>')


def _matrix(boards: List[Dict]) -> str:
    """アプリとブリーフの向きを並べる。食い違いは印を付ける。"""
    rows = []
    for b in boards:
        mine = {x["key"]: x.get("view") for x in b.get("factors") or []}
        br = b.get("brief") or {}
        sig = br.get("signals") or {}
        fresh = br.get("age_days") == 0
        tds = []
        for k, _lab in _KEYS:
            a = mine.get(k)
            r = (sig.get(k) or {}).get("view") if fresh else None
            if k == "range":
                pos = (b.get("facts") or {}).get("position")
                a_txt = "—" if pos is None else f"{round(pos * 100)}%"
                a_cls = "fact"
            else:
                a_txt, a_cls = _MARK.get(a, "—"), f"v-{a or 'none'}"
            diff = (k != "range" and a in ("up", "down") and r in ("up", "down") and a != r)
            tds.append(f'<td class="{"diff" if diff else ""}"><span class="{a_cls}">{a_txt}</span>'
                       f'<span class="sep">/</span><span class="v-{r or "none"}">{_MARK.get(r, "—")}</span></td>')
        lean = _BRIEF_LEAN.get(br.get("lean") or "", "—") if fresh else "今日のブリーフなし"
        rows.append(f'<tr><th>{_e(b["pair"])}</th>{"".join(tds)}'
                    f'<td>{_chip(b["verdict"].get("state", "mixed"), b["verdict"].get("label", ""))}</td>'
                    f'<td class="muted">{_e(lean)}</td></tr>')
    head = "".join(f"<th>{lab}</th>" for _k, lab in _KEYS)
    return (f'<section class="panel matrix"><div class="ph"><h2>◆ SIGNAL MATRIX · アプリ / ブリーフ</h2>'
            f'<span class="muted">赤い枠＝向きが食い違い。レンジは アプリ＝20日の位置％ ／ ブリーフ＝向き で、比べていない</span></div><div class="scroll"><table>'
            f'<thead><tr><th></th>{head}<th>アプリの判定</th><th>ブリーフ</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></section>')


def _gauges(boards: List[Dict]) -> str:
    out = []
    for b in boards:
        f, d = b["facts"], b.get("digits", 3)
        used, pos = f.get("used_ratio"), f.get("position")
        uw = 0 if used is None else min(100.0, used / 1.5 * 100)
        ucls = "" if used is None else "ex" if used >= 1.2 else "ca" if used >= 0.9 else "ok"
        above, below = near_levels(f)
        def lv(l):
            dist = "" if l["adr"] is None else f'{"+" if l["adr"] > 0 else ""}{l["adr"]:.2f}'
            return (f'<li><span class="n">{_fmt(l["price"], d)}</span> {_e(l["label"])}'
                    f'<span class="muted"> {dist}日</span></li>')
        today = "今日" if f.get("market_open") else "直近の取引日"
        out.append(
            f'<div class="gauge"><div class="gh"><b>{_e(b["pair"])}</b>'
            f'<span class="muted">{today}の値幅 {"—" if used is None else f"普段の {round(used * 100)}%"}</span></div>'
            f'<div class="bar"><i class="fill {ucls}" style="width:{uw:.1f}%"></i>'
            f'<i class="tick" style="left:60%"></i><i class="tick" style="left:80%"></i></div>'
            f'<div class="scale"><span>0</span><span>0.9 注意</span><span>1.2 除外</span><span>1.5</span></div>'
            f'<div class="gh"><span class="muted">20日のレンジ</span>'
            f'<span class="muted">{"—" if pos is None else f"{round(pos * 100)}%"} ／ 上に {_fmt(f.get("room_up_adr"), 1)} 日分・下に {_fmt(f.get("room_down_adr"), 1)} 日分</span></div>'
            f'<div class="bar pos"><i class="mark" style="left:{0 if pos is None else pos * 100:.1f}%"></i></div>'
            f'<div class="ladder"><ul>{"".join(map(lv, above)) or "<li class=muted>上の水準なし</li>"}</ul>'
            f'<ul>{"".join(map(lv, below)) or "<li class=muted>下の水準なし</li>"}</ul></div></div>')
    return (f'<section class="panel gauges"><div class="ph"><h2>◆ RANGE · 値幅の消化と位置</h2></div>'
            f'{"".join(out)}</section>')


def _plan_box(boards: List[Dict], snapshots: Dict[str, Dict]) -> str:
    parts = []
    for b in boards:
        if b["pair"] not in snapshots:
            continue
        f, d = b["facts"], b.get("digits", 3)
        excluded = b["verdict"].get("state") == "excluded"
        parts.append(
            f'<details class="plan" data-pair="{_e(b["pair"])}"><summary>{_e(b["pair"])} の計画を見る'
            f'<span class="muted">{"今日は除外の日なので、計画は出ません" if excluded else "買い／売りを選び、MT4 の売値（Bid）・買値（Ask）を入れる"}</span>'
            f'</summary><form><div class="dirs"><button type="button" class="on up" data-dir="LONG">'
            f'▲ 買いで見る</button><button type="button" data-dir="SHORT">▼ 売りで見る</button></div>'
            f'<p class="muted howto">MT4 の「気配値表示」の<b>売値（Bid）と買値（Ask）をそのまま</b>'
            f'入れてください。買い・売りどちらでも同じ2つです（買値の方が少し高い）。'
            f'損切り・利確はこちらで計算します。</p>'
            f'<div class="qs"><label>売値（Bid）<input name="bid" inputmode="decimal" '
            f'autocomplete="off" placeholder="{_fmt(f.get("price"), d)}"></label>'
            f'<label>買値（Ask）<input name="ask" inputmode="decimal" autocomplete="off" '
            f'placeholder="{_fmt(f.get("price"), d)}"></label></div>'
            f'<button type="submit" class="go">計画を出す</button></form>'
            f'<div class="result" aria-live="polite"></div></details>')
    if not parts:
        return ""
    acc = next(iter(snapshots.values())).get("account") or {}
    # **資金とリスクは、この端末にだけ残す。** 送らない（このページは誰でも見られる）。
    account = (f'<div class="acct"><label>資金（円）<input id="acct-balance" inputmode="numeric" '
               f'autocomplete="off" placeholder="{_e(acc.get("balance", ""))}"></label>'
               f'<label>1回のリスク（%）<input id="acct-risk" inputmode="decimal" autocomplete="off" '
               f'placeholder="{_e(acc.get("risk_pct", ""))}"></label>'
               f'<p class="muted">空欄なら設定の既定値（資金 {_e(acc.get("balance", "—"))} 円・'
               f'{_e(acc.get("risk_pct", "—"))}%）。上限は {_e(acc.get("max_risk_pct", "—"))}%。'
               f'<b>入れた値はこの端末のブラウザにだけ残り、どこにも送りません。</b></p></div>')
    return (f'<section class="panel planbox"><div class="ph"><h2>◆ PLAN · 自分向けの計画</h2>'
            f'<span class="muted">このページの中で計算します（PC につながらなくても動く）</span></div>'
            f'{account}<div class="plans">{"".join(parts)}</div></section>')


def _tape(rows: List[Dict], digits: Dict[str, int]) -> str:
    if not rows:
        body = '<tr><td colspan="8" class="muted">残したボードはまだありません</td></tr>'
    else:
        trs = []
        def cell(mv):
            if mv is None:
                return '<span class="muted">待ち</span>'
            return f'<span class="{"up" if mv > 0 else "down"}">{"+" if mv > 0 else ""}{mv:.2f}</span>'
        for r in rows:
            seg = cell(r.get("seg_move"))
            res = cell(r.get("move") if r.get("scored") else None)
            st = r.get("state") or "mixed"
            trs.append(
                f'<tr><td class="n">{_jst(r.get("recorded_at"), "%m/%d %H:%M")}</td>'
                f'<td>{_e(_SESSION.get(r.get("session"), r.get("session")))}</td>'
                f'<td>{_e(r.get("pair"))}</td>'
                f'<td><span class="chip {_STATE_CLASS.get(st, "mixed")} sm">{_STATE_MARK.get(st, "■")}</span></td>'
                f'<td class="v-{_e(r.get("brief_lean") or "none")}">{_MARK.get(r.get("brief_lean"), "■" if r.get("brief_lean") == "conflict" else "—")}</td>'
                f'<td class="n">{_fmt(r.get("price"), digits.get(r.get("pair"), 3))}</td>'
                f'<td class="n">{seg}<span class="muted"> {_SEG_END.get(r.get("session"), "")}</span></td>'
                f'<td class="n">{res}</td></tr>')
        body = "".join(trs)
    return (f'<section class="panel tape"><div class="ph"><h2>◆ BOARD TAPE · 残したボードとその後</h2>'
            f'<span class="muted">その後の値動き（普段の値幅を1とする・上が＋）。区切り＝その時間帯の終わり。どちらも刻限の後（翌朝）にまとめて採点</span></div>'
            f'<div class="scroll"><table><thead><tr><th>記録</th><th>時間帯</th><th>銘柄</th>'
            f'<th>判定</th><th>ブリーフ</th><th>価格</th><th>区切りまで</th><th>刻限まで</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div></section>')


def _pick(p: Optional[Dict]) -> str:
    """非円建ての選び方と、候補の表。**当たる確率ではない**と書く。"""
    if not p:
        return ""
    status = p.get("status")
    when = _jst(p.get("picked_at"), "%m/%d %H:%M")
    sess = _SESSION.get(p.get("session"), "")
    head = {"today": f"{_e(p.get('fx_day'))} {sess or 'この'}の時間帯に選んだ3本（{when} 時点の足で）",
            "previous": f"この時間帯の候補の足がまだそろわないので、前の選択（{when}"
                        f"{'・' + sess if sess else ''}）のまま",
            }.get(status, "選べませんでした（候補の足が足りません）")
    chosen = set(p.get("pairs") or [])
    over = [r["pair"] for r in p.get("table") or []
            if r.get("pair") in (p.get("pairs") or []) and r.get("cost_ok") is False]
    warn = ""
    if over:
        warn = (f'<p class="warnline">費用の条件（スプレッドが ATR の上限以下）を満たす銘柄が足りず、'
                f'{"、".join(_e(x) for x in over)} は条件を超えたまま並べています。'
                f'<b>この銘柄はボードが除外になり、計画は止まります。</b></p>')
    rows = []
    for r in (p.get("table") or [])[:8]:
        if "reach" not in r:
            continue
        mark = "◆" if r["pair"] in chosen else ""
        sa = r.get("spread_atr")
        cost = ("" if sa is None else
                f'<span class="{"" if r.get("cost_ok", True) else "down"}">{sa * 100:.0f}%</span>')
        rows.append(f'<tr class="{"on" if mark else ""}"><td>{mark} {_e(r["pair"])}</td>'
                    f'<td class="n"><b>{r["reach"]:.2f}</b></td><td class="n">{r["tp1_pips"]:.1f}</td>'
                    f'<td class="n">{r["room_pips"]:.1f}</td><td class="n">{r["spread_pips"]:.1f}</td>'
                    f'<td class="n">{cost}</td></tr>')
    skipped = [r for r in p.get("table") or [] if "reach" not in r]
    skip = ""
    if skipped:
        skip = ('<p class="muted">候補にしなかった銘柄：'
                + "、".join(f'{_e(r["pair"])}（{_e(r["reason"])}）' for r in skipped[:6])
                + ("…" if len(skipped) > 6 else "") + "</p>")
    table = ""
    if rows:
        table = ('<div class="scroll"><table><thead><tr><th>銘柄</th><th>届きやすさ</th>'
                 '<th>TP1まで(pips)</th><th>刻限までの幅(pips)</th><th>スプレッド(pips)</th><th>スプレッド÷ATR</th></tr></thead>'
                 f'<tbody>{"".join(rows)}</tbody></table></div>')
    return (f'<section class="panel pick"><div class="ph"><h2>◆ PICK · この時間帯の非円建て3本</h2>'
            f'<span class="muted">{head}</span></div>{warn}{table}'
            f'<p class="muted">届きやすさ＝刻限までに約8割が収まった値動きの幅 ÷ 利確（TP1）までの距離'
            f'（損切り・スプレッド・滑り込み。計画と同じ規則）。1を超えるほど TP1 がふだんの幅の内側。'
            f'エンジンの費用の条件（スプレッド÷ATR が上限以下。赤は超過）を満たす銘柄を先に選びます。'
            f'<b>当たる確率でも、向きの予測でもありません。</b>時間帯（東京・ロンドン・NY）の始まりごとに選び直します。</p>{skip}</section>')


def _score(s: Optional[Dict]) -> str:
    if not s:
        return ""
    def line(lab, h):
        if not h["n"]:
            return f'<div class="kv"><span>{lab}</span><b class="muted">まだ0件</b></div>'
        rate = (f'{h["rate"] * 100:.0f}%' if h["rate"] is not None
                else f'{s["min_samples"]}件まで割合は出しません')
        return f'<div class="kv"><span>{lab}</span><b>{h["hits"]} / {h["n"]}</b><em>{rate}</em></div>'
    sw = s.get("excluded_swing")
    return (f'<section class="panel score"><div class="ph"><h2>◆ SCORE · 採点</h2></div>'
            f'<div class="stats"><div><span class="lab">記録</span><b class="big">{s["recorded"]}</b></div>'
            f'<div><span class="lab">採点済み</span><b class="big">{s["scored"]}</b></div></div>'
            f'{line("そろった日が その向きに動いた", s["aligned"])}'
            f'{line("ブリーフの向きが当たった", s["brief"])}'
            f'<div class="kv"><span>除外した日の振れ（平均）</span><b>{"—" if sw is None else f"{sw:.2f} 日分"}</b>'
            f'<em>{s["excluded"]} 件</em></div>'
            f'<p class="muted">コイン投げなら半分。件数が少ないうちは結論を出しません。</p></section>')


def _verify(v: Optional[Dict], score: Optional[Dict]) -> str:
    """測った結果（毎週土曜の検証）。**このボードが「当たる」とは言えないことを、数字のまま出す。**"""
    if not v:
        return ""

    def r(x):
        return "—" if x is None else f"{x:+.2f}R"

    items = []
    lg = v.get("long")
    if lg:
        found = lg.get("winners") or []
        head = ("費用を超える決まりが見つかっています：" + "、".join(_e(x) for x in found)) if found else             "<b>いまのところ、費用を超えて勝てる決まりは見つかっていません。</b>"
        items.append(f'<li>{head}<span class="muted">（約2年の1時間足、{_e(lg.get("first"))}〜{_e(lg.get("last"))}。'
                     f'前半で見つけ・後半で確かめる）</span></li>')
        items.append(f'<li>ボードの判定（テクニカル2つがそろう）：前半 {r(lg.get("lean_a"))}・後半 {r(lg.get("lean_b"))}'
                     f'<span class="muted">（同じ時刻に向きを問わず建てた場合 {r(lg.get("base_a"))}・{r(lg.get("base_b"))}）</span></li>')
    rp = v.get("replay")
    if rp and rp.get("n"):
        items.append(f'<li>直近約8週の再生（{rp.get("boards")}枚）：ボードが「そろった」{rp["n"]}件で {r(rp.get("avg_r"))}'
                     f'<span class="muted">（向きを問わず {r(rp.get("base_r"))}・除外 {rp.get("excluded_share", 0) * 100:.0f}%）</span></li>')
    sw = v.get("swing")
    if sw:
        items.append(f'<li>費用の重さ：日計りで向きを問わず建てると {r((lg or {}).get("base_b"))}、'
                     f'5日持つと {r(sw.get("base_b"))}<span class="muted">（スワップ込み・後半）。保有を延ばすと費用は軽くなるが、'
                     f'向きを当てる力は見つかっていない</span></li>')
    if score:
        b = score.get("brief") or {}
        items.append(f'<li>ブリーフの向きの採点：{b.get("n", 0)} / {score.get("min_samples", 30)} 件'
                     f'<span class="muted">（30件たまったら当たる割合を出す）</span></li>')
    dates = sorted({x.get("date") for x in v.values() if isinstance(x, dict) and x.get("date")})
    return (f'<section class="panel verify"><div class="ph"><h2>◆ VERIFY · 測った結果</h2>'
            f'<span class="muted">毎週土曜に測り直す（最新 {_e(dates[-1] if dates else "—")}）</span></div>'
            f'<ul class="vlist">{"".join(items)}</ul>'
            f'<p class="muted">ボードは「やめる時を知る」「正しい幅と数量で計画する」ための道具です。'
            f'向きの予測には使えないことが、ここまでの検証で分かっています。</p></section>')


def _decision(d: Optional[Dict]) -> str:
    """今日の FX の判断（一番上）。答えを1つ出す。見送りも正しい判断として出す。"""
    if not d:
        return ""
    tone = {"candidate": "go", "skip": "skip", "wait": "skip", "stale": "bad", "closed": "skip"}.get(d["verdict"], "skip")
    body = f'<p class="dlead">{_e(d.get("lead", ""))}</p>'
    if d.get("candidates"):
        rows = "".join(
            f'<li><b>{_e(c["pair"])} {"買い" if c["lean"] == "up" else "売り"}</b>'
            f'<span class="n">　建値 {_fmt(float(c["entry"]), c["digits"]) if c.get("entry") else "—"}'
            f' ／ 損切り {_fmt(float(c["stop"]), c["digits"]) if c.get("stop") else "—"}'
            f' ／ TP1 {_fmt(float(c["tp1"]), c["digits"]) if c.get("tp1") else "—"}</span>'
            f'<br><span class="muted">{_e(c["mr"])}。数字は平常時のスプレッドでの目安。</span></li>'
            for c in d["candidates"])
        body += f'<ul class="dlist">{rows}</ul>'
    if d.get("reasons"):
        body += '<p class="muted">主な理由：' + "、".join(_e(r) for r in d["reasons"]) + '</p>'
    if d.get("skipped"):
        body += ('<p class="muted">見送った候補：' + "、".join(
            f'{_e(x["pair"])}（{_e(x["why"])}）' for x in d["skipped"]) + '</p>')
    return (f'<section class="panel decision {tone}"><div class="ph"><h2>◆ DECISION · 今日のFXの判断</h2>'
            f'<span class="muted">次に見る時刻 <b>{_e(d.get("next_look_label", "—"))}</b></span></div>'
            f'<div class="dtitle">{_e(d["title"])}</div>{body}'
            f'<p class="muted dfoot">決まった順（市場→材料の鮮度→時刻→そろった銘柄と計画→Market Radar）で確かめた結果です。'
            f'検証では費用を超える決まりは見つかっていないので、<b>候補が出ても勝てる根拠ではありません。'
            f'見送りも正しい判断です。</b></p></section>')


def _mr_note(b: Dict, mr: Optional[Dict]) -> str:
    """Market Radar（24時間先）の見方を、その銘柄の欄に1行で。無い・古いなら書かない。"""
    if not mr or mr.get("stale"):
        return ""
    v = (mr.get("views") or {}).get(b["pair"])
    if not v:
        return ""
    st = (b.get("verdict") or {}).get("state")
    lean = "up" if st == "aligned_up" else "down" if st == "aligned_down" else None
    rel = "" if lean is None else ("（ボードと同じ向き）" if v["lean"] == lean else "（ボードと逆の向き）")
    return (f'<div class="lvnote mr"><b>Market Radar・{_e(mr.get("horizon"))}</b>：'
            f'{"▲ 上" if v["lean"] == "up" else "▼ 下"}・確信度 {v["confidence"] * 100:.0f}%'
            f'・{"シグナルあり" if v["signal"] else _e(v.get("status") or "見送り")}{rel}'
            f'<span class="muted">　期間がこのボード（01:00 まで）より長い別アプリの予測。配信後の的中は約54%。</span></div>')


def _health_bar(items: Optional[List[Dict]]) -> str:
    """仕組みの不調があれば、ページの一番上に赤い帯で出す（無ければ何も出さない）。"""
    bad = [x for x in items or [] if x.get("state") == "warn"]
    if not bad:
        return ""
    return ('<div class="healthbar" role="alert">⚠ 仕組みの不調：'
            + "／".join(f'{_e(x["label"])}（{_e(x["detail"])}）' for x in bad)
            + '<span class="muted">　詳しくは FEED 欄</span></div>')


def _health_rows(items: Optional[List[Dict]]) -> str:
    if not items:
        return ""
    mark = {"ok": ("✓", "up"), "warn": ("⚠", "down"), "idle": ("…", "muted")}
    rows = "".join(f'<tr><th>{_e(x["label"])}</th><td class="{mark.get(x["state"], ("?", ""))[1]}">'
                   f'{mark.get(x["state"], ("?", ""))[0]}</td><td>{_e(x["detail"])}</td></tr>' for x in items)
    return (f'<h3 class="sub">仕組みの状態</h3><div class="scroll"><table class="health"><tbody>{rows}</tbody></table></div>'
            f'<p class="muted">✓＝動くはずの時刻に動いた　⚠＝止まっているか失敗　…＝動く時刻ではない（夜・週末）</p>')


def _feed(boards: List[Dict], charts: Dict[str, Dict], snapshots: Dict[str, Dict],
          gen: datetime, valid_minutes: int, health_items: Optional[List[Dict]] = None) -> str:
    rows = []
    for b in boards:
        ch = charts.get(b["pair"]) or {}
        last = ch.get("last_bar_at") or (snapshots.get(b["pair"]) or {}).get("last_bar_at")
        rows.append(f'<tr><th>{_e(b["pair"])}</th><td class="n">{_jst(last, "%m/%d %H:%M")}</td>'
                    f'<td class="n" data-since="{_e(last)}">—</td></tr>')
    return (f'<section class="panel feed"><div class="ph"><h2>◆ FEED · データの鮮度</h2></div>'
            f'<div class="scroll"><table><thead><tr><th>銘柄</th><th>最後の足（M15）</th><th>古さ</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            f'<div class="kv"><span>この写し</span><b>{gen.astimezone(JST):%m/%d %H:%M}</b>'
            f'<em data-since="{gen.isoformat()}">—</em></div>'
            f'<p class="muted">PC が5分ごとに足を取り込み、15分ごとにこのページを作り直します'
            f'（平日 9:05〜翌1:05、朝 8:40）。写しが{valid_minutes}分より古いと計画は出しません。</p>'
            f'{_health_rows(health_items)}</section>')


def _events(evs: List[Dict]) -> str:
    if not evs:
        lis = '<li class="muted">この先36時間に、支援する銘柄の通貨の重要指標はありません（予定表の範囲で）</li>'
    else:
        lis = "".join(
            f'<li><span class="n">{_jst(e["at"], "%m/%d %H:%M")}</span>'
            f'<span class="imp {"hi" if e["impact"] == "High" else "md"}">{_e(e["impact"])}</span>'
            f'<b>{_e(e.get("currency") or "")}</b> {_e(e["title"])}'
            f'<span class="muted cd" data-until="{_e(e["at"])}"></span></li>' for e in evs)
    return (f'<section class="panel events"><div class="ph"><h2>◆ EVENTS · これからの指標</h2>'
            f'<span class="muted">日本時間</span></div><ul class="evs">{lis}</ul></section>')


# ------------------------------------------------------------ 見た目


_CSS = """
:root{color-scheme:dark;--bg:#060a11;--panel:#0b111b;--panel2:#0e1622;--line:#1a2333;--line2:#26344b;
--tx:#d5e1ef;--tx2:#7f8ea5;--mute:#56647a;--up:#2fd47e;--down:#f2555a;--wait:#f5b62b;--acc:#39c2f2;
--mono:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.55 -apple-system,BlinkMacSystemFont,
"Hiragino Sans","Noto Sans JP","Segoe UI",sans-serif}
.muted{color:var(--tx2);font-size:12.5px}.n,.big,.mk,.px{font-family:var(--mono);font-variant-numeric:tabular-nums}
.up{color:var(--up)}.down{color:var(--down)}
.top{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;justify-content:space-between;
padding:max(10px,env(safe-area-inset-top)) 14px 10px;border-bottom:1px solid var(--line);background:#080d16}
.brand{display:flex;gap:10px;align-items:center}
.logo{width:34px;height:34px;border-radius:7px;background:#1b4fd8;color:#fff;display:grid;place-items:center;
font:700 14px var(--mono)}
.brand .t1{font:600 11.5px var(--mono);letter-spacing:.12em;color:var(--tx2)}
.brand .t2{font:700 16px var(--mono);letter-spacing:.04em}
.brand .t3{font-size:12.5px;color:var(--tx2)}
a.kpi.home{color:var(--acc);text-decoration:none;border-color:rgba(57,194,242,.5)}a.kpi.home b{color:var(--acc)}
.kpis{display:flex;flex-wrap:wrap;gap:6px}
.kpi{border:1px solid var(--line);background:var(--panel);border-radius:6px;padding:4px 10px;min-width:92px}
.kpi .l{display:block;font:600 11px var(--mono);letter-spacing:.1em;color:var(--tx2)}
.kpi b{font:700 15px var(--mono)}
.state{font:700 12px var(--mono);padding:2px 8px;border-radius:4px;border:1px solid var(--up);color:var(--up)}
.stamp.stale .state{border-color:var(--down);color:var(--down)}
.stamp.closed .state{border-color:var(--tx2);color:var(--tx2)}.stamp.closed .warn{color:var(--tx2)}
.stamp .warn{display:block;color:var(--down);font-size:12.5px}
.clock{font:700 20px var(--mono)}
.ticker{display:flex;gap:16px;align-items:center;overflow-x:auto;white-space:nowrap;padding:6px 14px;
border-bottom:1px solid var(--line);background:#070c14;font:12px var(--mono)}
.ticker .live{background:#b91c1c;color:#fff;font-weight:700;padding:2px 8px;border-radius:3px;letter-spacing:.08em}
.ticker .it{color:var(--tx2)}.ticker .it b{color:var(--tx)}
main{display:grid;gap:10px;padding:10px 12px max(30px,env(safe-area-inset-bottom));
grid-template-columns:repeat(12,minmax(0,1fr));max-width:1500px;margin:0 auto}
.panel{grid-column:span 12;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px;min-width:0}
@media (min-width:1300px){.pair{grid-column:span 4!important}}
@media (min-width:980px){.pair{grid-column:span 6}.matrix,.gauges{grid-column:span 6}.planbox{grid-column:span 12}
.tape{grid-column:span 12}.decision{grid-column:span 12}.verify{grid-column:span 12}.score,.feed,.events{grid-column:span 4}}
.ph{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px}
.ph h2{margin:0;font:700 12.5px var(--mono);letter-spacing:.1em;color:#a9bbd3}
.chip{font:700 12px var(--mono);padding:2px 9px;border-radius:4px;border:1px solid var(--line2);white-space:nowrap}
.chip.excluded{color:var(--tx2)}.chip.up{color:var(--up);border-color:rgba(47,212,126,.5)}
.chip.down{color:var(--down);border-color:rgba(242,85,90,.5)}.chip.mixed{color:var(--wait);border-color:rgba(245,182,43,.5)}
.chip.sm{padding:0 6px;font-size:12px}.chip.pick{color:#c9a5ff;border-color:rgba(201,165,255,.5)}
.warnline{margin:4px 0 8px;padding:6px 10px;border:1px solid rgba(242,85,90,.45);border-radius:6px;font-size:12.5px;background:rgba(242,85,90,.06)}
.pick tr.on td{background:rgba(57,194,242,.07)}.pick tr.on td:first-child{color:var(--acc);font-weight:700}.chip.watch{color:var(--acc);border-color:rgba(57,194,242,.5)}
.pair{border-top:2px solid var(--line2)}.pair.up{border-top-color:var(--up)}.pair.down{border-top-color:var(--down)}
.pair.mixed{border-top-color:var(--wait)}.pair.excluded{border-top-color:#3a4558}
.pxrow{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}.pxrow .big{font-size:26px;font-weight:700}
.chg{font:600 13px var(--mono)}.note{margin:2px 0}
.chartbox{display:grid;grid-template-columns:minmax(0,1fr) 62px;grid-template-rows:210px 18px;margin:6px 0;
background:var(--panel2);border:1px solid var(--line);border-radius:6px;overflow:hidden}
.plot{position:relative;min-width:0;overflow:hidden}.chart{position:absolute;inset:0;width:100%;height:100%;display:block}
.chart *{vector-effect:non-scaling-stroke}
.chart .g{stroke:#131c2a}.chart .openl{stroke:rgba(57,194,242,.35);stroke-dasharray:2 4}
.chart .cone{fill:rgba(57,194,242,.08);stroke:rgba(57,194,242,.4);stroke-dasharray:3 3}
.chart .lv{stroke:#3a4964;stroke-dasharray:4 3}.chart .lv.support{stroke:rgba(47,212,126,.5)}
.chart .lv.resistance{stroke:rgba(242,85,90,.5)}
.chart .wk{stroke-width:1}.chart .wk.u{stroke:#2a9e63}.chart .wk.d{stroke:#c0464b}
.chart .bd.u{fill:#2fd47e;stroke:#2fd47e;stroke-width:.5}.chart .bd.d{fill:#f2555a;stroke:#f2555a;stroke-width:.5}
.chart .px{fill:none;stroke-width:1;opacity:.35}.chart .px.up{stroke:var(--up)}.chart .px.down{stroke:var(--down)}
.chart .lastl{stroke-dasharray:1 3;opacity:.7}.chart .lastl.up{stroke:var(--up)}.chart .lastl.down{stroke:var(--down)}
.chart .dl{stroke:var(--wait);stroke-dasharray:2 3}
.plot>span{position:absolute;font:10.5px/1 var(--mono);white-space:nowrap;pointer-events:none}
.plot .lvt{right:4px;transform:translateY(-120%);color:var(--tx2);background:rgba(14,22,34,.75);padding:1px 3px;border-radius:2px}
.plot .lvt.support{color:#5fd99a}.plot .lvt.resistance{color:#f08589}
.chart .fib{stroke:rgba(201,165,255,.45);stroke-dasharray:1 3}.chart .fibz{fill:rgba(201,165,255,.07)}
.chart .tp{stroke:rgba(47,212,126,.8);stroke-dasharray:6 3}.chart .sl{stroke:rgba(242,85,90,.8);stroke-dasharray:6 3}
.plot .fbt{left:4px;transform:translateY(-120%);color:#c9a5ff;background:rgba(14,22,34,.75);padding:1px 3px;border-radius:2px}
.plot .tpt{left:50%;transform:translate(-50%,-120%);font-weight:700;background:rgba(14,22,34,.85);padding:1px 4px;border-radius:2px}
.plot .fbt.below{transform:translateY(20%)}.plot .tpt.below{transform:translate(-50%,20%)}
.plot .tpt.tp{color:var(--up)}.plot .tpt.sl{color:var(--down)}
.lvnote{margin:4px 0;padding:7px 10px;border:1px solid var(--line);border-radius:6px;font-size:12.5px;background:var(--panel2)}
.lvnote.tpbox{border-color:rgba(47,212,126,.45)}.lvnote.tpbox.short{border-color:rgba(242,85,90,.45)}
.lvnote b.n{font-family:var(--mono)}.lvnote .fibk{color:#c9a5ff}
.chart .ema20{fill:none;stroke:#39c2f2;stroke-width:1.4;opacity:.85}.chart .ema50{fill:none;stroke:#f5b62b;stroke-width:1.4;opacity:.85}
.plot .emat{left:4px;bottom:3px;color:var(--tx2);background:rgba(14,22,34,.8);padding:1px 4px;border-radius:2px}
.plot .emat .e20{color:#39c2f2}.plot .emat .e50{color:#f5b62b}
.oscbox{display:grid;grid-template-columns:minmax(0,1fr) 62px;grid-template-rows:56px 40px;margin:-7px 0 6px;
background:var(--panel2);border:1px solid var(--line);border-top:0;border-radius:0 0 6px 6px;overflow:hidden}
.oscbox .plot{border-top:1px solid var(--line)}.oscbox .yax{border-top:1px solid var(--line)}
.chart .rsin{fill:rgba(245,182,43,.07)}.chart .rsil{fill:none;stroke:#c9a5ff;stroke-width:1.3}
.chart .mh.u{fill:rgba(47,212,126,.75)}.chart .mh.d{fill:rgba(242,85,90,.75)}
.plot .osct{left:4px;top:3px;color:var(--mute)}.osct i{font-style:normal}
.yax .last.mid{background:#3a4558;color:var(--tx)}
.lvnote.vol .vgauge{position:relative;height:6px;margin:6px 0 4px;border-radius:3px;
background:linear-gradient(90deg,rgba(242,85,90,.35) 0 40%,#26344b 40% 60%,rgba(47,212,126,.35) 60% 100%)}
.lvnote.vol .vgauge i{position:absolute;top:-3px;width:3px;height:12px;background:var(--tx);border-radius:1px;transform:translateX(-50%)}
.lvnote.vol.high{border-color:rgba(47,212,126,.45)}.lvnote.vol.low{border-color:rgba(242,85,90,.35)}
.lvnote.dxy{border-color:rgba(57,194,242,.35)}.dxyrow{display:flex;justify-content:space-between;gap:10px;align-items:center}
.dxysp{width:120px;height:28px;flex:none}.dxysp .all{fill:none;stroke:#56647a;stroke-width:1}
.dxysp .tail{fill:none;stroke-width:2.2}.dxysp .tail.up{stroke:var(--up)}.dxysp .tail.down{stroke:var(--down)}.dxysp .tail.mid{stroke:var(--wait)}
.dxymark{font-size:15px}.dxymark.up{color:var(--up)}.dxymark.down{color:var(--down)}.dxymark.mid{color:var(--wait)}
.warnword{color:var(--wait)}
.healthbar{background:rgba(242,85,90,.12);border-bottom:1px solid rgba(242,85,90,.5);color:#ffb3b5;
padding:7px 14px;font-size:13px}
h3.sub{font:700 11.5px var(--mono);letter-spacing:.08em;color:#a9bbd3;margin:12px 0 4px}
table.health td:nth-child(2){font-weight:700;text-align:center;width:28px}
.vlist{margin:4px 0;padding-left:18px}.vlist li{margin:3px 0}
.decision{border-left:4px solid var(--line2)}.decision.go{border-left-color:var(--up)}
.decision.skip{border-left-color:var(--tx2)}.decision.bad{border-left-color:var(--down)}
.dtitle{font-size:20px;font-weight:700;margin:2px 0 4px}.decision.go .dtitle{color:var(--up)}
.dlead{margin:2px 0 6px}.dlist{margin:4px 0 6px;padding-left:18px}.dlist li{margin:4px 0}.dfoot{margin-top:8px}
.lvnote.mr{border-color:rgba(201,165,255,.35)}
.plot .lvt.below{transform:translateY(20%)}.plot .dlt{bottom:3px;transform:translateX(-105%);color:var(--wait)}
.plot .dot{width:7px;height:7px;border-radius:50%;transform:translate(-50%,-50%);box-shadow:0 0 0 3px rgba(255,255,255,.08)}
.plot .dot.up{background:var(--up)}.plot .dot.down{background:var(--down)}
.yax{position:relative;border-left:1px solid var(--line)}
.yax span{position:absolute;left:5px;transform:translateY(-50%);font:10.5px/1 var(--mono);color:var(--mute)}
.yax .last{left:0;right:0;padding:3px 4px;color:#04110a;font-weight:700;border-radius:0 3px 3px 0;z-index:1}
.yax .last.up{background:var(--up)}.yax .last.down{background:var(--down);color:#1a0405}
.xax{position:relative;grid-column:1;border-top:1px solid var(--line)}
.chartbox::after{content:"";border-top:1px solid var(--line);border-left:1px solid var(--line)}
.xax span{position:absolute;top:3px;transform:translateX(-50%);font:10.5px/1 var(--mono);color:var(--mute)}
.xax span:first-child{transform:none;left:3px!important}
.nochart{padding:30px;text-align:center;color:var(--tx2);border:1px dashed var(--line);border-radius:6px;margin:6px 0}
.fs{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin:6px 0}
.f{border:1px solid var(--line);border-radius:6px;padding:6px 8px;background:var(--panel2);min-width:0}
.f .lab{display:block;font:600 11px var(--mono);letter-spacing:.08em;color:var(--tx2)}
.f .mk{font-size:20px;font-weight:700;line-height:1.2}.f .tx{display:block;font-size:12.5px;color:#b7c4d6}
.f .src{display:block;font-size:11.5px;color:var(--mute);overflow-wrap:anywhere}
.v-up,.v-up .mk{color:var(--up)}.v-down,.v-down .mk{color:var(--down)}.v-neutral,.v-neutral .mk{color:var(--wait)}
.v-none,.v-none .mk,.v-conflict{color:var(--mute)}
.why{font-size:12.5px;border-radius:6px;padding:5px 9px;margin:6px 0;background:var(--panel2)}
.why.ex{border-left:3px solid var(--down)}.why.ca{border-left:3px solid var(--wait)}
.why ul{margin:2px 0 0;padding-left:16px}
.facts{list-style:none;margin:6px 0 0;padding:0;font-size:12.5px;display:flex;gap:4px 16px;flex-wrap:wrap}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{padding:5px 6px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
thead th{font:600 11.5px var(--mono);letter-spacing:.08em;color:var(--tx2)}
.scroll{overflow-x:auto}
.matrix td{font:700 15px var(--mono)}.matrix td .sep{color:var(--mute);margin:0 4px;font-weight:400}
.matrix td.diff{outline:1px solid var(--down);outline-offset:-3px}.matrix .fact{color:var(--tx);font-size:13px}
.gauge{padding:6px 0;border-bottom:1px solid var(--line)}.gauge:last-child{border-bottom:0}
.gh{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;margin:3px 0}
.bar{position:relative;height:10px;border-radius:5px;background:#131c2a;overflow:hidden}
.bar .fill{position:absolute;left:0;top:0;bottom:0;background:var(--acc)}
.bar .fill.ca{background:var(--wait)}.bar .fill.ex{background:var(--down)}
.bar .tick{position:absolute;top:0;bottom:0;width:2px;background:#0a0f18}
.bar.pos{background:linear-gradient(90deg,rgba(242,85,90,.3),#131c2a 30%,#131c2a 70%,rgba(47,212,126,.3))}
.bar .mark{position:absolute;top:-2px;bottom:-2px;width:3px;margin-left:-1.5px;background:var(--tx)}
.scale{display:flex;justify-content:space-between;font:11px var(--mono);color:var(--mute)}
.ladder{display:grid;grid-template-columns:1fr 1fr;gap:8px}.ladder ul{list-style:none;margin:4px 0 0;padding:0;font-size:12px}
.plans{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.plan{border:1px solid var(--line);border-radius:8px;background:var(--panel2);padding:0}
.plan summary{cursor:pointer;list-style:none;padding:10px 12px;font:700 14px var(--mono);border-radius:8px;
border:1px solid #2f6fe0;background:rgba(47,111,224,.12)}
.plan summary::-webkit-details-marker{display:none}.plan summary .muted{display:block;font:400 11.5px sans-serif}
.plan form,.plan .result{padding:0 12px}.plan form{display:grid;gap:8px;margin:10px 0}
.plan .dirs{display:flex;gap:8px}
.plan button{background:var(--panel);color:var(--tx);border:1px solid var(--line2);border-radius:6px;padding:8px 14px;
font:inherit;font-size:14px;cursor:pointer}
.plan .dirs .on.up{border-color:var(--up);color:var(--up)}.plan .dirs .on.down{border-color:var(--down);color:var(--down)}
.plan .qs{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.plan label{display:grid;gap:3px;color:var(--tx2);font-size:12.5px}
.plan input{background:#05080e;color:var(--tx);border:1px solid var(--line2);border-radius:6px;padding:8px 10px;
font:16px var(--mono);min-width:0}
.plan .go{justify-self:start;border-color:#2f6fe0}.plan .howto{margin:0}
.plan .r{margin:0 0 12px;border:1px solid var(--line);border-radius:6px;padding:8px 10px;font-size:12.5px}
.plan .r.ok{border-color:rgba(47,212,126,.5)}.plan .r table td,.plan .r table th{white-space:normal}
.plan .loss{color:var(--down)}.plan .gain{color:var(--up)}.plan code{font-size:11.5px;color:var(--tx2)}
.acct{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:0 0 10px}
.acct label{display:grid;gap:3px;color:var(--tx2);font-size:12.5px}
.acct input{background:#05080e;color:var(--tx);border:1px solid var(--line2);border-radius:6px;padding:8px 10px;
font:16px var(--mono);min-width:0}.acct p{grid-column:1/-1;margin:0}
.score .stats{display:flex;gap:14px}.score .stats .big{font-size:26px;font-weight:700;display:block}
.score .lab,.kv span{color:var(--tx2);font-size:12.5px}
.kv{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;padding:5px 0;border-bottom:1px solid var(--line)}
.kv span{flex:1 1 140px}.kv b{font-family:var(--mono)}.kv em{font-style:normal;color:var(--tx2);font-size:12.5px}
.evs{list-style:none;margin:0;padding:0}.evs li{padding:5px 0;border-bottom:1px solid var(--line);font-size:12.5px;
display:flex;gap:8px;flex-wrap:wrap;align-items:baseline}
.imp{font:700 11px var(--mono);padding:1px 6px;border-radius:3px}.imp.hi{background:rgba(242,85,90,.18);color:var(--down)}
.imp.md{background:rgba(245,182,43,.15);color:var(--wait)}.cd{margin-left:auto}
footer{max-width:1500px;margin:0 auto;padding:0 14px 30px;font-size:12.5px;color:var(--tx2)}
@media (max-width:560px){.fs{grid-template-columns:1fr 1fr}.chartbox{grid-template-rows:200px 18px;grid-template-columns:minmax(0,1fr) 56px}.oscbox{grid-template-columns:minmax(0,1fr) 56px}.osct .wide{display:none}.xax span:nth-child(even){display:none}
.kpis{display:grid;grid-template-columns:1fr 1fr;width:100%}.kpi{min-width:0}.brand .t3{display:none}}
"""

_JS_CALC = Path(__file__).resolve().parent / "static" / "plancalc.cjs"

# ページの動き。時計・残り時間・古さの表示、読み直し、計画。**外から何も読み込まない。**
_JS_UI = r"""
(function(){
var G=new Date(document.body.dataset.generated),MAX=Number(document.body.dataset.valid);
function two(n){return(n<10?'0':'')+n}
function jstNow(){return new Date(Date.now()+9*3600000)}
function left(ms){if(ms<=0)return null;var m=Math.floor(ms/60000);return Math.floor(m/60)+':'+two(m%60)}
function tick(){
 var a=Math.floor((Date.now()-G.getTime())/60000),box=document.querySelector('.stamp');
 box.querySelector('.age').textContent=a<1?'1分以内':a<120?a+'分前':Math.floor(a/60)+'時間前';
 var st=box.querySelector('.state'),w=box.querySelector('.warn');
 if(document.body.dataset.closed==='1'){box.classList.add('closed');box.classList.remove('stale');st.textContent='CLOSED';w.textContent='市場が閉まっています（週末・休場）。値は最後の取引時間のものです。';}
 else if(a>=MAX){box.classList.add('stale');st.textContent='STALE';w.textContent='PC からの更新が止まっているか、取引時間外です。計画は出しません（写しが'+MAX+'分より古い）。';}
 else{box.classList.remove('stale');st.textContent='LIVE';w.textContent='';}
 var j=jstNow(),c=document.querySelector('.clock');
 if(c)c.textContent=two(j.getUTCHours())+':'+two(j.getUTCMinutes())+':'+two(j.getUTCSeconds());
 document.querySelectorAll('[data-until]').forEach(function(el){var t=Date.parse(el.dataset.until);if(isNaN(t))return;
  var l=left(t-Date.now());el.textContent=l?'あと '+l:'過ぎました';});
 document.querySelectorAll('[data-since]').forEach(function(el){var t=Date.parse(el.dataset.since);if(isNaN(t))return;
  var m=Math.floor((Date.now()-t)/60000);el.textContent=m<1?'1分以内':m<120?m+'分前':Math.floor(m/60)+'時間前';});
}
tick();setInterval(tick,1000);
setInterval(function(){if(document.hidden)return;
 if(document.querySelector('details.plan[open]'))return;location.reload();},5*60000);
var KEY='fxboard.account';
function loadAcct(){try{return JSON.parse(localStorage.getItem(KEY)||'{}')||{}}catch(e){return{}}}
function saveAcct(a){try{localStorage.setItem(KEY,JSON.stringify(a))}catch(e){}}
(function(){var a=loadAcct(),b=document.getElementById('acct-balance'),r=document.getElementById('acct-risk');
 if(b&&a.balance)b.value=a.balance;if(r&&a.risk)r.value=a.risk;
 [b,r].forEach(function(el){if(el)el.addEventListener('change',function(){
  saveAcct({balance:(b.value||'').trim(),risk:(r.value||'').trim()});});});})();
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function yen(n){return Number(n).toLocaleString('ja-JP')+'円'}
function show(box,r,acct){
 if(r.status!=='PLAN_OK'){box.innerHTML='<div class="r"><b>計画を出しません</b><ul>'+r.stops.map(function(s){return'<li>'+esc(s.message)+' <code>'+esc(s.code)+'</code></li>'}).join('')+'</ul></div>';return}
 var p=r.plan,rows='<tr><th>建値（'+(p.direction==='LONG'?'買値':'売値')+'）</th><td>'+p.entry+'</td></tr>'
 +'<tr><th>損切り</th><td>'+p.stop+' <span class="muted">'+esc(p.stop_basis)+'</span></td></tr>';
 p.targets.forEach(function(t){rows+='<tr><th>'+t.label+'</th><td>'+t.price+' <span class="muted">費用込み '+t.net_rr+' 倍'+(t.capped_by?'・'+esc(t.capped_by)+'の手前':'')+'</span></td></tr>'});
 rows+='<tr><th>数量</th><td><b>'+p.qty+'</b> <span class="muted">（'+p.units.toLocaleString('ja-JP')+'通貨）</span></td></tr>'
 +'<tr><th>損切りに当たったら</th><td class="loss">−'+yen(p.loss_jpy)+' <span class="muted">許容 '+yen(p.risk_amount_jpy)+'</span></td></tr>';
 p.targets.forEach(function(t){rows+='<tr><th>'+t.label+' に届いたら</th><td class="gain">＋'+yen(t.profit_jpy)+'</td></tr>'});
 rows+='<tr><th>必要証拠金（推定）</th><td>'+yen(p.margin_jpy)+' <span class="muted">資金の '+p.margin_pct+'%</span></td></tr>';
 box.innerHTML='<div class="r ok"><b>計画</b><table>'+rows+'</table>'+(p.notes||[]).map(function(n){return'<p class="muted">'+esc(n)+'</p>'}).join('')
 +'<p class="muted">その向きで建てるならこの幅・この数量、という算術で、儲かる根拠ではありません。滑り 片道0.2pips・円換算の余裕1%は仮定、呼値と証拠金は推定、資金 '+yen(acct.balance)+'・1回のリスク '+acct.risk+'% で計算しました。発注はしません。</p></div>';}
document.querySelectorAll('details.plan').forEach(function(d){
 var pair=d.dataset.pair,dir='LONG',form=d.querySelector('form'),box=d.querySelector('.result');
 d.querySelectorAll('.dirs button').forEach(function(b){b.addEventListener('click',function(){
  dir=b.dataset.dir;d.querySelectorAll('.dirs button').forEach(function(x){x.className=''});
  b.className='on '+(dir==='LONG'?'up':'down');})});
 form.addEventListener('submit',function(e){e.preventDefault();
  var bid=form.bid.value.trim(),ask=form.ask.value.trim();
  if(!/^\d+(\.\d+)?$/.test(bid)||!/^\d+(\.\d+)?$/.test(ask)){box.innerHTML='<div class="r">MT4 の売値（Bid）と買値（Ask）を数字で入れてください</div>';return}
  var snap=(window.SNAPS||{})[pair];
  if(!snap){box.innerHTML='<div class="r">この銘柄は計画の材料がありません</div>';return}
  var b=(document.getElementById('acct-balance')||{}).value||'',r=(document.getElementById('acct-risk')||{}).value||'';
  b=b.replace(/[,，\s]/g,'').trim();r=r.trim();
  if(b&&!/^\d+(\.\d+)?$/.test(b)){box.innerHTML='<div class="r">資金は数字で入れてください（例 1000000）</div>';return}
  if(r&&!/^\d+(\.\d+)?$/.test(r)){box.innerHTML='<div class="r">リスクは数字で入れてください（例 0.5）</div>';return}
  var use=JSON.parse(JSON.stringify(snap));
  if(b)use.account.balance=b;if(r)use.account.risk_pct=r;
  show(box,window.PlanCalc.decide(use,dir,bid,ask,new Date()),{balance:use.account.balance,risk:use.account.risk_pct});});
});
})();
"""


def _json_for_script(data) -> str:
    """script の中に置く JSON。``</`` を閉じタグと読まれないようにする。"""
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _ticker(boards: List[Dict]) -> str:
    items = []
    for b in boards:
        f, d = b["facts"], b.get("digits", 3)
        chg = f.get("today_change_pct")
        c = ("" if chg is None else
             f' <span class="{"up" if chg >= 0 else "down"}">{"+" if chg >= 0 else ""}{chg:.2f}%</span>')
        st = b["verdict"].get("state", "mixed")
        items.append(f'<span class="it">{_e(b["pair"])} <b>{_fmt(f.get("price"), d)}</b>{c} '
                     f'{_STATE_MARK.get(st, "■")}</span>')
    br = next((b.get("brief") for b in boards if b.get("brief")), None)
    if br:
        when = "今日" if br.get("age_days") == 0 else f'{br.get("age_days")}日前'
        items.append(f'<span class="it">ブリーフ <b>{_e(br.get("brief_date"))}</b>（{when}）</span>')
    ex = sum(1 for b in boards if b["verdict"].get("state") == "excluded")
    items.append(f'<span class="it">除外 <b>{ex} / {len(boards)}</b></span>')
    return f'<div class="ticker"><span class="live">● LIVE FEED</span>{"".join(items)}</div>'


def render(boards: List[Dict], generated_at: Optional[datetime] = None,
           note: str = "", snapshots: Optional[Dict[str, Dict]] = None,
           valid_minutes: int = 20, extras: Optional[Dict] = None,
           home: Optional[str] = None) -> str:
    """ダッシュボードを1枚の HTML にする。

    ``snapshots`` があれば計画の欄を、``extras``（dashboard_data.collect）があれば
    チャート・記録・採点・指標の欄を付ける。無くてもページは出る。
    ``home`` は PC の画面から開いた時だけ渡す（分析の画面へ戻る口）。公開ページには付けない。
    """
    gen = generated_at or datetime.now(timezone.utc)
    at = gen.astimezone(JST)
    snapshots = snapshots or {}
    extras = extras or {}
    charts = extras.get("charts") or {}
    digits = {b["pair"]: b.get("digits", 3) for b in boards}

    deadline = next((s.get("deadline") for s in snapshots.values() if s.get("deadline")), None)
    evs = extras.get("events") or []
    nxt = evs[0] if evs else None
    kpis = (f'<div class="kpi stamp" aria-label="{at:%Y/%m/%d %H:%M} 時点の写し">'
            f'<span class="l">SNAPSHOT 写し <span class="state">LIVE</span></span>'
            f'<b>{at:%m/%d %H:%M}</b> <span class="muted"><span class="age">—</span></span>'
            f'<span class="warn"></span></div>'
            f'<div class="kpi"><span class="l">DEADLINE 刻限</span><b>{_jst(deadline)}</b> '
            f'<span class="muted" data-until="{_e(deadline or "")}"></span></div>'
            f'<div class="kpi"><span class="l">NEXT EVENT 指標</span>'
            + (f'<b>{_jst(nxt["at"])}</b> <span class="muted">{_e(nxt.get("currency") or "")} '
               f'<span data-until="{_e(nxt["at"])}"></span></span>' if nxt else '<b>—</b>')
            + '</div><div class="kpi"><span class="l">JST</span><b class="clock">--:--:--</b></div>')
    top = (f'<header class="top"><div class="brand"><span class="logo">FX</span><div>'
           f'<div class="t1">◆ BOARD · PLAN · NO ORDERS</div>'
           f'<div class="t2">FX 判断支援 // ボード</div>'
           f'<div class="t3">予測しない ／ 材料の向きと止める理由を並べる ／ 決めるのはご自身 {_e(note)}</div>'
           f'</div></div><div class="kpis">{kpis}'
           + (f'<a class="kpi home" href="{_e(home)}"><span class="l">PC</span><b>分析の画面へ</b></a>'
              if home else '')
           + '</div></header>')

    # 今日の判断 → 円建ての3本 → 今日の選び方 → 非円建ての3本
    first = [b for b in boards if b.get("group") != "pick"]
    picked = [b for b in boards if b.get("group") == "pick"]
    panels = _decision((extras or {}).get("decision"))
    panels += "".join(_pair_panel(b, charts.get(b["pair"]), (extras or {}).get("dxy"), (extras or {}).get("mr")) for b in first)
    if extras and extras.get("pick"):
        panels += _pick(extras["pick"])
    panels += "".join(_pair_panel(b, charts.get(b["pair"]), (extras or {}).get("dxy"), (extras or {}).get("mr")) for b in picked)
    if not boards:
        panels = '<section class="panel"><p class="muted">ボードがありません。</p></section>'
    if boards:
        panels += _matrix(boards) + _gauges(boards)
    panels += _plan_box(boards, snapshots)
    if extras:
        panels += (_tape(extras.get("tape") or [], digits) + _verify(extras.get("verify"), extras.get("score"))
                   + _score(extras.get("score")))
    panels += _feed(boards, charts, snapshots, gen, valid_minutes, (extras or {}).get("health"))
    if extras:
        panels += _events(evs)

    verdict_note = boards[0]["verdict"].get("note", "") if boards else ""
    # どの銘柄も市場が閉まっている（週末・休場）なら、写しが新しくても LIVE とは出さない
    closed = "1" if boards and not any((b.get("facts") or {}).get("market_open") for b in boards) else "0"
    scripts = ""
    if snapshots:
        scripts = (f"<script>window.SNAPS={_json_for_script(snapshots)};</script>"
                   f"<script>{_JS_CALC.read_text(encoding='utf-8')}</script>")
    scripts += f"<script>{_JS_UI}</script>"
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#060a11">
<title>FX ボード</title>
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="icon" href="icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="FX ボード">
<style>{_CSS}</style></head>
<body data-generated="{gen.isoformat()}" data-valid="{valid_minutes}" data-closed="{closed}">
{top}{_health_bar((extras or {}).get("health"))}{_ticker(boards)}
<main>{panels}</main>
<footer>
<p>{_e(verdict_note)} 建てなかった日を損とは数えません。</p>
<p>予測ではありません。材料の向き（▲▼■）と、機械が止める理由（除外）を並べたものです。
チャートの帯は、実データで測った「この先8時間までに約8割が収まった値動きの幅」で、上下どちらに動くかは示しません。
投資助言ではなく、このページから発注はできません。売買の判断と結果はご自身の責任です。</p>
</footer>{scripts}</body></html>
"""
