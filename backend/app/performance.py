# -*- coding: utf-8 -*-
"""成績の集計。

**この段階でいちばん危ないのは、意味のない数字に意味があるように見せること。**
だから集計側に歯止めを入れてある。

1. 件数が足りない区分では **勝率を名乗らない**（None を返す）。
2. 勝率は必ず **Wilson 信頼区間** と一緒に返す。点の値だけを見せない。
3. 基準（同じ規則で全部の足を建てた場合）との差を出す。**基準と差が無いなら、
   選別に意味は無い。**

「よく見える数字」を作らないために、丸めや都合のよい期間の切り出しはしない。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ------------------------------------------------------------------ 統計


def wilson(wins: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """二項比率の Wilson 信頼区間。

    単純な p ± 1.96·√(p(1-p)/n) は件数が少ないときに区間が [0,1] を
    はみ出したり、0勝のときに幅が0になったりする。Wilson はそうならない。
    """
    if n <= 0:
        return (0.0, 1.0)
    p = wins / n
    d = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (centre - spread) / d), min(1.0, (centre + spread) / d))


def two_proportion_z(w1: int, n1: int, w2: int, n2: int) -> Optional[float]:
    """2つの比率の差の z 値。正規近似。

    近似なので、件数が少ないときは返さない。**近似が成り立たない場所で
    p 値めいた数字を出すほうが、出さないより害がある。**
    """
    if n1 < 10 or n2 < 10:
        return None
    p1, p2 = w1 / n1, w2 / n2
    p = (w1 + w2) / (n1 + n2)
    denom = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if denom == 0:
        return None
    return (p1 - p2) / denom


def two_sided_p(z: float) -> float:
    """標準正規の両側 p 値。"""
    return math.erfc(abs(z) / math.sqrt(2.0))


# ------------------------------------------------------------------ 集計


@dataclass
class Stats:
    """ひと区分ぶんの成績。

    win_rate が None なのは「計算できなかった」のではなく、
    **件数が足りないので名乗らない**という意味。
    """

    label: str
    n: int = 0
    wins: int = 0
    losses: int = 0
    timeouts: int = 0
    win_rate: Optional[float] = None
    win_rate_low: Optional[float] = None
    win_rate_high: Optional[float] = None
    avg_r: Optional[float] = None
    total_r: float = 0.0
    expectancy_r: Optional[float] = None
    max_drawdown_r: Optional[float] = None
    reliable: bool = False
    note: str = ""

    def as_dict(self) -> Dict:
        return {
            "label": self.label, "n": self.n,
            "wins": self.wins, "losses": self.losses, "timeouts": self.timeouts,
            "win_rate": self.win_rate,
            "win_rate_ci": [self.win_rate_low, self.win_rate_high],
            "avg_r": self.avg_r, "total_r": round(self.total_r, 3),
            "expectancy_r": self.expectancy_r,
            "max_drawdown_r": self.max_drawdown_r,
            "reliable": self.reliable, "note": self.note,
        }


def _max_drawdown(r_values: Sequence[float]) -> float:
    """R の累積曲線の最大の落ち込み。順番どおりに見る。"""
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for r in r_values:
        equity += r
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def summarize(trades: Iterable, label: str = "all",
              min_samples: int = 30, z: float = 1.96) -> Stats:
    """建玉の並びから成績を出す。

    trades の各要素は outcome（WIN/LOSS/TIMEOUT）と r_multiple を
    持っていればよい。時間切れは勝ちにも負けにも数えず、R だけ足す。
    """
    items = list(trades)
    s = Stats(label=label, n=len(items))
    if not items:
        s.note = "該当なし"
        return s

    r_values = [float(t.r_multiple) for t in items]
    s.wins = sum(1 for t in items if t.outcome == "WIN")
    s.losses = sum(1 for t in items if t.outcome == "LOSS")
    s.timeouts = sum(1 for t in items if t.outcome == "TIMEOUT")
    s.total_r = sum(r_values)
    s.avg_r = round(s.total_r / len(items), 4)
    s.expectancy_r = s.avg_r          # 1件あたりの期待 R。同じもの
    s.max_drawdown_r = round(_max_drawdown(r_values), 3)

    decided = s.wins + s.losses
    low, high = wilson(s.wins, decided, z) if decided else (0.0, 1.0)
    s.win_rate_low, s.win_rate_high = round(low, 4), round(high, 4)

    if decided >= min_samples:
        s.win_rate = round(s.wins / decided, 4)
        s.reliable = True
        s.note = f"決着 {decided} 件（時間切れ {s.timeouts} 件は勝敗に数えない）"
    else:
        s.note = (f"決着が {decided} 件しかありません（{min_samples} 件必要）。"
                  f"勝率は名乗りません")
    return s


def by_band(trades: Iterable, bands: Sequence[Dict],
            min_samples: int = 30, z: float = 1.96) -> List[Stats]:
    """点数の区分ごとにまとめる。**点数が結果を分けているかを見るため。**"""
    items = list(trades)
    out: List[Stats] = []
    for b in bands:
        sel = [t for t in items if b["min"] <= t.score <= b["max"]]
        out.append(summarize(sel, label=b["label"], min_samples=min_samples, z=z))
    return out


@dataclass
class Comparison:
    """選別した組と基準の比較。"""

    label: str
    selected: Stats
    baseline: Stats
    win_rate_diff: Optional[float] = None
    avg_r_diff: Optional[float] = None
    z: Optional[float] = None
    p_value: Optional[float] = None
    verdict: str = ""

    def as_dict(self) -> Dict:
        return {
            "label": self.label,
            "selected": self.selected.as_dict(),
            "baseline": self.baseline.as_dict(),
            "win_rate_diff": self.win_rate_diff,
            "avg_r_diff": self.avg_r_diff,
            "z": self.z, "p_value": self.p_value,
            "verdict": self.verdict,
        }


def compare(selected: Stats, baseline: Stats, label: str = "選別 vs 基準") -> Comparison:
    """選別が基準より良いかを見る。

    **差が出ないことを失敗として扱わない。** 差が無いと分かることが、
    この検証でいちばん価値のある結果になりうる。
    """
    c = Comparison(label=label, selected=selected, baseline=baseline)
    sd = selected.wins + selected.losses
    bd = baseline.wins + baseline.losses

    if selected.avg_r is not None and baseline.avg_r is not None:
        c.avg_r_diff = round(selected.avg_r - baseline.avg_r, 4)

    z = two_proportion_z(selected.wins, sd, baseline.wins, bd)
    if z is None:
        c.verdict = "件数が足りず、比較できません"
        return c

    c.z = round(z, 3)
    c.p_value = round(two_sided_p(z), 4)
    c.win_rate_diff = round(selected.wins / sd - baseline.wins / bd, 4)

    if not selected.reliable:
        c.verdict = (f"選別側の決着が {sd} 件しかありません。"
                     f"差が見えても、まだ確かめたことになりません")
    elif c.p_value < 0.05 and c.win_rate_diff > 0:
        c.verdict = (f"基準より高い（差 {c.win_rate_diff:+.1%}, p={c.p_value:.3f}）。"
                     f"ただし1つの検証にすぎません")
    elif c.p_value < 0.05 and c.win_rate_diff < 0:
        c.verdict = (f"基準より低い（差 {c.win_rate_diff:+.1%}, p={c.p_value:.3f}）。"
                     f"選別が結果を悪くしています")
    else:
        c.verdict = (f"基準と区別がつきません（差 {c.win_rate_diff:+.1%}, "
                     f"p={c.p_value:.3f}）。選別に効果があるとは言えません")
    return c


@dataclass
class Stratified:
    """銘柄ごとに揃えたうえでの比較。

    **まとめて比べると、まず間違える。** 実際に間違えた。

    合成データでは銘柄ごとに場面が違う。上昇が続く銘柄では、何も考えず
    買うだけで勝率7割になる。持ち合いの銘柄では勝てない。
    そこで「選別した組」が持ち合いの銘柄に偏っていると、全体の基準と
    比べただけで「選別すると成績が下がる」という結論が出てしまう。
    **下がったのは選別のせいではなく、比べた相手が違ったせい。**

    だから銘柄ごとに基準を取り、選別した組が実際に建てた場所の重みで
    足し合わせる。「同じ場所で何も選別しなかったらどうなったか」を出す。
    """

    n_decided: int = 0
    observed_wins: int = 0
    observed_rate: Optional[float] = None
    expected_wins: float = 0.0
    expected_rate: Optional[float] = None
    z: Optional[float] = None
    p_value: Optional[float] = None
    groups: int = 0
    verdict: str = ""

    def as_dict(self) -> Dict:
        return {
            "n_decided": self.n_decided, "observed_wins": self.observed_wins,
            "observed_rate": self.observed_rate,
            "expected_wins": round(self.expected_wins, 2),
            "expected_rate": self.expected_rate,
            "z": self.z, "p_value": self.p_value, "groups": self.groups,
            "verdict": self.verdict,
        }


def _decided(trades: Iterable) -> List:
    return [t for t in trades if t.outcome in ("WIN", "LOSS")]


def stratified_compare(selected: Iterable, universe: Iterable,
                       key: str = "pair", min_samples: int = 30) -> Stratified:
    """銘柄ごとに揃えて、選別に効果があったかを見る。

    ``universe`` は同じ規則で全部の足を建てた場合（基準）。
    ``selected`` はそのうち実際に建てたもの。
    """
    sel = _decided(selected)
    uni = _decided(universe)
    s = Stratified(n_decided=len(sel))
    if not sel or not uni:
        s.verdict = "件数が足りず、比較できません"
        return s

    base: Dict[str, List[int]] = {}
    for t in uni:
        g = base.setdefault(getattr(t, key), [0, 0])
        g[0] += 1
        g[1] += 1 if t.outcome == "WIN" else 0

    taken_by: Dict[str, List[int]] = {}
    for t in sel:
        g = taken_by.setdefault(getattr(t, key), [0, 0])
        g[0] += 1
        g[1] += 1 if t.outcome == "WIN" else 0

    exp = 0.0
    var = 0.0
    used = 0
    for g, (n_taken, _) in taken_by.items():
        if g not in base or base[g][0] == 0:
            continue
        p = base[g][1] / base[g][0]
        exp += n_taken * p
        var += n_taken * p * (1 - p)
        used += 1

    s.groups = used
    s.observed_wins = sum(v[1] for v in taken_by.values())
    s.observed_rate = round(s.observed_wins / len(sel), 4)
    s.expected_wins = exp
    s.expected_rate = round(exp / len(sel), 4) if sel else None

    if var <= 0 or len(sel) < min_samples:
        s.verdict = (f"決着 {len(sel)} 件では、銘柄ごとに揃えた比較はできません"
                     f"（{min_samples} 件必要）")
        return s

    s.z = round((s.observed_wins - exp) / math.sqrt(var), 3)
    s.p_value = round(two_sided_p(s.z), 4)
    diff = s.observed_rate - s.expected_rate

    if s.p_value >= 0.05:
        s.verdict = (f"同じ銘柄で何も選別しなかった場合と区別がつきません"
                     f"（実測 {s.observed_rate:.1%} / 期待 {s.expected_rate:.1%}, "
                     f"p={s.p_value:.3f}）。選別に効果があるとは言えません")
    elif diff > 0:
        s.verdict = (f"同じ銘柄の基準より高い（実測 {s.observed_rate:.1%} / "
                     f"期待 {s.expected_rate:.1%}, p={s.p_value:.3f}）")
    else:
        s.verdict = (f"同じ銘柄の基準より低い（実測 {s.observed_rate:.1%} / "
                     f"期待 {s.expected_rate:.1%}, p={s.p_value:.3f}）。"
                     f"選別が結果を悪くしています")
    return s


def component_effect(trades: Iterable, component: str,
                     edges: Sequence[float], min_samples: int = 30) -> List[Stats]:
    """配点の1項目の高低で成績を分ける。

    **どの項目が成績を押し下げているかは、ここでしか分からない。**
    合計点だけ見ていると「80点台が弱い」までしか言えず、9項目のどれが
    原因かに辿り着けない。項目ごとに区切って、単調に効いているかを見る。

    得点が上がるほど成績が下がる項目があれば、その項目は**逆に効いている**。
    重みを上げるどころか、符号から考え直す必要がある。
    """
    items = [t for t in trades if component in (t.breakdown or {})]
    out: List[Stats] = []
    for lo, hi in zip(edges, edges[1:]):
        sel = [t for t in items if lo <= t.breakdown[component] < hi]
        out.append(summarize(sel, label=f"{component} {lo:g}-{hi:g}",
                             min_samples=min_samples))
    return out


def effect_direction(buckets: Sequence[Stats]) -> str:
    """項目が順方向に効いているか、逆に効いているかを一言で返す。"""
    usable = [b for b in buckets if b.avg_r is not None and b.n >= 10]
    if len(usable) < 2:
        return "件数が足りず、判断できません"
    pairs = list(zip(usable, usable[1:]))
    up = sum(1 for a, b in pairs if b.avg_r > a.avg_r)
    down = sum(1 for a, b in pairs if b.avg_r < a.avg_r)
    if down == len(pairs):
        return "得点が高いほど成績が悪い。**逆に効いています**"
    if up == len(pairs):
        return "得点が高いほど成績が良い。順方向に効いています"
    return "一貫した向きがありません"


def monotonicity(bands: Sequence[Stats]) -> str:
    """点数が上がるほど成績が良くなっているか。

    **ここが崩れていたら、配点そのものを疑うべき。** 点数の高い区分が
    低い区分に勝てないなら、その点数は場面の良さを測れていない。
    """
    usable = [b for b in bands if b.avg_r is not None and b.n > 0]
    if len(usable) < 2:
        return "区分が足りず、傾向を見られません"
    pairs = list(zip(usable, usable[1:]))
    up = sum(1 for a, b in pairs if b.avg_r > a.avg_r)
    if up == len(pairs):
        return "点数が上がるほど平均 R も上がっています"
    if up == 0:
        return "点数が上がっても平均 R は上がっていません。配点を疑うべきです"
    return (f"傾向は一貫していません（{len(pairs)} 組中 {up} 組だけ順当）。"
            f"配点が場面の良さを測れているとは言えません")
