# -*- coding: utf-8 -*-
"""シグナルの組み立て。

大事な決まりが3つある。

1. **点数と方向は別に決める。** 点数が高いだけで BUY にはしない。点数は
   「場面が整っているか」であって、向きの根拠ではない。
2. **点数は勝率ではない。** 80点は勝率80%を意味しない。名前も score で
   通し、確率と呼ばない。
3. **強制の見送りは点数で覆せない。** filters.py の結果が優先する。

理由（reasons）は、どの材料がどう働いたかを書く。「AIが上がると考えている」
のような、確かめようのない文言は書かない。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from .filters import FilterResult
from .indicator_engine import relative_level
from .market_structure import structure_direction
from .models import (
    Direction,
    MarketStructure,
    Regime,
    RegimeResult,
    ScoreBreakdown,
    SetupQuality,
    Signal,
    SpreadInfo,
    StructureState,
)
from .regime_engine import regime_direction
from .strategies import (
    REGISTRY,
    Context,
    Strategy,
    decide_range,
    decide_trend,
    strategies_for,
)


def _last(ind: Dict[str, List[Optional[float]]], key: str) -> Optional[float]:
    arr = ind.get(key) or []
    return arr[-1] if arr else None


# ---------------------------------------------------------------- 方向
#
# **向きの決め方は strategies.py に集めた。** 戦略ごとに違うものなので、
# ここに並べると「どれがどの前提か」が読めなくなる。
# 呼び出し側の互換のために名前だけ残してある。


def decide_direction(
    h1_ind: Dict, h4_ind: Dict, m15_structure: MarketStructure,
    regime: Regime, cfg: Dict,
) -> Tuple[Direction, List[str]]:
    """トレンド追随の向き（``strategies.decide_trend`` と同じもの）。"""
    return decide_trend(Context(
        h1=h1_ind, h4=h4_ind, m15={}, m5={}, structure=m15_structure,
        regime=regime, signal_cfg=cfg, cfg={}))


def decide_direction_range(m15_ind: Dict, cfg: Dict
                           ) -> Tuple[Direction, List[str]]:
    """持ち合いの逆張りの向き（``strategies.decide_range`` と同じもの）。"""
    return decide_range(Context(
        h1={}, h4={}, m15=m15_ind, m5={}, structure=MarketStructure(),
        regime=Regime.RANGE, signal_cfg={}, cfg=cfg))


# ---------------------------------------------------------------- 点数

def _session_score(now: datetime, cfg: Dict) -> Tuple[float, str]:
    """時間帯の点。流動性の目安であって、儲かる根拠ではない。"""
    s = cfg["session"]
    h = now.astimezone(timezone.utc).hour
    in_london = s["london"]["start"] <= h < s["london"]["end"]
    in_ny = s["newyork"]["start"] <= h < s["newyork"]["end"]
    in_tokyo = s["tokyo"]["start"] <= h < s["tokyo"]["end"]
    if in_london and in_ny:
        return float(s["score"]["overlap"]), "ロンドンとニューヨークが重なる時間帯"
    if in_london or in_ny or in_tokyo:
        return float(s["score"]["single"]), "主要市場が開いている時間帯"
    return float(s["score"]["off"]), "主要市場が閉じている時間帯"


def score_setup(
    direction: Direction,
    h1_ind: Dict, h4_ind: Dict, m15_ind: Dict, m5_ind: Dict,
    m15_structure: MarketStructure,
    regime_result: RegimeResult,
    spread: Optional[SpreadInfo],
    max_spread_pips: float,
    cfg: Dict,
    now: Optional[datetime] = None,
    correlation: Optional[object] = None,
    news: Optional[object] = None,
    strategy: str = "trend",
) -> Tuple[ScoreBreakdown, List[str]]:
    """場面の整い方を 0-100 で表す。**勝率ではない。**

    ``strategy`` で、戦略に依存する4項目（トレンド・勢い・支持抵抗・
    値動きの形）の測り方が変わる。**項目と配点は変えない。**

    配点から外さないのは理由がある。持ち合いの逆張りでは「上位足の
    トレンドが向きと一致」は根拠にならないが、だからといって測れない
    わけではない。**「測れない」と「この戦略には当てはまらない」は別。**
    外すと満点（attainable）が下がって、少ない材料で売買の水準に届いて
    しまう。ここでは同じ項目を、その戦略にとっての根拠で測り直す。
    """
    now = now or datetime.now(timezone.utc)
    w = cfg["weights"]
    b = ScoreBreakdown()
    reasons: List[str] = []
    sign = 1 if direction is Direction.LONG else (-1 if direction is Direction.SHORT else 0)
    is_range = strategy == "range"
    rcfg = cfg.get("range") or {}

    close = _last(m15_ind, "close")
    atr_v = _last(m15_ind, "atr14")
    sup, res_ = _last(m15_ind, "support"), _last(m15_ind, "resistance")
    rsi_v = _last(m15_ind, "rsi14")
    macd_h = _last(m15_ind, "macd_hist")

    # --- トレンド ---
    if is_range:
        # 持ち合いでの20点は、**トレンドが無いことを測る。** 逆張りが
        # 成り立つ根拠は「どちらへも伸びていない」こと。向きとの一致で
        # 測ると、根拠が逆のものを根拠として数えることになる。
        flat = float(rcfg.get("flat_ema_sep_atr", 1.0))
        seps: List[float] = []
        for ind in (h4_ind, h1_ind):
            e20, e50, a = (_last(ind, "ema20"), _last(ind, "ema50"),
                           _last(ind, "atr14"))
            if e20 is None or e50 is None or not a or a <= 0 or flat <= 0:
                continue
            seps.append(max(0.0, 1.0 - abs(e20 - e50) / a / flat))
        if seps:
            ratio = sum(seps) / len(seps)
            b.trend = round(w["trend"] * ratio, 2)
            if ratio > 0.7:
                reasons.append("H4 と H1 の EMA が絡んでおり、方向が出ていない")
            elif ratio < 0.3:
                reasons.append("上位足にはまだ向きが出ており、逆張りには不利")
    else:
        hits = 0
        total = 0
        for ind, label in ((h4_ind, "H4"), (h1_ind, "H1")):
            e20, e50, e200 = _last(ind, "ema20"), _last(ind, "ema50"), _last(ind, "ema200")
            if e20 is None or e50 is None:
                continue
            total += 1
            if sign and ((e20 - e50) * sign > 0):
                hits += 1
            if e200 is not None:
                total += 1
                c = _last(ind, "close") or e20
                if sign and ((c - e200) * sign > 0):
                    hits += 1
        if total:
            b.trend = round(w["trend"] * hits / total, 2)
            if hits == total:
                reasons.append("H4 と H1 のトレンドが向きと揃っている")
            elif hits == 0:
                reasons.append("トレンドは向きと逆")

    # --- 勢い ---
    mom = 0.0
    if is_range:
        # 逆張りでは、**勢いが向きと逆なのが普通。** 見るのは2つ。
        #   1. RSI が入る側の端にあるか（行き過ぎているほど良い）
        #   2. 逆向きの勢いが弱まっているか（MACD ヒストグラムの縮小）
        ext = float(rcfg.get("rsi_extreme", 30.0))
        neu = float(rcfg.get("rsi_neutral", 50.0))
        if rsi_v is not None and sign and neu != ext:
            v = rsi_v if sign > 0 else 100.0 - rsi_v
            mom += 0.6 * max(0.0, min(1.0, (neu - v) / (neu - ext)))
        hist = [h for h in (m15_ind.get("macd_hist") or [])[-3:] if h is not None]
        if len(hist) >= 2 and sign:
            if hist[-1] * sign > 0:
                mom += 0.4          # すでに反転の側へ向いている
            elif abs(hist[-1]) < abs(hist[-2]):
                mom += 0.25         # まだ逆だが弱まっている
        if mom >= 0.8:
            reasons.append("RSI が端にあり、逆向きの勢いも弱まっている")
    else:
        if macd_h is not None and sign:
            mom += 0.5 if macd_h * sign > 0 else 0.0
        if rsi_v is not None and sign:
            # 行き過ぎの側に飛びつかない。順方向でも極端なら加点しない。
            if sign > 0:
                mom += 0.5 if 45 <= rsi_v <= 70 else (0.2 if rsi_v > 70 else 0.0)
            else:
                mom += 0.5 if 30 <= rsi_v <= 55 else (0.2 if rsi_v < 30 else 0.0)
        if mom >= 0.9:
            reasons.append("M15 の MACD と RSI がどちらも向きを支持")
    b.momentum = round(w["momentum"] * min(mom, 1.0), 2)

    # --- 支持帯・抵抗帯からの距離 ---
    if close and atr_v and sign:
        target = sup if sign > 0 else res_
        if target:
            dist = abs(close - target) / atr_v
            if is_range:
                # 逆張りでは、**端に近いことがそのまま根拠になる。**
                # 近すぎるほど損切りを帯の外に置いても幅が小さくて済む。
                # 追随のときの「近すぎは抜けの危険」は当てはまらない
                # （抜けていないことは向きの判断で既に確かめてある）。
                limit = float(rcfg.get("edge_full_score_atr",
                                       cfg.get("max_distance_atr", 0.5)) or 0.5)
                ratio = max(0.0, 1.0 - dist / limit) if limit > 0 else 0.0
            else:
                # 近いほど良い（押し目・戻り）。ただし0.2未満は抜けの危険があるので満点にしない。
                ratio = 0.0 if dist > 3 else max(0.0, 1.0 - dist / 3.0)
                if dist < 0.2:
                    ratio *= 0.6
            b.support_resistance = round(w["support_resistance"] * ratio, 2)
            if ratio > 0.6:
                reasons.append(
                    f"{'支持' if sign > 0 else '抵抗'}帯まで ATR の {dist:.1f} 倍と近い")

    # --- 変動の大きさ ---
    atr_ratio = relative_level(h1_ind.get("atr14") or [], 100)
    if atr_ratio is not None:
        # 普段どおり（比が 1.0 前後）がいちばん扱いやすい
        ratio = max(0.0, 1.0 - abs(atr_ratio - 1.0))
        b.volatility = round(w["volatility"] * ratio, 2)
        if ratio > 0.7:
            reasons.append(f"ATR が普段の {atr_ratio:.1f} 倍で平常の範囲")

    # --- 値動きの形（HH/HL/LH/LL・BOS・下位足のストキャス）---
    #
    # 従来は「相場の構造」を15点の独立項目にしていたが、配点の見直しで
    # ここに畳んだ。**材料そのものは捨てていない。** 実データの検証では
    # 構造は数少ない「順方向に効いた」項目だったので、落とすと測れた
    # 数少ない手がかりを失う。
    pa = 0.0
    s_d = structure_direction(m15_structure)
    stoch = _last(m5_ind, "stoch_k")
    if is_range:
        # 逆張りでの「形」。追随とは良し悪しが逆になる。
        #   * 構造が中立＝どちらへも切り上げ／切り下げていない → 持ち合い
        #   * 構造が向きと同じ＝すでに反転が始まっている → なお良い
        #   * BOS（直近の転換点を終値で抜けた）＝**持ち合いが終わった合図**
        #     なので、ここでは減点材料。追随のときの加点をそのまま
        #     持ち込むと、抜けた場面ほど高い点が付いてしまう。
        if m15_structure.structure is StructureState.NEUTRAL:
            pa += 0.45
            reasons.append("M15 の構造は中立で、持ち合いと整合")
        elif s_d != 0 and sign and s_d == sign:
            pa += 0.35
            reasons.append("M15 の構造はすでに反転の側")
        else:
            reasons.append("M15 の構造はまだ逆張りの向きと逆")
        if m15_structure.bos:
            pa = max(0.0, pa - 0.25)
            reasons.append("直近の転換点を終値で抜けており、持ち合いが崩れかけ")
        if stoch is not None and sign:
            # 入る側で行き過ぎているか（買いなら売られ過ぎ）
            v = stoch if sign > 0 else 100.0 - stoch
            if v <= 20:
                pa += 0.4
            elif v <= 35:
                pa += 0.2
    else:
        if s_d != 0 and sign:
            if s_d == sign:
                pa += 0.4
                reasons.append(
                    f"M15 の構造が {'/'.join(m15_structure.pattern) or '—'} で向きと一致")
            else:
                reasons.append("M15 の構造は向きと逆")
        elif m15_structure.structure is StructureState.NEUTRAL:
            pa += 0.16                      # 中立は控えめに
        if m15_structure.bos and sign and s_d == sign:
            pa += 0.3
            reasons.append("直近の転換点を終値で抜けている")
        if stoch is not None and sign:
            if sign > 0 and stoch < 80:
                pa += 0.3
            elif sign < 0 and stoch > 20:
                pa += 0.3
    b.price_action = round(w["price_action"] * min(pa, 1.0), 2)

    # --- 相関 ---
    #
    # **どの銘柄が何と相関するかは決め打ちしない。** 期間ごとに測って、
    # 弱ければ使わない（correlation.py）。測れなければ 0点のまま。
    # **中立として半分入れることはしない。**
    if correlation is not None and correlation.state == "KNOWN":
        b.correlation = round(w["correlation"] * correlation.ratio, 2)
        reasons.extend(correlation.reasons)
    else:
        b.correlation = round(w["correlation"]
                              * cfg["correlation"]["neutral_score_ratio"], 2)
        if correlation is not None and correlation.reasons:
            reasons.append(correlation.reasons[0])

    # --- ニュース ---
    #
    # **発表前後の停止は強制条件で表す。点数では表さない。**
    # 点数にすると、他の項目が高ければ上書きできてしまう。
    # ここで点にするのは「次の重要指標までの余裕」だけ。
    if news is not None and news.available and news.state.value != "UNKNOWN":
        b.news = round(w.get("news", 0) * news.ratio, 2)
        if news.reasons:
            reasons.append(news.reasons[0])
    else:
        b.news = 0.0
        if news is not None and news.reasons:
            reasons.append(news.reasons[0])

    # --- 時間帯 ---
    sess, sess_note = _session_score(now, cfg)
    b.session = round(min(sess, w["session"]), 2)
    reasons.append(sess_note)

    # --- スプレッド ---
    if spread and max_spread_pips > 0:
        room = max(0.0, 1.0 - spread.spread_pips / max_spread_pips)
        b.spread_risk = round(w["spread_risk"] * room, 2)

    return b, reasons


def attainable_max(cfg: Dict,
                   unavailable: Optional[Sequence[str]] = None) -> float:
    """材料がある項目の配点合計。

    **100点は出ない。** 相関10点とニュース10点は材料が無いので常に0点。
    だから到達しうる最大は80点。区分の境目を絶対点の80で持つと、
    最大と一致して満点以外は BUY/SELL が出なくなる。
    """
    weights = cfg["weights"]
    avail = cfg.get("data_available") or {}
    skip = set(unavailable or ())
    return float(sum(v for k, v in weights.items()
                     if avail.get(k, True) and k not in skip))


def classify_quality(score: float, cfg: Dict,
                     unavailable: Optional[Sequence[str]] = None) -> SetupQuality:
    """点数を区分に落とす。

    区分は「測れる配点に対する割合」で判定する。材料を繋いで満点が
    増えても、区分の意味が変わらないようにするため。
    """
    basis = cfg.get("score_basis", "absolute")
    denom = (attainable_max(cfg, unavailable) if basis == "attainable"
             else float(sum(cfg["weights"].values())))
    if denom <= 0:
        return SetupQuality.NO_TRADE
    pct = score / denom * 100.0
    for c in cfg["categories"]:
        lo = c.get("min_pct", c.get("min"))
        hi = c.get("max_pct", c.get("max"))
        if lo <= pct <= hi:
            return SetupQuality(c["label"])
    return SetupQuality.NO_TRADE


# 「〜の相場」に続けて読める形にしておく（「変動が小さいの相場」にしない）
_REGIME_JA = {
    "RANGE": "持ち合い", "TRANSITION": "移行中",
    "HIGH_VOLATILITY": "変動の大きい", "LOW_VOLATILITY": "変動の小さい",
    "NEWS": "指標発表中", "NO_TRADE": "判定不可",
}


TREND = "trend"
RANGE = "range"


def strategy_for(regime: Regime, cfg: Dict) -> Optional[str]:
    """その相場つきで最初に試す戦略の名前。無ければ ``None``。

    **点数とは別に決める。** 点数で表すと、高得点で上書きできてしまう。
    複数を割り当てている場合、実際にどれで入ったかは
    ``strategies.decide`` が返す（ここでは先頭しか分からない）。
    """
    names = strategies_for(regime, cfg)
    return names[0].name if names else None


def strategy_supports(regime: Regime, cfg: Dict) -> bool:
    """その相場つきに対応する戦略を持っているか。"""
    return bool(strategies_for(regime, cfg))


def decide_signal(
    direction: Direction,
    quality: SetupQuality,
    filter_result: FilterResult,
    regime: Optional[Regime] = None,
    filters_cfg: Optional[Dict] = None,
) -> Tuple[Signal, List[str]]:
    """最終のシグナル。

    順序: **強制の見送り → 相場つきへの対応 → 点数の区分 → 方向**。
    点数が高いだけで BUY にはならないし、対応する戦略が無い相場つきでも
    BUY にはならない。
    """
    invalidation: List[str] = list(filter_result.reasons)
    if filter_result.blocked:
        return Signal.NO_TRADE, invalidation

    # --- 相場つきに対応する戦略があるか（点数より先に見る）---
    if regime is not None and filters_cfg is not None:
        if not strategy_supports(regime, filters_cfg):
            name = _REGIME_JA.get(regime.value, regime.value)
            joiner = "" if name.endswith("い") else "の"
            table = (filters_cfg.get("regime_strategy", {})
                     .get("strategies") or {})
            # **`name` を使い回さない。** 以前ここを `name` で回していて、
            # 上で作った相場つきの日本語名を上書きしていた。その結果
            # 「range相場に対応する戦略を持っていません（持っているのは
            # …持ち合いの逆張り）」という、相場つきも中身も食い違う文が
            # 出ていた（regime は HIGH_VOLATILITY だった）。
            used: List[str] = []
            for v in table.values():
                for sname in ([v] if isinstance(v, str) else list(v or ())):
                    st = REGISTRY.get(str(sname))
                    if st is not None and st.label not in used:
                        used.append(st.label)
            have = "・".join(used)
            invalidation.append(
                f"{name}{joiner}相場に対応する戦略を持っていません"
                f"（持っているのは {have or 'トレンド追随'}）")
            action = (filters_cfg.get("regime_strategy", {})
                      .get("action_when_unsupported", "WAIT"))
            return (Signal.NO_TRADE if action == "NO_TRADE"
                    else Signal.WAIT), invalidation

    if quality is SetupQuality.NO_TRADE:
        invalidation.append("点数が取引の水準に届いていません")
        return Signal.NO_TRADE, invalidation

    if direction is Direction.NEUTRAL:
        invalidation.append("向きが定まっていません")
        return Signal.WAIT, invalidation

    if quality in (SetupQuality.WATCH, SetupQuality.SETUP):
        invalidation.append("場面は整いつつありますが、まだ様子見の水準です")
        return Signal.WAIT, invalidation

    return (Signal.BUY if direction is Direction.LONG else Signal.SELL), invalidation
