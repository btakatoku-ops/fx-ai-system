# -*- coding: utf-8 -*-
"""戦略の一覧と、向きの決め方。

**戦略が違えば、向きの決め方も違う。** トレンド追随は押し目を買うが、
持ち合いでは同じ場面が「上限に近い＝売り」になる。同じ条件を当てはめると
前提ごと間違える。だから向きの判断は戦略ごとに分け、ここに集める。

## 増やすときの決まり

戦略を増やすこと自体は構わない。ただし **増やせば強くなる、ということは
無い。** 順に試して最初に出た向きを採るので、戦略が増えるほど
「どれかが偶然当てはまる」回数が増える。1件あたりの期待値が負なら、
件数が増えるぶんだけ負けが増える。

なので、次の2つを必ず守る。

1. **どの戦略で入ったかを記録する**（``AnalysisResult.strategy``）。
   混ぜて集計すると、効いていない戦略が効いている戦略の成績に隠れる。
2. **実データで1件ずつ測る**（``scripts/measure_strategies.py``）。
   測る前の戦略は「候補」であって、戦略ではない。

## 点数の測り方

``scoring`` は ``signal_engine.score_setup`` のどの測り方を使うかを指す。
順張り側（trend / breakout / momentum_flag）は "trend"、
逆張り側（range / failed_breakout）は "range"。
**項目と配点は変えない。変えるのは測り方だけ。**
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .market_structure import structure_direction
from .models import Direction, MarketStructure, Regime
from .regime_engine import regime_direction

log = logging.getLogger(__name__)

Decision = Tuple[Direction, List[str]]


def last(ind: Dict, key: str) -> Optional[float]:
    arr = ind.get(key) or []
    return arr[-1] if arr else None


def _series(ind: Dict, key: str, n: int) -> List[float]:
    """末尾 n 本のうち、値のあるものだけ。"""
    arr = (ind.get(key) or [])[-n:] if n > 0 else []
    return [float(v) for v in arr if v is not None]


@dataclass
class Context:
    """戦略が向きを決めるのに使う材料。

    **戦略が自分で材料を取りに行かない。** 取りに行けるようにすると、
    戦略ごとに違う足・違う期間を見はじめて、比較ができなくなる。
    """

    h1: Dict
    h4: Dict
    m15: Dict
    m5: Dict
    structure: MarketStructure
    regime: Regime
    signal_cfg: Dict                       # config/signal_weights.json
    cfg: Dict                              # その戦略の設定


@dataclass(frozen=True)
class Strategy:
    name: str
    label: str
    scoring: str                           # "trend" / "range"
    decide: Callable[["Context"], Decision]
    why: str = ""


# ------------------------------------------------------------ トレンド追随

def decide_trend(ctx: Context) -> Decision:
    """上位足の向きと構造が揃った側に付く。**材料の一致だけで決める。**"""
    cfg = ctx.signal_cfg
    reasons: List[str] = []
    votes: List[int] = []

    def ema_dir(ind: Dict, label: str) -> int:
        e20, e50 = last(ind, "ema20"), last(ind, "ema50")
        if e20 is None or e50 is None:
            return 0
        d = 1 if e20 > e50 else -1
        reasons.append(f"{label} は EMA20 が EMA50 の{'上' if d > 0 else '下'}")
        return d

    h4_d = ema_dir(ctx.h4, "H4")
    h1_d = ema_dir(ctx.h1, "H1")
    votes.extend([h4_d, h1_d])

    s_d = structure_direction(ctx.structure)
    if s_d != 0:
        votes.append(s_d)
        reasons.append(f"M15 の構造は{'上' if s_d > 0 else '下'}向き")

    r_d = regime_direction(ctx.regime)
    if r_d != 0:
        votes.append(r_d)

    if cfg["direction"]["require_h1_h4_agreement"] and h1_d != 0 and h4_d != 0:
        if h1_d != h4_d:
            reasons.append("H1 と H4 の向きが揃っていない")
            return Direction.NEUTRAL, reasons

    up = sum(1 for v in votes if v > 0)
    down = sum(1 for v in votes if v < 0)
    need = cfg["direction"]["min_agreement"]
    if up >= need and up > down:
        return Direction.LONG, reasons
    if down >= need and down > up:
        return Direction.SHORT, reasons
    reasons.append("向きを決めるだけの材料が揃っていない")
    return Direction.NEUTRAL, reasons


# ------------------------------------------------------ 持ち合いの逆張り

def decide_range(ctx: Context) -> Decision:
    """持ち合い・変動の小さい相場。**帯の端でだけ入る。**

    入る条件は4つで、ひとつでも欠けたら様子見。

    1. 支持帯と抵抗帯の幅が ATR の一定倍以上（幅が無ければ取れない）
    2. 直近で帯を終値で抜けていない（抜けていれば持ち合いは終わっている）
    3. どちらかの端まで ATR の一定倍以内（**真ん中では入らない**）
    4. RSI も反転の側（端に来ただけでは入らない）
    """
    cfg, m15 = ctx.cfg, ctx.m15
    reasons: List[str] = []
    close, atr_v = last(m15, "close"), last(m15, "atr14")
    sup, res_ = last(m15, "support"), last(m15, "resistance")
    rsi_v = last(m15, "rsi14")

    if close is None or not atr_v or atr_v <= 0:
        reasons.append("ATR が無く、帯までの距離を測れません")
        return Direction.NEUTRAL, reasons
    if sup is None or res_ is None:
        reasons.append("支持帯と抵抗帯の両方が揃っておらず、帯を決められません")
        return Direction.NEUTRAL, reasons

    width = (res_ - sup) / atr_v
    min_width = float(cfg.get("min_band_width_atr", 1.5))
    if width < min_width:
        reasons.append(
            f"帯の幅が ATR の {width:.1f} 倍しかありません"
            f"（{min_width:.1f} 倍以上が必要）。取れる値幅がありません")
        return Direction.NEUTRAL, reasons

    if cfg.get("require_no_breakout", True):
        look = int(cfg.get("breakout_lookback_bars", 10))
        closes = _series(m15, "close", look)
        if any(c > res_ or c < sup for c in closes):
            reasons.append(
                f"直近 {look} 本のうちに帯を終値で抜けています。"
                f"持ち合いが続いている前提が崩れています")
            return Direction.NEUTRAL, reasons

    max_dist = float(cfg.get("max_distance_atr", 0.5))
    d_sup = (close - sup) / atr_v
    d_res = (res_ - close) / atr_v
    confirm = cfg.get("rsi_confirm") or {}

    if d_sup <= max_dist:
        limit = confirm.get("long_below")
        if limit is not None:
            if rsi_v is None:
                reasons.append("RSI が無く、反転の側かどうかを確かめられません")
                return Direction.NEUTRAL, reasons
            if rsi_v >= float(limit):
                reasons.append(
                    f"支持帯に近い（ATR の {d_sup:.2f} 倍）ものの、"
                    f"RSI が {rsi_v:.0f} で反転の側にありません"
                    f"（{float(limit):.0f} 未満が必要）")
                return Direction.NEUTRAL, reasons
        reasons.append(
            f"帯の下端。支持帯まで ATR の {d_sup:.2f} 倍、"
            f"帯の幅は {width:.1f} 倍、RSI {rsi_v:.0f}")
        return Direction.LONG, reasons

    if d_res <= max_dist:
        limit = confirm.get("short_above")
        if limit is not None:
            if rsi_v is None:
                reasons.append("RSI が無く、反転の側かどうかを確かめられません")
                return Direction.NEUTRAL, reasons
            if rsi_v <= float(limit):
                reasons.append(
                    f"抵抗帯に近い（ATR の {d_res:.2f} 倍）ものの、"
                    f"RSI が {rsi_v:.0f} で反転の側にありません"
                    f"（{float(limit):.0f} 超が必要）")
                return Direction.NEUTRAL, reasons
        reasons.append(
            f"帯の上端。抵抗帯まで ATR の {d_res:.2f} 倍、"
            f"帯の幅は {width:.1f} 倍、RSI {rsi_v:.0f}")
        return Direction.SHORT, reasons

    reasons.append(
        f"帯の内側にいます（下端まで ATR の {d_sup:.1f} 倍、"
        f"上端まで {d_res:.1f} 倍）。端に来るまで入りません")
    return Direction.NEUTRAL, reasons


# ------------------------------------------------------------------ 抜け

def _prior_range(m15: Dict, lookback: int, skip: int
                 ) -> Optional[Tuple[float, float]]:
    """直近 skip 本を除いた、その前 lookback 本の高値・安値。

    **抜けた足そのものを帯に含めない。** 含めると帯が抜けた先まで伸びて、
    「抜けた」と判定できなくなる。
    """
    highs = m15.get("high") or []
    lows = m15.get("low") or []
    if not highs or not lows:
        return None
    end = len(highs) - skip
    start = end - lookback
    if start < 0 or end <= start:
        return None
    hs = [h for h in highs[start:end] if h is not None]
    ls = [x for x in lows[start:end] if x is not None]
    if not hs or not ls:
        return None
    return max(hs), min(ls)


def _find_break(m15: Dict, atr_v: float, look: int, first: int, last_ago: int,
                min_break: float) -> Optional[Tuple[int, int, float]]:
    """帯を終値で抜けた足を、近いほうから探す。

    返すのは (何本前, 向き, 抜かれた水準)。見つからなければ ``None``。
    """
    closes = m15.get("close") or []
    for ago in range(first, last_ago + 1):
        idx = len(closes) - 1 - ago
        if idx < 0:
            break
        c = closes[idx]
        if c is None:
            continue
        band = _prior_range(m15, look, ago + 1)
        if band is None:
            return None
        hi, lo = band
        if c - hi >= min_break * atr_v:
            return ago, 1, hi
        if lo - c >= min_break * atr_v:
            return ago, -1, lo
    return None


def decide_breakout(ctx: Context) -> Decision:
    """持ち合いを抜けた側に付く。**抜けた直後だけ。追いかけない。**

    トレンド追随との違いは入る場所。追随は押し目で入るが、抜けは
    「帯の外に出た直後」に入る。損切りは帯の内側に置ける。

    条件。

    1. 直前の帯（抜けた足を除く）を、終値で ATR の一定倍ぶん抜けている
    2. 抜けてから一定本数以内（**出遅れて飛びつかない**）
    3. 抜けた水準から離れすぎていない（**伸びきってから入らない**）
    4. ATR が広がっている（動きを伴わない抜けは続かない）
    """
    cfg, m15 = ctx.cfg, ctx.m15
    reasons: List[str] = []
    close, atr_v = last(m15, "close"), last(m15, "atr14")
    if close is None or not atr_v or atr_v <= 0:
        reasons.append("ATR が無く、抜けの大きさを測れません")
        return Direction.NEUTRAL, reasons

    look = int(cfg.get("lookback_bars", 40))
    max_since = int(cfg.get("max_bars_since_break", 3))
    min_break = float(cfg.get("min_break_atr", 0.15))
    max_ext = float(cfg.get("max_extension_atr", 1.5))

    hit = _find_break(m15, atr_v, look, 0, max_since, min_break)
    if hit is None:
        reasons.append(
            f"直近 {max_since + 1} 本では、直前 {look} 本の帯を"
            f"ATR の {min_break:.2f} 倍ぶん抜けていません")
        return Direction.NEUTRAL, reasons

    ago, sign, level = hit
    ext = abs(close - level) / atr_v
    if ext > max_ext:
        reasons.append(
            f"抜けた水準から ATR の {ext:.1f} 倍離れています"
            f"（{max_ext:.1f} 倍まで）。伸びきってからは追いません")
        return Direction.NEUTRAL, reasons

    # **戻されていたら抜けとして扱わない。** 戻りは failed_breakout の
    # 場面であって、同じ足で両方に入れてはいけない。
    back_inside = (close < level) if sign > 0 else (close > level)
    if back_inside:
        reasons.append("抜けたあと帯の中へ戻されています。抜けとしては入りません")
        return Direction.NEUTRAL, reasons

    min_exp = float(cfg.get("min_atr_expansion", 1.0))
    if min_exp > 1.0:
        atrs = _series(m15, "atr14", int(cfg.get("atr_compare_bars", 20)) + 1)
        if len(atrs) < 2 or atrs[0] <= 0:
            reasons.append("ATR の変化を測れません")
            return Direction.NEUTRAL, reasons
        exp = atrs[-1] / atrs[0]
        if exp < min_exp:
            reasons.append(
                f"ATR が {exp:.2f} 倍にしかなっていません"
                f"（{min_exp:.2f} 倍以上が必要）。動きを伴わない抜けです")
            return Direction.NEUTRAL, reasons

    reasons.append(
        f"{ago} 本前に直前 {look} 本の帯を{'上' if sign > 0 else '下'}へ"
        f"終値で抜け、いま水準から ATR の {ext:.1f} 倍のところ")
    return (Direction.LONG if sign > 0 else Direction.SHORT), reasons


def decide_failed_breakout(ctx: Context) -> Decision:
    """抜けたのに戻された側に付く。**抜けの失敗を取る。**

    持ち合いの逆張りとは別物。逆張りは「端に来ただけ」で入るが、これは
    **一度外に出てから帯の中へ戻った**ことを条件にする。戻されたという
    事実があるぶん、根拠が一段強い。ただし狙いは反転なので、点数は
    逆張りの測り方で見る。
    """
    cfg, m15 = ctx.cfg, ctx.m15
    reasons: List[str] = []
    close, atr_v = last(m15, "close"), last(m15, "atr14")
    if close is None or not atr_v or atr_v <= 0:
        reasons.append("ATR が無く、抜けの大きさを測れません")
        return Direction.NEUTRAL, reasons

    look = int(cfg.get("lookback_bars", 40))
    window = int(cfg.get("failure_window_bars", 5))
    min_break = float(cfg.get("min_break_atr", 0.15))

    hit = _find_break(m15, atr_v, look, 1, window, min_break)
    if hit is None:
        reasons.append(
            f"直近 {window} 本のうちに、帯を終値で抜けた足がありません")
        return Direction.NEUTRAL, reasons

    ago, sign, level = hit
    # **いま帯の中へ戻っていること。** 戻っていなければ、抜けは失敗して
    # いない。ここを省くと、抜けの途中で毎回逆に入ることになる。
    back_inside = (close < level) if sign > 0 else (close > level)
    if not back_inside:
        reasons.append(
            f"{ago} 本前に抜けたまま、まだ帯の外にいます。失敗していません")
        return Direction.NEUTRAL, reasons

    min_back = float(cfg.get("min_return_atr", 0.1))
    depth = abs(close - level) / atr_v
    if depth < min_back:
        reasons.append(
            f"帯へ戻った幅が ATR の {depth:.2f} 倍しかありません"
            f"（{min_back:.2f} 倍以上が必要）")
        return Direction.NEUTRAL, reasons

    reasons.append(
        f"{ago} 本前に帯を{'上' if sign > 0 else '下'}へ抜けたあと、"
        f"いま帯の中へ ATR の {depth:.2f} 倍ぶん戻されています")
    return (Direction.SHORT if sign > 0 else Direction.LONG), reasons


# -------------------------------------------------------------- 押し目継続

def decide_momentum_flag(ctx: Context) -> Decision:
    """強く動いたあとの浅い押し目で、同じ方向に付く。

    トレンド追随が拾えない場面のためにある。追随は EMA と構造の一致を
    見るので、**動き出した直後で構造がまだ付いてこない場面**では向きが
    出ない。ここでは値幅そのものを見る。

    条件。

    1. 直近 impulse_bars 本で ATR の一定倍以上動いている
    2. そこから戻した幅が、動いた幅の一定割合以内（**深い押しは別物**）
    3. いま EMA20 の近くにいる
    4. H1 の向きと逆でない（上位足に逆らわない）
    """
    cfg, m15 = ctx.cfg, ctx.m15
    reasons: List[str] = []
    atr_v = last(m15, "atr14")
    if not atr_v or atr_v <= 0:
        reasons.append("ATR が無く、動いた幅を測れません")
        return Direction.NEUTRAL, reasons

    bars = int(cfg.get("impulse_bars", 12))
    seg = _series(m15, "close", bars + 1)
    if len(seg) < 3:
        reasons.append("押し目を測るだけの足がありません")
        return Direction.NEUTRAL, reasons

    start, close = seg[0], seg[-1]
    hi, lo = max(seg), min(seg)
    up_move = (hi - start) / atr_v
    down_move = (start - lo) / atr_v
    min_impulse = float(cfg.get("min_impulse_atr", 2.0))

    if up_move >= min_impulse and up_move >= down_move:
        sign, peak, move = 1, hi, up_move
    elif down_move >= min_impulse:
        sign, peak, move = -1, lo, down_move
    else:
        reasons.append(
            f"直近 {bars} 本の動きが ATR の {max(up_move, down_move):.1f} 倍で、"
            f"{min_impulse:.1f} 倍に届きません")
        return Direction.NEUTRAL, reasons

    span = abs(peak - start)
    retrace = abs(peak - close) / span if span > 0 else 1.0
    max_retrace = float(cfg.get("max_retrace", 0.5))
    if retrace > max_retrace:
        reasons.append(
            f"押しが {retrace * 100:.0f}% と深く、"
            f"{max_retrace * 100:.0f}% までの浅い押しではありません")
        return Direction.NEUTRAL, reasons

    e20 = last(m15, "ema20")
    if e20 is None:
        reasons.append("EMA20 が無く、押し目の位置を測れません")
        return Direction.NEUTRAL, reasons
    max_dist = float(cfg.get("max_distance_ema20_atr", 0.8))
    dist = abs(close - e20) / atr_v
    if dist > max_dist:
        reasons.append(
            f"EMA20 から ATR の {dist:.1f} 倍離れています"
            f"（{max_dist:.1f} 倍まで）。押し目の位置ではありません")
        return Direction.NEUTRAL, reasons

    if cfg.get("require_h1_agreement", True):
        h20, h50 = last(ctx.h1, "ema20"), last(ctx.h1, "ema50")
        if h20 is None or h50 is None:
            reasons.append("H1 の向きが分からないので、上位足に逆らえません")
            return Direction.NEUTRAL, reasons
        if (1 if h20 > h50 else -1) != sign:
            reasons.append("H1 の向きと逆の押し目なので入りません")
            return Direction.NEUTRAL, reasons

    reasons.append(
        f"直近 {bars} 本で ATR の {move:.1f} 倍{'上' if sign > 0 else '下'}へ動き、"
        f"押しは {retrace * 100:.0f}%、EMA20 まで ATR の {dist:.1f} 倍")
    return (Direction.LONG if sign > 0 else Direction.SHORT), reasons


# ------------------------------------------------------------------ 一覧

REGISTRY: Dict[str, Strategy] = {
    s.name: s for s in (
        Strategy("trend", "トレンド追随", "trend", decide_trend,
                 "上位足の向きと構造が揃った側に付く"),
        Strategy("range", "持ち合いの逆張り", "range", decide_range,
                 "帯の端でだけ入る。真ん中では入らない"),
        Strategy("breakout", "抜けに付く", "trend", decide_breakout,
                 "帯を抜けた直後だけ。伸びきってからは追わない"),
        Strategy("failed_breakout", "抜けの失敗を取る", "range",
                 decide_failed_breakout,
                 "外に出てから帯の中へ戻されたことを条件にする"),
        Strategy("momentum_flag", "押し目継続", "trend",
                 decide_momentum_flag,
                 "強く動いたあとの浅い押しで、同じ方向に付く"),
    )
}


def strategies_for(regime: Regime, filters_cfg: Dict) -> List[Strategy]:
    """その相場つきで試す戦略。順番に意味がある（先に出たものを採る）。

    **一覧に無い相場つきは「戦略なし」。** 既定をトレンドにすると、
    相場つきを増やしたときに黙って誤った戦略で判断してしまう。

    設定に知らない名前があっても落とさない。その戦略だけを飛ばす
    （＝見送り側に倒れる）。落とすと綴り間違いで全銘柄が止まる。
    """
    rs = filters_cfg.get("regime_strategy")
    if not rs:
        return [REGISTRY["trend"]]
    table = rs.get("strategies")
    if isinstance(table, dict):
        raw = table.get(regime.value)
    else:
        # 旧い形（supported の一覧）との互換
        raw = "trend" if regime.value in (rs.get("supported") or []) else None
    if not raw:
        return []
    names: Sequence[str] = [raw] if isinstance(raw, str) else list(raw)
    out: List[Strategy] = []
    for n in names:
        s = REGISTRY.get(str(n))
        if s is None:
            log.warning("知らない戦略名です（飛ばします）: %s", n)
            continue
        out.append(s)
    return out


def decide(ctx: Context, filters_cfg: Dict
           ) -> Tuple[Direction, List[str], Optional[Strategy]]:
    """相場つきに割り当てられた戦略を順に試し、最初に出た向きを採る。

    **どれで入ったかを返す。** 返さないと、効いていない戦略が効いている
    戦略の成績に紛れて見えなくなる。
    """
    reasons: List[str] = []
    for s in strategies_for(ctx.regime, filters_cfg):
        cfg = (filters_cfg.get("regime_strategy", {}) or {}).get(s.name, {}) or {}
        sub = Context(
            h1=ctx.h1, h4=ctx.h4, m15=ctx.m15, m5=ctx.m5,
            structure=ctx.structure, regime=ctx.regime,
            signal_cfg=ctx.signal_cfg, cfg=cfg)
        direction, why = s.decide(sub)
        if direction is not Direction.NEUTRAL:
            return direction, [f"{s.label}の戦略で判断しています"] + why, s
        reasons.extend(f"[{s.label}] {w}" for w in why)
    return Direction.NEUTRAL, reasons, None
