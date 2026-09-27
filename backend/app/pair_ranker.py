# -*- coding: utf-8 -*-
"""全通貨ペアを分析して並べる。

決まりが2つある。

1. **1つの銘柄が壊れても、全体を止めない。** その銘柄だけ NO_TRADE と警告に
   して先へ進む。26銘柄のうち1つの不調で一覧が出なくなる方が困る。
2. **NO_TRADE の銘柄も一覧から消さない。** 下に置くだけにする。なぜ弾かれた
   かは、不具合を追うときに要る情報だから。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, List, Optional

from .analysis_service import analyze
from .config import TradingConfig
from .focus import tier_of
from .market_data import MarketDataProvider
from .models import (
    AnalysisResult,
    DataQuality,
    RankingEntry,
    RankingResponse,
    Signal,
)

from .logging_setup import ReasonCode

log = logging.getLogger(__name__)

# 並べる優先順位。実際に建てられるものを上に出す。
_SIGNAL_ORDER = {Signal.BUY: 0, Signal.SELL: 0, Signal.WAIT: 1, Signal.NO_TRADE: 2}


def _sort_key(e: RankingEntry):
    return (_SIGNAL_ORDER.get(e.signal, 3), -e.score, e.warning_count, e.pair)


def rank_pairs(provider: MarketDataProvider, cfg: TradingConfig,
               symbols: Optional[List[str]] = None,
               now: Optional[datetime] = None,
               on_result: Optional[Callable[[AnalysisResult], None]] = None
               ) -> RankingResponse:
    """全銘柄を並べる。

    ``on_result`` は1銘柄ぶんの結果を渡す。記録のためにある。
    **呼び出し側で analyze をやり直さないため**で、やり直すと使い回しの
    外れた銘柄だけ26銘柄ぶんの計算がもう一度走る。

    渡すのは**期限を見たあとの結果**。期限切れを落とす前に渡していた
    ため、一覧には NO_TRADE と出ているのに、記録と朝の確認には WAIT が
    残っていた。**利用者が見ていないものを記録していた。**
    """
    now = now or datetime.now(timezone.utc)
    targets = symbols or [p.symbol for p in cfg.enabled_pairs()]

    results: List[AnalysisResult] = []
    failed = 0
    for sym in targets:
        try:
            # now を渡さないのは、**一覧と詳細で同じ結果を使い回すため**。
            # 明示の時刻で呼ぶと使い回しが効かず、詳細を開くたびに
            # 26銘柄ぶんの計算がもう一度走る。銘柄ごとに数ミリ秒ずれるが、
            # 時間帯の点も鮮度もその差では変わらない。
            results.append(analyze(sym, provider, cfg))
        except Exception as exc:      # analyze_pair 側で拾うが、念のため
            log.exception("順位付けの途中で例外 %s", sym, extra={
                "pair": sym, "reason": str(exc),
                "reason_code": ReasonCode.ANALYSIS_ERROR})
            failed += 1
            results.append(_error_entry_result(sym, str(exc), now))

    # **一覧を組む前に期限を見る。** 切れた判断を BUY/SELL のまま並べない。
    results = [r.expired_view(now) if r.is_expired(now) else r for r in results]

    # 記録に渡すのはここ。**画面に出すのと同じものを渡す。**
    if on_result is not None:
        for r in results:
            try:
                on_result(r)
            except Exception:          # 記録のために一覧を止めない
                log.exception("判断の記録に失敗しました %s", r.pair)

    entries = [
        RankingEntry(
            rank=0, pair=r.pair, direction=r.direction, signal=r.signal,
            score=r.score, quality=r.quality, regime=r.regime,
            strategy=r.strategy,
            tier=tier_of(cfg, r.pair),
            spread_pips=(round(r.spread.spread_pips, 2) if r.spread else None),
            warning_count=len(r.warnings) + len(r.invalidation_reasons),
            data_quality=r.data_quality,
            valid_until=r.valid_until,
            # 弾かれたペアも黙って消さず、理由を1つ持たせて下位に残す。
            reason=(r.invalidation_reasons[0] if r.invalidation_reasons else None),
        )
        for r in results
    ]
    entries.sort(key=_sort_key)
    for i, e in enumerate(entries, start=1):
        e.rank = i

    failed += sum(1 for r in results if r.data_quality is not DataQuality.OK)
    return RankingResponse(
        version=cfg.signal.get("version", "0.1.0") if isinstance(cfg.signal, dict) else "0.1.0",
        generated_at=now,
        provider=provider.provider_status(),
        entries=entries,
        analyzed=len(entries),
        failed=failed,
    )


def _error_entry_result(symbol: str, message: str, now: datetime) -> AnalysisResult:
    from .analysis import _fallback
    return _fallback(symbol.upper(), [f"順位付けの途中で問題が起きました: {message}"],
                     DataQuality.INVALID)
