# -*- coding: utf-8 -*-
"""1通貨ペアの分析を組み立てる。

各部品（指標・構造・相場つき・強制条件・点数）を呼ぶ順番と、失敗したときの
倒し方をここで決める。

**失敗はすべて NO_TRADE に倒す。** 供給元の異常、足の不足、指標の計算失敗、
想定外の例外 — どれも BUY/SELL にはしない。分からないときに動かないのが、
資金を守る唯一確実な方法だから。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .config import PairSpec, TradingConfig
from .correlation import evaluate as evaluate_correlation
from .filters import check_all, error_result, provider_offline_result
from .freshness import check_all as check_integrity
from .freshness import next_bar_close, valid_until
from .logging_setup import ReasonCode
from .news import evaluate as evaluate_news
from .indicator_engine import compute_all, pivot_points, swing_points
from .market_data import MarketDataError, MarketDataProvider
from .market_structure import analyze as analyze_structure
from .models import (
    AnalysisResult,
    CandleSeries,
    DataQuality,
    Direction,
    IndicatorSnapshot,
    MarketStructure,
    NewsState,
    ProviderState,
    Regime,
    RegimeResult,
    ScoreBreakdown,
    SetupQuality,
    Signal,
    SpreadInfo,
    StructureState,
    TimeframeBias,
)
from .regime_engine import classify as classify_regime
from .strategies import Context as StrategyContext, decide as decide_strategy
from .signal_engine import (
    classify_quality,
    decide_signal,
    score_setup,
)

log = logging.getLogger(__name__)

# 分析に使う時間足。役割は config/timeframes.json の roles と合わせる。
ANALYSIS_TIMEFRAMES = ("H4", "H1", "M15", "M5")


def _enrich(series: CandleSeries, ind: Dict, icfg: Dict) -> Dict:
    """終値・支持抵抗・ピボット・転換点を指標の並びに足す。

    すべて足の本数と同じ長さにそろえる。添字がずれても気づけなくなるため。
    """
    n = len(series)
    closes, highs, lows = series.closes, series.highs, series.lows
    ind["close"] = list(closes)
    # 高値・安値も並びに入れる。抜けを見る戦略は「帯を終値で抜けたか」を
    # 判定するのに、その前の高値・安値が要る。**足の並びと同じ長さで持つ。**
    ind["high"] = list(highs)
    ind["low"] = list(lows)

    # 支持帯・抵抗帯は「いまの値段にいちばん近いもの」を採る。
    #
    # 直近60本の安値だけを支持帯にすると、上昇が続いた場面では遠すぎて、
    # 押し目を「支持帯から遠い」と評価してしまう。実際には押し目の支えは
    # 直近の転換安値や EMA50 であることが多い。候補を出して、値段の下で
    # いちばん高いものを支持帯、上でいちばん低いものを抵抗帯とする。
    sr_cfg = icfg["support_resistance"]
    look = sr_cfg["lookback"]
    sw = icfg["swing"]
    sh_idx, sl_idx = swing_points(highs, lows, sw["left"], sw["right"], sw["lookback"])
    ema50 = ind.get("ema50") or [None] * n

    res_list: List[Optional[float]] = [None] * n
    sup_list: List[Optional[float]] = [None] * n
    for i in range(n):
        if i < look:
            continue
        c = closes[i]
        below = [min(lows[i - look + 1: i + 1])]
        above = [max(highs[i - look + 1: i + 1])]
        prior_sl = [lows[j] for j in sl_idx if j <= i]
        prior_sh = [highs[j] for j in sh_idx if j <= i]
        if prior_sl:
            below.append(prior_sl[-1])
        if prior_sh:
            above.append(prior_sh[-1])
        if ema50[i] is not None:
            (below if ema50[i] <= c else above).append(ema50[i])
        cand_below = [v for v in below if v is not None and v <= c]
        cand_above = [v for v in above if v is not None and v >= c]
        sup_list[i] = max(cand_below) if cand_below else None
        res_list[i] = min(cand_above) if cand_above else None
    ind["resistance"] = res_list
    ind["support"] = sup_list

    # ひとつ前の足からピボット
    piv: List[Optional[float]] = [None] * n
    r1: List[Optional[float]] = [None] * n
    s1: List[Optional[float]] = [None] * n
    prev_h: List[Optional[float]] = [None] * n
    prev_l: List[Optional[float]] = [None] * n
    for i in range(1, n):
        p = pivot_points(highs[i - 1], lows[i - 1], closes[i - 1])
        piv[i], r1[i], s1[i] = p["pivot"], p["r1"], p["s1"]
        prev_h[i], prev_l[i] = highs[i - 1], lows[i - 1]
    ind["pivot"], ind["pivot_r1"], ind["pivot_s1"] = piv, r1, s1
    ind["previous_high"], ind["previous_low"] = prev_h, prev_l

    # 転換点は上で一度求めてある。**同じものを二度計算しない。**
    # 1分析あたり9回呼ばれていたのがここの重複だった（時間足は4つしかない）。
    sh, sl = sh_idx, sl_idx
    swing_h: List[Optional[float]] = [None] * n
    swing_l: List[Optional[float]] = [None] * n
    if sh:
        swing_h[-1] = highs[sh[-1]]
    if sl:
        swing_l[-1] = lows[sl[-1]]
    ind["swing_high"], ind["swing_low"] = swing_h, swing_l
    return ind


def _snapshot(tf: str, ind: Dict) -> IndicatorSnapshot:
    def last(k: str) -> Optional[float]:
        arr = ind.get(k) or []
        v = arr[-1] if arr else None
        return None if v is None else round(float(v), 6)

    from .indicator_engine import percentile_of_last
    return IndicatorSnapshot(
        timeframe=tf,
        ema20=last("ema20"), ema50=last("ema50"), ema200=last("ema200"),
        sma20=last("sma20"), sma50=last("sma50"), sma200=last("sma200"),
        rsi14=last("rsi14"),
        macd=last("macd"), macd_signal=last("macd_signal"), macd_hist=last("macd_hist"),
        stoch_k=last("stoch_k"), stoch_d=last("stoch_d"),
        atr14=last("atr14"),
        atr_percentile=(lambda v: None if v is None else round(v, 4))(
            percentile_of_last(ind.get("atr14") or [], 100)),
        bb_upper=last("bb_upper"), bb_middle=last("bb_middle"),
        bb_lower=last("bb_lower"), bb_width=last("bb_width"),
        bb_width_percentile=(lambda v: None if v is None else round(v, 4))(
            percentile_of_last(ind.get("bb_width") or [], 100)),
        adx14=last("adx14"),
        pivot=last("pivot"), pivot_r1=last("pivot_r1"), pivot_s1=last("pivot_s1"),
        previous_high=last("previous_high"), previous_low=last("previous_low"),
        resistance=last("resistance"), support=last("support"),
        swing_high=last("swing_high"), swing_low=last("swing_low"),
        close=last("close"),
    )


def _bias(tf: str, ind: Dict, structure: Optional[MarketStructure] = None) -> TimeframeBias:
    e20 = (ind.get("ema20") or [None])[-1]
    e50 = (ind.get("ema50") or [None])[-1]
    d = Direction.NEUTRAL
    note = "移動平均が揃っていません"
    if e20 is not None and e50 is not None:
        if e20 > e50:
            d, note = Direction.LONG, "EMA20 が EMA50 の上"
        elif e20 < e50:
            d, note = Direction.SHORT, "EMA20 が EMA50 の下"
    return TimeframeBias(
        timeframe=tf, direction=d,
        structure=structure.structure if structure else StructureState.UNKNOWN,
        note=note,
    )


def _direction_int(b: TimeframeBias) -> int:
    return 1 if b.direction is Direction.LONG else (-1 if b.direction is Direction.SHORT else 0)


def _fallback(pair: str, reasons: List[str], quality: DataQuality,
              warnings: Optional[List[str]] = None) -> AnalysisResult:
    """分析できないときの結果。必ず NO_TRADE。"""
    empty = TimeframeBias(timeframe="-", direction=Direction.NEUTRAL)
    return AnalysisResult(
        pair=pair, timestamp=datetime.now(timezone.utc),
        direction=Direction.NEUTRAL, signal=Signal.NO_TRADE,
        score=0.0, quality=SetupQuality.NO_TRADE,
        score_breakdown=ScoreBreakdown(),
        regime=Regime.NO_TRADE, regime_score=0.0,
        news_state=NewsState.UNKNOWN,
        h1_bias=empty, h4_bias=empty, m15_setup=empty, m5_context=empty,
        market_structure=MarketStructure(),
        data_quality=quality,
        hard_blocked=True,          # 分析できなかった場合も強制の見送り
        filter_blocked=True,
        warnings=warnings or [],
        reasons=[],
        invalidation_reasons=reasons,
    )


def analyze_pair(symbol: str, provider: MarketDataProvider,
                 cfg: TradingConfig, now: Optional[datetime] = None) -> AnalysisResult:
    """1通貨ペアを分析する。例外は外へ出さず NO_TRADE として返す。"""
    now = now or datetime.now(timezone.utc)
    try:
        spec: PairSpec = cfg.pair(symbol)
    except KeyError as exc:
        return _fallback(symbol.upper(), [str(exc)], DataQuality.INVALID)

    status = provider.provider_status()
    if status.state in (ProviderState.OFFLINE, ProviderState.NOT_IMPLEMENTED):
        fr = provider_offline_result(status.detail or status.state.value)
        log.warning("供給元が使えません %s", spec.symbol, extra={
            "pair": spec.symbol, "provider": getattr(provider, "name", "?"),
            "reason": status.detail or status.state.value,
            "reason_code": ReasonCode.PROVIDER_OFFLINE})
        return _fallback(spec.symbol, fr.reasons, fr.data_quality)

    try:
        series_by_tf: Dict[str, CandleSeries] = {}
        ind_by_tf: Dict[str, Dict] = {}
        for tf in ANALYSIS_TIMEFRAMES:
            need = cfg.timeframes["min_candles"].get(tf, 250)
            s = provider.get_candles(spec.symbol, tf, limit=need)
            series_by_tf[tf] = s
            ind_by_tf[tf] = _enrich(s, compute_all(s, cfg.indicators), cfg.indicators)

        try:
            spread: Optional[SpreadInfo] = provider.get_spread(spec.symbol)
        except MarketDataError as exc:
            spread = None
            log.warning("スプレッドを取得できません %s: %s", spec.symbol, exc)

        structure_m15 = analyze_structure(series_by_tf["M15"], cfg.indicators)
        regime_res: RegimeResult = classify_regime(
            ind_by_tf["H1"], structure_m15, cfg.regime, NewsState.UNKNOWN)

        h4_b = _bias("H4", ind_by_tf["H4"])
        h1_b = _bias("H1", ind_by_tf["H1"])
        m15_b = _bias("M15", ind_by_tf["M15"], structure_m15)
        m5_b = _bias("M5", ind_by_tf["M5"])

        tf_minutes = {tf: cfg.timeframe_minutes(tf) for tf in ANALYSIS_TIMEFRAMES}
        icfg = cfg.filters.get("integrity", {})
        integrity = check_integrity(
            series_by_tf, tf_minutes, now,
            min_matching_ratio=icfg.get("min_matching_ratio", 0.8),
            max_unexplained_gaps=icfg.get("max_unexplained_gaps", 5))

        # 経済指標。**停止は強制条件、余裕は点数。** 分けて扱う。
        news_view = evaluate_news(spec, now, cfg.news)

        fr = check_all(
            spec=spec, series_by_tf=series_by_tf, indicators_by_tf=ind_by_tf,
            spread=spread, h1_dir=_direction_int(h1_b), h4_dir=_direction_int(h4_b),
            cfg=cfg.filters, now=now,
            timeframe_minutes=tf_minutes,
            integrity_reports=integrity,
            news=news_view,
        )

        # 相場つきに割り当てた戦略を順に試し、**最初に向きが出たものを採る。**
        # トレンド追随の条件を持ち合いにそのまま当てはめると、上限に
        # 近づくほど買いたくなる、という前提ごと逆の判断になる。
        #
        # **どれで入ったかを残す。** 混ぜて集計すると、効いていない戦略が
        # 効いている戦略の成績に紛れて見えなくなる。
        direction, dir_reasons, used = decide_strategy(
            StrategyContext(
                h1=ind_by_tf["H1"], h4=ind_by_tf["H4"], m15=ind_by_tf["M15"],
                m5=ind_by_tf["M5"], structure=structure_m15,
                regime=regime_res.regime, signal_cfg=cfg.signal, cfg={}),
            cfg.filters)
        strategy = used.name if used else None
        scoring = used.scoring if used else "trend"

        # 相関。材料が無い・弱い銘柄は UNKNOWN のまま（0点）。
        corr = None
        synthetic = bool(getattr(provider, "is_synthetic", False))
        if synthetic:
            # 合成データを実勢の材料と突き合わせても意味がない。
            # 時刻がたまたま合うだけで、数字は相関を表していない。
            from .correlation import CorrelationResult
            corr = CorrelationResult(
                state="UNAVAILABLE",
                reasons=["合成データのため、実勢の材料とは突き合わせません"])
        elif cfg.signal.get("data_available", {}).get("correlation", False):
            sign = (1 if direction is Direction.LONG
                    else (-1 if direction is Direction.SHORT else 0))
            corr_tf = cfg.correlation["timeframe"]
            src = series_by_tf.get(corr_tf)
            if src is not None:
                corr = evaluate_correlation(
                    [(c.timestamp, c.close) for c in src.candles],
                    sign, cfg.correlation)

        breakdown, score_reasons = score_setup(
            direction=direction,
            h1_ind=ind_by_tf["H1"], h4_ind=ind_by_tf["H4"],
            m15_ind=ind_by_tf["M15"], m5_ind=ind_by_tf["M5"],
            m15_structure=structure_m15, regime_result=regime_res,
            spread=spread, max_spread_pips=cfg.max_spread_pips(spec.symbol),
            cfg=cfg.signal, now=now, correlation=corr,
            news=news_view, strategy=scoring)

        score = min(100.0, max(0.0, breakdown.total))
        # 測れなかった項目は満点から外す。「測ったが弱い」は外さない
        # （確認できないことは減点として残す）。
        unavailable = []
        if corr is not None and corr.state == "UNAVAILABLE":
            unavailable.append("correlation")
        if not news_view.available:
            unavailable.append("news")
        quality = classify_quality(score, cfg.signal, unavailable)
        signal, invalidation = decide_signal(
            direction, quality, fr,
            regime=regime_res.regime, filters_cfg=cfg.filters)
        if fr.blocked:
            log.info("強制条件で見送りました %s", spec.symbol, extra={
                "pair": spec.symbol, "signal": signal.value,
                "reason": " / ".join(fr.reasons[:3]),
                "reason_code": ReasonCode.HARD_FILTER})

        # 有効期限。依存しているものの中でいちばん早く切れるものに合わせる。
        fcfg = cfg.filters.get("freshness", {})
        setup_tf = cfg.role_timeframe("setup")
        last_setup_bar = series_by_tf[setup_tf].last
        expires = valid_until(
            now=now,
            analysis_ttl_seconds=float(fcfg.get("analysis_ttl_seconds", 60)),
            quote_timestamp=spread.timestamp if spread else None,
            quote_max_age_seconds=float(fcfg["quote_max_age_seconds"])
            if "quote_max_age_seconds" in fcfg else None,
            next_bar_close=next_bar_close(last_setup_bar.timestamp,
                                          cfg.timeframe_minutes(setup_tf))
            if last_setup_bar else None,
        )

        # どの戦略で見たのかは dir_reasons の先頭に入っている。同じ場面でも
        # 戦略が違えば向きの意味が変わるので、読む側が前提を取り違えない
        # ようにするため。
        reasons = dir_reasons + score_reasons + regime_res.reasons
        return AnalysisResult(
            pair=spec.symbol, timestamp=now,
            direction=direction if signal in (Signal.BUY, Signal.SELL) else direction,
            signal=signal, score=round(score, 2), quality=quality,
            score_breakdown=breakdown,
            regime=regime_res.regime, regime_score=regime_res.confidence,
            strategy=strategy,
            news_state=news_view.state,
            correlation=corr.as_dict() if corr is not None else None,
            news=news_view.as_dict(),
            h1_bias=h1_b, h4_bias=h4_b, m15_setup=m15_b, m5_context=m5_b,
            market_structure=structure_m15,
            spread=spread,
            valid_until=expires,
            data_quality=fr.data_quality,
            hard_blocked=fr.blocked,
            filter_blocked=fr.blocked,
            indicators={tf: _snapshot(tf, ind_by_tf[tf]) for tf in ANALYSIS_TIMEFRAMES},
            warnings=fr.warnings,
            reasons=reasons,
            invalidation_reasons=invalidation,
        )

    except MarketDataError as exc:
        log.warning("相場データを取得できません %s: %s", spec.symbol, exc,
                    extra={"pair": spec.symbol, "reason": str(exc),
                           "reason_code": ReasonCode.PROVIDER_ERROR})
        fr = error_result(str(exc), DataQuality.PROVIDER_ERROR)
        return _fallback(spec.symbol, fr.reasons, fr.data_quality)
    except Exception as exc:                      # 想定外も必ず NO_TRADE へ
        log.exception("分析中に例外 %s", spec.symbol,
                      extra={"pair": spec.symbol, "reason": str(exc),
                             "reason_code": ReasonCode.ANALYSIS_ERROR})
        fr = error_result(f"分析中に想定外の問題が起きました: {exc}")
        return _fallback(spec.symbol, fr.reasons, fr.data_quality,
                         warnings=["この銘柄の結果は信用しないでください"])


# チャートの口（main.py）からも同じ計算を使う。**作り直さない。**
# 別々に持つと、engine が見ている帯と画面の帯が静かに食い違う。
enrich_indicators = _enrich
