# -*- coding: utf-8 -*-
"""建玉に必要なレートを集める（Phase 2）。

計画を作るには、その銘柄の値段だけでは足りない。EURGBP の損益は GBP で
出るので、円に直すには GBPJPY が要る。証拠金は EUR の量で決まるので
EURJPY が要る。**足りないまま計算すると、損失額が桁違いに見える。**

ここは「どのレートが要るか」を洗い出して集めるだけの層。
集まらなければ、集まらなかったことを返す。**1.0 で代用しない。**
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Tuple

from .config import PairSpec, TradingConfig
from .logging_setup import ReasonCode
from .market_data import MarketDataError, MarketDataProvider
from .risk_engine import conversion_symbol

PHASE = 2
log = logging.getLogger(__name__)


def required_symbols(spec: PairSpec, quote_currency: str = "JPY") -> List[str]:
    """この銘柄の計画に必要な換算ペア。

    - 損益の換算: 決済通貨（quote）→ 口座通貨
    - 証拠金の換算: 基軸通貨（base）→ 口座通貨
    """
    out: List[str] = []
    for cur in (spec.quote, spec.base):
        sym = conversion_symbol(cur, quote_currency)
        if sym and sym not in out:
            out.append(sym)
    return out


def collect_rates(spec: PairSpec, provider: MarketDataProvider,
                  cfg: TradingConfig,
                  quote_currency: str = "JPY") -> Tuple[Dict[str, float], List[str]]:
    """必要な換算レートを集める。

    返すのは (集まったレート, 足りなかったペア名)。
    足りないものがあれば、呼び手はそれを理由に建てさせない。
    """
    rates: Dict[str, float] = {}
    missing: List[str] = []
    known = {p.symbol for p in cfg.pairs}

    for sym in required_symbols(spec, quote_currency):
        if sym not in known:
            missing.append(sym)
            log.warning("換算レートの銘柄が設定にありません", extra={
                "pair": sym, "reason_code": ReasonCode.INSUFFICIENT_DATA})
            continue
        try:
            q = provider.get_latest_price(sym)
        except (MarketDataError, KeyError, NotImplementedError) as exc:
            missing.append(sym)
            log.warning("換算レートを取得できません", extra={
                "pair": sym, "reason": str(exc),
                "reason_code": ReasonCode.PROVIDER_ERROR})
            continue
        rates[sym] = q.mid
    return rates, missing


def collect_rates_for(specs: Sequence[PairSpec], provider: MarketDataProvider,
                      cfg: TradingConfig,
                      quote_currency: str = "JPY") -> Dict[str, float]:
    """複数銘柄ぶんをまとめて集める（一覧表示用）。1件の失敗で止めない。"""
    rates: Dict[str, float] = {}
    wanted: List[str] = []
    for spec in specs:
        for sym in required_symbols(spec, quote_currency):
            if sym not in wanted:
                wanted.append(sym)
    known = {p.symbol for p in cfg.pairs}
    for sym in wanted:
        if sym not in known:
            continue
        try:
            rates[sym] = provider.get_latest_price(sym).mid
        except (MarketDataError, KeyError, NotImplementedError):
            continue          # 足りない分は呼び手が missing として扱う
    return rates


def entry_zone(reference: float, atr: float,
               width_atr: float = 0.15) -> Tuple[float, float]:
    """建値の帯。

    **1点で当てられるふりをしない。** 147.820 と書くと、そこで約定する
    前提に見える。実際には滑るし、指値なら届かないこともある。
    """
    half = width_atr * atr
    return (reference - half, reference + half)


def order_hint(is_long: bool, reference: float, low: float, high: float,
               last_price: Optional[float] = None) -> str:
    """成行か指値か、どちらが素直かの目安。

    **発注はしない。** 判断の材料として言葉で返すだけ。
    """
    px = last_price if last_price is not None else reference
    if low <= px <= high:
        return "いまの値段は建値の帯の中です（成行でも帯の中に収まります）"
    if (is_long and px > high) or (not is_long and px < low):
        return "いまの値段は帯の外側に出ています（追いかけず、指値で待つ形になります）"
    return "いまの値段は帯より手前です（帯に入るまで待つ形になります）"
