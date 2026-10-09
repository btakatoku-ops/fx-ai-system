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
    end_min = 19 * 60
    if ch.get("deadline"):
        end_min = max(60, int((datetime.fromisoformat(ch["deadline"]) - start).total_seconds() // 60))
    end_min = max(end_min, bars[-1][0] + 15)
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
            if t > end_min:
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
    dlx = X(end_min)
    svg.append(f'<line class="dl" x1="{dlx:.1f}" y1="0" x2="{dlx:.1f}" y2="{H}"/>')
    svg.append("</svg>")
    html.append(f'<span class="dlt" style="left:{px(dlx)}">刻限</span>')
    html.append(f'<span class="dot {cls}" style="left:{px(X(last_t + 7.5))};top:{py(Y(last_c))}"></span>')
    yax.append(f'<span class="last {cls}" style="top:{py(Y(last_c))}">{_fmt(last_c, d)}</span>')
    label = f'{_e(b["pair"])} のその日の値動き（15分足）。最後 {_fmt(last_c, d)}'
    return (f'<div class="chartbox" role="img" aria-label="{label}"><div class="plot">{"".join(svg)}'
            f'{"".join(html)}</div><div class="yax">{"".join(yax)}</div>'
            f'<div class="xax">{"".join(xax)}</div></div>')


# ------------------------------------------------------------ 欄


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


def _pair_panel(b: Dict, ch: Optional[Dict]) -> str:
    f, v, d = b["facts"], b["verdict"], b.get("digits", 3)
    st = v.get("state", "mixed")
    chg = f.get("today_change_pct")
    chg_html = ("" if chg is None else
                f'<span class="chg {"up" if chg >= 0 else "down"}">'
                f'{"+" if chg >= 0 else ""}{chg:.2f}%</span>')
    watch = ('<span class="chip sm watch">様子を見る・計画なし</span> '
             if b.get("tier") == "watch" else "")
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
            f'{_chart(b, ch)}<div class="fs">{"".join(cells)}</div>{reasons}'
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


def _feed(boards: List[Dict], charts: Dict[str, Dict], snapshots: Dict[str, Dict],
          gen: datetime, valid_minutes: int) -> str:
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
            f'（平日 9:05〜翌1:05、朝 8:40）。写しが{valid_minutes}分より古いと計画は出しません。</p></section>')


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
.muted{color:var(--tx2);font-size:12px}.n,.big,.mk,.px{font-family:var(--mono);font-variant-numeric:tabular-nums}
.up{color:var(--up)}.down{color:var(--down)}
.top{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;justify-content:space-between;
padding:max(10px,env(safe-area-inset-top)) 14px 10px;border-bottom:1px solid var(--line);background:#080d16}
.brand{display:flex;gap:10px;align-items:center}
.logo{width:34px;height:34px;border-radius:7px;background:#1b4fd8;color:#fff;display:grid;place-items:center;
font:700 14px var(--mono)}
.brand .t1{font:600 10.5px var(--mono);letter-spacing:.12em;color:var(--tx2)}
.brand .t2{font:700 16px var(--mono);letter-spacing:.04em}
.brand .t3{font-size:11.5px;color:var(--tx2)}
a.kpi.home{color:var(--acc);text-decoration:none;border-color:rgba(57,194,242,.5)}a.kpi.home b{color:var(--acc)}
.kpis{display:flex;flex-wrap:wrap;gap:6px}
.kpi{border:1px solid var(--line);background:var(--panel);border-radius:6px;padding:4px 10px;min-width:92px}
.kpi .l{display:block;font:600 10px var(--mono);letter-spacing:.1em;color:var(--tx2)}
.kpi b{font:700 15px var(--mono)}
.state{font:700 11px var(--mono);padding:2px 8px;border-radius:4px;border:1px solid var(--up);color:var(--up)}
.stamp.stale .state{border-color:var(--down);color:var(--down)}
.stamp .warn{display:block;color:var(--down);font-size:11.5px}
.clock{font:700 20px var(--mono)}
.ticker{display:flex;gap:16px;align-items:center;overflow-x:auto;white-space:nowrap;padding:6px 14px;
border-bottom:1px solid var(--line);background:#070c14;font:12px var(--mono)}
.ticker .live{background:#b91c1c;color:#fff;font-weight:700;padding:2px 8px;border-radius:3px;letter-spacing:.08em}
.ticker .it{color:var(--tx2)}.ticker .it b{color:var(--tx)}
main{display:grid;gap:10px;padding:10px 12px max(30px,env(safe-area-inset-bottom));
grid-template-columns:repeat(12,minmax(0,1fr));max-width:1500px;margin:0 auto}
.panel{grid-column:span 12;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px;min-width:0}
@media (min-width:980px){.pair{grid-column:span 6}.matrix,.gauges{grid-column:span 6}.planbox{grid-column:span 12}
.tape{grid-column:span 8}.score{grid-column:span 4}.feed,.events{grid-column:span 6}}
.ph{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px}
.ph h2{margin:0;font:700 11.5px var(--mono);letter-spacing:.1em;color:#a9bbd3}
.chip{font:700 12px var(--mono);padding:2px 9px;border-radius:4px;border:1px solid var(--line2);white-space:nowrap}
.chip.excluded{color:var(--tx2)}.chip.up{color:var(--up);border-color:rgba(47,212,126,.5)}
.chip.down{color:var(--down);border-color:rgba(242,85,90,.5)}.chip.mixed{color:var(--wait);border-color:rgba(245,182,43,.5)}
.chip.sm{padding:0 6px;font-size:11px}.chip.watch{color:var(--acc);border-color:rgba(57,194,242,.5)}
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
.plot>span{position:absolute;font:10px/1 var(--mono);white-space:nowrap;pointer-events:none}
.plot .lvt{right:4px;transform:translateY(-120%);color:var(--tx2);background:rgba(14,22,34,.75);padding:1px 3px;border-radius:2px}
.plot .lvt.support{color:#5fd99a}.plot .lvt.resistance{color:#f08589}
.plot .lvt.below{transform:translateY(20%)}.plot .dlt{bottom:3px;transform:translateX(-105%);color:var(--wait)}
.plot .dot{width:7px;height:7px;border-radius:50%;transform:translate(-50%,-50%);box-shadow:0 0 0 3px rgba(255,255,255,.08)}
.plot .dot.up{background:var(--up)}.plot .dot.down{background:var(--down)}
.yax{position:relative;border-left:1px solid var(--line)}
.yax span{position:absolute;left:5px;transform:translateY(-50%);font:10px/1 var(--mono);color:var(--mute)}
.yax .last{left:0;right:0;padding:3px 4px;color:#04110a;font-weight:700;border-radius:0 3px 3px 0;z-index:1}
.yax .last.up{background:var(--up)}.yax .last.down{background:var(--down);color:#1a0405}
.xax{position:relative;grid-column:1/-1;border-top:1px solid var(--line)}
.xax span{position:absolute;top:3px;transform:translateX(-50%);font:10px/1 var(--mono);color:var(--mute)}
.xax span:first-child{transform:none;left:3px!important}
.nochart{padding:30px;text-align:center;color:var(--tx2);border:1px dashed var(--line);border-radius:6px;margin:6px 0}
.fs{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin:6px 0}
.f{border:1px solid var(--line);border-radius:6px;padding:6px 8px;background:var(--panel2);min-width:0}
.f .lab{display:block;font:600 10px var(--mono);letter-spacing:.08em;color:var(--tx2)}
.f .mk{font-size:20px;font-weight:700;line-height:1.2}.f .tx{display:block;font-size:11.5px;color:#b7c4d6}
.f .src{display:block;font-size:10.5px;color:var(--mute);overflow-wrap:anywhere}
.v-up,.v-up .mk{color:var(--up)}.v-down,.v-down .mk{color:var(--down)}.v-neutral,.v-neutral .mk{color:var(--wait)}
.v-none,.v-none .mk,.v-conflict{color:var(--mute)}
.why{font-size:12.5px;border-radius:6px;padding:5px 9px;margin:6px 0;background:var(--panel2)}
.why.ex{border-left:3px solid var(--down)}.why.ca{border-left:3px solid var(--wait)}
.why ul{margin:2px 0 0;padding-left:16px}
.facts{list-style:none;margin:6px 0 0;padding:0;font-size:12.5px;display:flex;gap:4px 16px;flex-wrap:wrap}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{padding:5px 6px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
thead th{font:600 10.5px var(--mono);letter-spacing:.08em;color:var(--tx2)}
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
.scale{display:flex;justify-content:space-between;font:10px var(--mono);color:var(--mute)}
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
.plan label{display:grid;gap:3px;color:var(--tx2);font-size:11.5px}
.plan input{background:#05080e;color:var(--tx);border:1px solid var(--line2);border-radius:6px;padding:8px 10px;
font:16px var(--mono);min-width:0}
.plan .go{justify-self:start;border-color:#2f6fe0}.plan .howto{margin:0}
.plan .r{margin:0 0 12px;border:1px solid var(--line);border-radius:6px;padding:8px 10px;font-size:12.5px}
.plan .r.ok{border-color:rgba(47,212,126,.5)}.plan .r table td,.plan .r table th{white-space:normal}
.plan .loss{color:var(--down)}.plan .gain{color:var(--up)}.plan code{font-size:10.5px;color:var(--tx2)}
.acct{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:0 0 10px}
.acct label{display:grid;gap:3px;color:var(--tx2);font-size:11.5px}
.acct input{background:#05080e;color:var(--tx);border:1px solid var(--line2);border-radius:6px;padding:8px 10px;
font:16px var(--mono);min-width:0}.acct p{grid-column:1/-1;margin:0}
.score .stats{display:flex;gap:14px}.score .stats .big{font-size:26px;font-weight:700;display:block}
.score .lab,.kv span{color:var(--tx2);font-size:11.5px}
.kv{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;padding:5px 0;border-bottom:1px solid var(--line)}
.kv span{flex:1 1 140px}.kv b{font-family:var(--mono)}.kv em{font-style:normal;color:var(--tx2);font-size:11.5px}
.evs{list-style:none;margin:0;padding:0}.evs li{padding:5px 0;border-bottom:1px solid var(--line);font-size:12.5px;
display:flex;gap:8px;flex-wrap:wrap;align-items:baseline}
.imp{font:700 10px var(--mono);padding:1px 6px;border-radius:3px}.imp.hi{background:rgba(242,85,90,.18);color:var(--down)}
.imp.md{background:rgba(245,182,43,.15);color:var(--wait)}.cd{margin-left:auto}
footer{max-width:1500px;margin:0 auto;padding:0 14px 30px;font-size:11.5px;color:var(--tx2)}
@media (max-width:560px){.fs{grid-template-columns:1fr 1fr}.chartbox{grid-template-rows:200px 18px;grid-template-columns:minmax(0,1fr) 56px}.xax span:nth-child(even){display:none}
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
 box.querySelector('.age').textContent=a<1?'1分以内':a+'分前';
 var st=box.querySelector('.state'),w=box.querySelector('.warn');
 if(a>=MAX){box.classList.add('stale');st.textContent='STALE';w.textContent='PC からの更新が止まっているか、取引時間外です。計画は出しません（写しが'+MAX+'分より古い）。';}
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

    panels = "".join(_pair_panel(b, charts.get(b["pair"])) for b in boards) or \
        '<section class="panel"><p class="muted">ボードがありません。</p></section>'
    if boards:
        panels += _matrix(boards) + _gauges(boards)
    panels += _plan_box(boards, snapshots)
    if extras:
        panels += _tape(extras.get("tape") or [], digits) + _score(extras.get("score"))
    panels += _feed(boards, charts, snapshots, gen, valid_minutes)
    if extras:
        panels += _events(evs)

    verdict_note = boards[0]["verdict"].get("note", "") if boards else ""
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
<body data-generated="{gen.isoformat()}" data-valid="{valid_minutes}">
{top}{_ticker(boards)}
<main>{panels}</main>
<footer>
<p>{_e(verdict_note)} 建てなかった日を損とは数えません。</p>
<p>予測ではありません。材料の向き（▲▼■）と、機械が止める理由（除外）を並べたものです。
チャートの帯は、実データで測った「この先8時間までに約8割が収まった値動きの幅」で、上下どちらに動くかは示しません。
投資助言ではなく、このページから発注はできません。売買の判断と結果はご自身の責任です。</p>
</footer>{scripts}</body></html>
"""
