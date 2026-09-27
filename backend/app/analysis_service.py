# -*- coding: utf-8 -*-
"""分析の入口。**同じ銘柄を同じ瞬間に何度も計算し直さない。**

API も一覧も、分析はここを通す。直接 ``analyze_pair`` を呼ぶと、
同じ計算が並走して全体が遅くなる。実際、一覧の更新中に詳細を開くと
1件 0.5秒の要求が 4秒 になっていた。

**安全の条件は緩めない。** 期限切れは返さないし、強制条件もそのまま。
使い回すのは「同じ瞬間の同じ銘柄」だけ。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .analysis import analyze_pair
from .cache import ResultCache
from .config import TradingConfig
from .market_data import MarketDataProvider
from .models import AnalysisResult

_cache: Optional[ResultCache] = None
_cache_ttl: Optional[float] = None


def get_cache(cfg: TradingConfig) -> ResultCache:
    global _cache, _cache_ttl
    ttl = float(cfg.filters.get("freshness", {}).get("cache_ttl_seconds", 0))
    if _cache is None or _cache_ttl != ttl:
        _cache = ResultCache(ttl_seconds=ttl)
        _cache_ttl = ttl
    return _cache


def reset_cache() -> None:
    """試験用。設定を替えたときにも呼ぶ。"""
    global _cache, _cache_ttl
    _cache = None
    _cache_ttl = None


def analyze(symbol: str, provider: MarketDataProvider, cfg: TradingConfig,
            now: Optional[datetime] = None) -> AnalysisResult:
    """分析する。同じ瞬間の同じ銘柄なら結果を使い回す。

    ``now`` を明示したとき（検証など）は使い回さない。過去の時刻で
    呼んだ結果が「いま」のものとして残ると、静かに間違える。
    """
    if now is not None:
        return analyze_pair(symbol, provider, cfg, now=now)

    cache = get_cache(cfg)
    if cache.ttl <= 0:
        return analyze_pair(symbol, provider, cfg)

    # **供給元の名前だけでは足りない。** 同じ "mock" でも、別の
    # インスタンス（置き場の違う CSV、試験用の壊れた供給元）は別物。
    # 名前で束ねると、壊れた供給元が正常な結果を受け取ってしまう。
    key = f"{getattr(provider, 'name', '?')}#{id(provider):x}:{symbol.upper()}"
    result = cache.get_or_compute(
        key, lambda: analyze_pair(symbol, provider, cfg))

    # **返す直前にもう一度期限を見る。** 使い回した結果が、返す時点で
    # 切れていることがある。
    if result.is_expired(datetime.now(timezone.utc)):
        cache.invalidate(key)
        result = analyze_pair(symbol, provider, cfg)
    return result
