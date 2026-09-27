# -*- coding: utf-8 -*-
"""強制的な見送り（NO_TRADE）の条件。

**点数付けとは別系統にしてある。** 点数が90点でも、ここに1つでも当たれば
NO_TRADE になる。資金の保全を最優先という原則を、コードの構造として表した
もの。ここを点数に混ぜると「高得点なら多少の異常は許す」という抜け道が
できてしまう。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .config import PairSpec
from .freshness import market_minutes_between
from .models import (
    CandleSeries,
    DataQuality,
    SpreadInfo,
)


@dataclass
class FilterResult:
    """判定の結果。blocked が真なら、何があっても取引しない。"""

    blocked: bool = False
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    data_quality: DataQuality = DataQuality.OK

    def block(self, reason: str, quality: DataQuality = DataQuality.OK) -> None:
        self.blocked = True
        self.reasons.append(reason)
        if quality is not DataQuality.OK:
            self.data_quality = quality

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def check_all(
    spec: PairSpec,
    series_by_tf: Dict[str, CandleSeries],
    indicators_by_tf: Dict[str, Dict[str, List[Optional[float]]]],
    spread: Optional[SpreadInfo],
    h1_dir: int,
    h4_dir: int,
    cfg: Dict,
    now: Optional[datetime] = None,
    timeframe_minutes: Optional[Dict[str, int]] = None,
    integrity_reports: Optional[List] = None,
    news: Optional[object] = None,
) -> FilterResult:
    """すべての強制条件を確かめる。1つでも当たれば取引しない。"""
    res = FilterResult()
    now = now or datetime.now(timezone.utc)
    tf_minutes = timeframe_minutes or {}

    # --- 日計り：刻限まで時間が残っているか ---
    #
    # **翌日に持ち越さないので、間に合わない場面では建てない。**
    # 点数で表さないのは、他が高ければ上書きできてしまうから。
    # 刻限そのものは day_trade.py（ロールオーバーの手前、夏時間で1時間ずれる）。
    from .day_trade import can_open

    ok, why = can_open(cfg, now)
    if not ok and why:
        res.block(why)

    # --- 足の本数 ---
    for tf, need in cfg["min_candles"].items():
        series = series_by_tf.get(tf)
        if series is None:
            res.block(f"{tf} の足を取得できていません", DataQuality.INSUFFICIENT)
            continue
        if len(series) < need:
            res.block(
                f"{tf} の足が {len(series)} 本しかありません（{need} 本必要）",
                DataQuality.INSUFFICIENT)

    # --- 鮮度 ---
    #
    # **閉場していた時間を経過に数えない。** 数えると、日曜再開直後は
    # 直前の H4 足が52時間前になり、許容12時間を超えて全銘柄が止まる。
    # 市場が閉まっていた時間は、データが古くなった時間ではない。
    stale_cfg = cfg["stale_data"]
    max_behind = stale_cfg["max_bars_behind"]
    market_only = stale_cfg.get("count_market_hours_only", False)
    for tf, series in series_by_tf.items():
        if not series.candles:
            continue
        minutes = tf_minutes.get(tf)
        if not minutes:
            continue
        last = series.last.timestamp
        age = (market_minutes_between(last, now) if market_only
               else (now - last).total_seconds() / 60.0)
        if age > minutes * max_behind:
            label = "取引時間で" if market_only else ""
            res.block(
                f"{tf} の最後の足が{label} {age / 60:.1f} 時間前で、古すぎます",
                DataQuality.STALE)

    # --- スプレッド ---
    if spread is None:
        res.block("スプレッドを取得できていません", DataQuality.PROVIDER_ERROR)
    else:
        max_pips = cfg["spread"]["max_pips"].get(
            spec.symbol, cfg["spread"]["max_pips"]["default"])
        if spread.spread_pips > max_pips:
            res.block(
                f"スプレッドが {spread.spread_pips:.1f}pips で、上限 {max_pips:.1f}pips を超えています")
        # **損切り幅を決めている足と同じ ATR と比べる。**
        # ここが M5 のまま、損切りは M15 の ATR で決めていた時期がある。
        # M5 の ATR は M15 の半分ほどなので、意図の倍ほど厳しくなっていた。
        # 費用が見合うかは「実際に賭ける幅」と比べないと判断できない。
        entry_tf = cfg["spread"].get("atr_timeframe", "M15")
        ind = indicators_by_tf.get(entry_tf, {})
        atr_list = ind.get("atr14") or []
        atr_v = atr_list[-1] if atr_list else None
        if atr_v:
            ratio = spread.spread_price / atr_v
            if ratio > cfg["spread"]["max_ratio_of_atr"]:
                res.block(
                    f"スプレッドが ATR の {ratio:.0%} に達しています"
                    f"（上限 {cfg['spread']['max_ratio_of_atr']:.0%}）")

    # --- 損切り幅を決められるか ---
    if cfg["risk_reward"]["require_atr"]:
        ind = indicators_by_tf.get(
            cfg["risk_reward"].get("atr_timeframe", "M15"), {})
        atr_list = ind.get("atr14") or []
        atr_v = atr_list[-1] if atr_list else None
        if not atr_v or atr_v <= cfg["risk_reward"]["min_atr_value"]:
            res.block("ATR を計算できず、損切り幅を決められません",
                      DataQuality.INSUFFICIENT)

    # --- 極端な変動 ---
    from .indicator_engine import relative_level
    ind_h1 = indicators_by_tf.get("H1", {})
    atr_ratio = relative_level(ind_h1.get("atr14") or [], 100)
    if atr_ratio is not None:
        if atr_ratio > cfg["volatility"]["max_atr_ratio"]:
            res.block(f"ATR が普段の {atr_ratio:.1f} 倍で、変動が異常です")
        elif atr_ratio < cfg["volatility"]["min_atr_ratio"]:
            res.block(f"ATR が普段の {atr_ratio:.1f} 倍しかなく、値動きがありません")

    # --- 上位足と下位足の向きの衝突 ---
    conflict = cfg["conflict"]
    if conflict["block_on_h1_h4_conflict"]:
        if h1_dir != 0 and h4_dir != 0 and h1_dir != h4_dir:
            res.block("H1 と H4 の向きが逆です（上位足と下位足が競合）")
        elif conflict.get("require_known_higher_bias", False) and h4_dir == 0:
            # **分からないことを「競合なし」として通してはいけない。**
            # 通せば、上位足を確かめないまま下位足だけで建てることになる。
            res.block("H4 の向きが判定できません（上位足が不明のまま進めない）",
                      DataQuality.INSUFFICIENT)

    # --- 経済指標（発表前後・予定表が古い）---
    #
    # **点数ではなく、ここで止める。** 点数にすると高得点で上書きできる。
    if news is not None and getattr(news, "blocked", False):
        for reason in getattr(news, "reasons", []):
            res.block(reason, DataQuality.OK)

    # --- 足の素性（本数が揃っていることと、正しい足であることは別）---
    integrity = cfg.get("integrity", {})
    if integrity.get("require_native_timeframe", False) and integrity_reports:
        for r in integrity_reports:
            if not r.ok:
                for reason in r.reasons:
                    res.block(reason, DataQuality.INVALID)
            if r.unclosed_last_bar and integrity.get(
                    "block_on_unclosed_last_bar", True):
                res.block(
                    f"{r.timeframe}: 最後の足が未確定です"
                    f"（未確定の足を確定として扱わない）", DataQuality.STALE)

    return res


def provider_offline_result(detail: str) -> FilterResult:
    """供給元が落ちているときの結果。分析に入る前に使う。"""
    res = FilterResult()
    res.block(f"相場データの供給元が利用できません: {detail}",
              DataQuality.PROVIDER_ERROR)
    return res


def error_result(message: str, quality: DataQuality = DataQuality.INVALID) -> FilterResult:
    """想定外の失敗。**BUY/SELL に倒さず必ず NO_TRADE にする。**"""
    res = FilterResult()
    res.block(message, quality)
    return res
