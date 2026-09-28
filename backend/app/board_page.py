# -*- coding: utf-8 -*-
"""朝のボードを1枚の静的ページにする（GitHub Pages で外から見るため）。

このページは**誰でも見られる**（リポジトリが公開なので、Pages も公開）。

- 個人の情報・ブリーフ本体へのリンクは載せない。
- 検索エンジンには載せない（noindex）。
- **いつ時点の写しかを一番上に出す。** 生きた画面ではない。
- 「発注」「買い」「売り」とは書かない。ボードの言葉のまま。
- 外から読み込むものは無し（文字・色・帯はすべてこのファイルの中）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Dict, List, Optional

JST = timezone(timedelta(hours=9))

_STATE_CLASS = {"excluded": "excluded", "aligned_up": "up",
                "aligned_down": "down", "mixed": "mixed"}
_STATE_MARK = {"excluded": "✕", "aligned_up": "▲", "aligned_down": "▼", "mixed": "■"}
_BRIEF_LEAN = {"up": "▲ 上方向", "down": "▼ 下方向", "neutral": "■ 中立",
               "conflict": "■ 対立"}
_BRIEF_KEYS = (("trend", "トレンド"), ("momentum", "モメンタム"),
               ("fundamentals", "ファンダ"), ("range", "レンジ位置"))


def _e(x) -> str:
    return escape("" if x is None else str(x))


def _fmt(x: Optional[float], d: int) -> str:
    return "—" if x is None else f"{x:.{d}f}"


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


def _range_bar(f: Dict, d: int) -> str:
    lo, hi = f.get("range_low"), f.get("range_high")
    if lo is None or hi is None:
        return ""
    lo = min(lo, f.get("today_low") if f.get("today_low") is not None else lo)
    hi = max(hi, f.get("today_high") if f.get("today_high") is not None else hi)
    if hi <= lo:
        return ""
    pct = lambda x: max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))  # noqa: E731
    band = ""
    if f.get("today_low") is not None and f.get("today_high") is not None:
        a, b = pct(f["today_low"]), pct(f["today_high"])
        band = f'<span class="band" style="left:{a:.1f}%;width:{max(0.8, b - a):.1f}%"></span>'
    now = (f'<span class="now" style="left:{pct(f["price"]):.1f}%"></span>'
           if f.get("price") is not None else "")
    which = "今日" if f.get("market_open") else "直近の取引日"
    return (f'<div class="rbar"><div class="track">{band}{now}</div>'
            f'<div class="ends"><span>{_fmt(lo, d)}</span>'
            f'<span class="muted">帯={which}の高安 ／ 縦線=いまの値</span>'
            f'<span>{_fmt(hi, d)}</span></div></div>')


def _card(b: Dict) -> str:
    f, v, d = b["facts"], b["verdict"], b.get("digits", 3)
    st = v.get("state", "mixed")
    out: List[str] = [f'<section class="card {_STATE_CLASS.get(st, "mixed")}">']
    chg = f.get("today_change_pct")
    chg_html = ("" if chg is None else
                f' <span class="chg {"up" if chg >= 0 else "down"}">'
                f'{"+" if chg >= 0 else ""}{chg:.2f}%</span>')
    out.append(f'<header><span class="sym">{_e(b["pair"])}</span>'
               f'<span class="vd {_STATE_CLASS.get(st, "mixed")}">'
               f'{_STATE_MARK.get(st, "■")} {_e(v.get("label"))}</span>'
               f'<span class="px">{_fmt(f.get("price"), d)}{chg_html}</span></header>')
    if f.get("day_note"):
        out.append(f'<p class="muted">{_e(f["day_note"])}</p>')

    cells = []
    for fac in b.get("factors") or []:
        src = ""
        if fac.get("key") == "fundamentals":
            src = (f'<span class="src">出典: {_e(fac.get("source") or "なし")}'
                   f'{"（" + _e(fac.get("as_of")) + "）" if fac.get("as_of") else ""}'
                   f'{" ／ 古いので判定に数えていません" if fac.get("stale") else ""}</span>')
        cells.append(f'<div class="f v-{_e(fac.get("view"))}"><span class="lab">'
                     f'{_e(fac.get("label"))}</span><span class="mk">{_e(fac.get("mark"))}'
                     f'</span><span class="tx">{_e(fac.get("text"))}</span>{src}</div>')
    pos = f.get("position")
    where = ("20日の高安が取れていません" if pos is None else
             "上の端に近い" if pos >= 0.8 else "下の端に近い" if pos <= 0.2 else "中ほど")
    cells.append(f'<div class="f v-fact"><span class="lab">レンジ位置（20日）</span>'
                 f'<span class="mk">{"—" if pos is None else f"{round(pos * 100)}%"}</span>'
                 f'<span class="tx">{where}</span><span class="src">上に '
                 f'{_fmt(f.get("room_up_adr"), 1)} 日分 ／ 下に '
                 f'{_fmt(f.get("room_down_adr"), 1)} 日分</span></div>')
    out.append(f'<div class="fs">{"".join(cells)}</div>')

    br = b.get("brief")
    if br:
        when = ("今日" if br.get("age_days") == 0 else
                f'{br.get("age_days")}日前' if br.get("age_days") is not None else "日付不明")
        sig = " ".join(
            f'<span class="v-{_e((br.get("signals") or {}).get(k, {}).get("view", "none"))}">'
            f'{lab} {_e((br.get("signals") or {}).get(k, {}).get("mark", "—"))}</span>'
            for k, lab in _BRIEF_KEYS)
        out.append(f'<div class="brief"><span class="lab">FXモーニングブリーフ</span> '
                   f'<b>{_e(_BRIEF_LEAN.get(br.get("lean") or "", "—"))}'
                   f'{"（除外・見送り）" if br.get("excluded") else ""}</b> '
                   f'<span class="muted">{_e(br.get("brief_date"))}（{when}）</span>'
                   f'<div class="bs">{sig}</div></div>')

    for title, items, cls in (("除外の理由", v.get("exclude"), "ex"),
                              ("気をつけること", v.get("cautions"), "ca")):
        if items:
            lis = "".join(f"<li>{_e(x)}</li>" for x in items)
            out.append(f'<div class="why {cls}"><b>{title}</b><ul>{lis}</ul></div>')

    out.append(_range_bar(f, d))
    above, below = near_levels(f)
    def lv(l):
        dist = "" if l["adr"] is None else f'（{"+" if l["adr"] > 0 else ""}{l["adr"]:.2f} 日分）'
        return f'<li><b>{_fmt(l["price"], d)}</b> {_e(l["label"])}<span class="muted">{dist}</span></li>'
    if above or below:
        out.append('<div class="lv"><div><span class="lab">上の水準</span><ul>'
                   + ("".join(map(lv, above)) or '<li class="muted">なし</li>')
                   + '</ul></div><div><span class="lab">下の水準</span><ul>'
                   + ("".join(map(lv, below)) or '<li class="muted">なし</li>')
                   + "</ul></div></div>")

    used = f.get("used_ratio")
    lvl = ("" if used is None else "ex" if used >= 1.2 else "ca" if used >= 0.9 else "")
    facts = [f'{"今日" if f.get("market_open") else "直近の取引日"}の値幅 '
             f'<b class="u {lvl}">{"—" if used is None else f"普段の {round(used * 100)}%"}</b>'
             f'<span class="muted">（{_fmt(f.get("today_low"), d)}〜{_fmt(f.get("today_high"), d)}'
             f' ／ 普段は {_fmt(f.get("adr"), d)}）</span>']
    if f.get("market_open") and f.get("deadline_jst"):
        m = f.get("minutes_to_deadline")
        left = ("" if m is None else
                f'（あと {m // 60} 時間{m % 60} 分）' if m > 0 else "（過ぎました。日替わりまで建てません）")
        facts.append(f'手仕舞いの刻限 <b>{_e(f["deadline_jst"])}</b><span class="muted">{left}</span>')
    facts.append("指標 " + (f'<b>{_e(f["next_event"])}</b>' if f.get("next_event")
                           else f'<span class="muted">{_e(f.get("event_note") or "—")}</span>'))
    out.append('<ul class="facts">' + "".join(f"<li>{x}</li>" for x in facts) + "</ul>")
    out.append("</section>")
    return "".join(out)


_CSS = """
:root{--bg:#0f1217;--panel:#171b22;--line:#2a313d;--tx:#e6eaf2;--tx2:#98a2b5;
--up:#2fb37a;--down:#e0575f;--wait:#d9a13b;--none:#6b7589}
@media (prefers-color-scheme:light){:root{--bg:#f5f7fa;--panel:#fff;--line:#dce2ea;
--tx:#141922;--tx2:#5d6675;--up:#0b7a55;--down:#b0362a;--wait:#9a6408;--none:#7a8494}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.6 -apple-system,
BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP","Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:max(18px,env(safe-area-inset-top)) 16px
max(40px,env(safe-area-inset-bottom))}
h1{font-size:20px;margin:0 0 2px}.muted{color:var(--tx2);font-size:12.5px}
.stamp{border:1px solid var(--line);border-left:3px solid var(--wait);background:var(--panel);
padding:8px 12px;border-radius:6px;margin:10px 0 14px;font-size:13px}
.cards{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.card.excluded{border-left:4px solid var(--none)}.card.up{border-left:4px solid var(--up)}
.card.down{border-left:4px solid var(--down)}.card.mixed{border-left:4px solid var(--wait)}
header{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.sym{font-size:18px;font-weight:700}.px{margin-left:auto;font-weight:600;font-variant-numeric:tabular-nums}
.chg{font-size:12px;font-weight:500}.chg.up{color:var(--up)}.chg.down{color:var(--down)}
.vd{font-weight:700;font-size:14px;padding:1px 9px;border-radius:4px;border:1px solid var(--line)}
.vd.excluded{color:var(--tx2)}.vd.up{color:var(--up)}.vd.down{color:var(--down)}.vd.mixed{color:var(--wait)}
.fs{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:10px 0}
.f{border:1px solid var(--line);border-radius:8px;padding:8px 10px;display:flex;flex-direction:column;gap:2px;min-width:0}
.lab{color:var(--tx2);font-size:11.5px}.mk{font-size:22px;font-weight:700;line-height:1.2}
.tx{font-size:12.5px}.src{color:var(--tx2);font-size:11px;overflow-wrap:anywhere}
.v-up .mk,.bs .v-up{color:var(--up)}.v-down .mk,.bs .v-down{color:var(--down)}
.v-neutral .mk,.bs .v-neutral{color:var(--wait)}.v-none .mk,.bs .v-none{color:var(--none)}
.brief{border:1px dashed var(--line);border-radius:8px;padding:8px 10px;margin:6px 0;font-size:13px}
.bs{display:flex;flex-wrap:wrap;gap:2px 12px;font-size:12.5px;margin-top:3px}
.why{font-size:13px;border-radius:6px;padding:6px 10px;margin:6px 0}
.why.ex{border:1px solid var(--none)}.why.ca{border:1px solid var(--wait)}
ul{margin:2px 0 0;padding-left:18px}li{font-size:12.5px}
.rbar{margin:12px 0 4px}.track{position:relative;height:14px;border-radius:7px;
background:linear-gradient(90deg,rgba(224,87,95,.25),rgba(128,128,128,.15) 30%,rgba(128,128,128,.15) 70%,rgba(47,179,122,.25))}
.band{position:absolute;top:3px;bottom:3px;border-radius:4px;background:rgba(74,122,212,.75)}
.now{position:absolute;top:-4px;bottom:-4px;width:3px;margin-left:-1.5px;background:var(--tx);border-radius:2px}
.ends{display:flex;justify-content:space-between;gap:6px;font-size:11.5px;margin-top:4px;font-variant-numeric:tabular-nums}
.lv{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:6px 0}
.facts{list-style:none;padding:0;margin:8px 0 2px;font-size:13px}.facts li{font-size:13px;padding:2px 0}
.u.ca{color:var(--wait)}.u.ex{color:var(--down)}
footer{margin-top:18px;font-size:12px;color:var(--tx2)}
@media (max-width:480px){.cards{grid-template-columns:1fr}}
"""


def render(boards: List[Dict], generated_at: Optional[datetime] = None,
           note: str = "") -> str:
    """ボードの一覧を1枚の HTML にする。"""
    at = (generated_at or datetime.now(timezone.utc)).astimezone(JST)
    cards = "".join(_card(b) for b in boards) or '<p class="muted">ボードがありません。</p>'
    verdict_note = boards[0]["verdict"].get("note", "") if boards else ""
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<meta name="color-scheme" content="dark light">
<title>朝のボード</title>
<style>{_CSS}</style></head>
<body><main>
<h1>朝のボード</h1>
<div class="stamp"><b>{at:%Y/%m/%d %H:%M} 時点の写しです</b>（日本時間）。
生きた画面ではありません。平日の朝に1回だけ作り直します。{_e(note)}</div>
<div class="cards">{cards}</div>
<footer>
<p>{_e(verdict_note)} 建てなかった日を損とは数えません。</p>
<p>予測ではありません。材料の向き（▲▼■）と、機械が止める理由（除外）を並べたものです。
投資助言ではなく、このページから発注はできません。売買の判断と結果はご自身の責任です。</p>
</footer>
</main></body></html>
"""
