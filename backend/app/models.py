# -*- coding: utf-8 -*-
"""データ模型。

**壊れた足は黙って直さず、弾く。** 高値が安値より低いような足を「たぶんこう
だろう」と補正すると、その先の指標も判定も静かに狂う。どこで狂ったか後から
追えなくなるので、受け取った時点で拒否する。
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------- 列挙

class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"
    NO_TRADE = "NO_TRADE"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class Regime(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    BREAKOUT = "BREAKOUT"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    NEWS = "NEWS"
    TRANSITION = "TRANSITION"
    NO_TRADE = "NO_TRADE"


class NewsState(str, Enum):
    """Phase 1 では指標カレンダーを持たない。捏造せず UNKNOWN を返す。"""

    UNKNOWN = "UNKNOWN"
    QUIET = "QUIET"
    UPCOMING = "UPCOMING"
    ACTIVE = "ACTIVE"


class StructureState(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class SetupQuality(str, Enum):
    NO_TRADE = "NO_TRADE"
    WATCH = "WATCH"
    SETUP = "SETUP"
    STRONG_SETUP = "STRONG_SETUP"
    VERY_STRONG_SETUP = "VERY_STRONG_SETUP"


class DataQuality(str, Enum):
    OK = "OK"
    STALE = "STALE"
    INSUFFICIENT = "INSUFFICIENT"
    INVALID = "INVALID"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class ProviderState(str, Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


# ---------------------------------------------------------------- 足

class Candle(BaseModel):
    """1本の足。壊れていれば作成時点で失敗する。"""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None

    model_config = {"frozen": True}

    @field_validator("timestamp")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        # 時刻帯のない値は UTC とみなす。混在させると比較が狂う。
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @field_validator("open", "high", "low", "close")
    @classmethod
    def _finite_positive(cls, v: float) -> float:
        # **有限であることを先に確かめる。**
        # inf は `v > 0` を通ってしまう。通せば ATR も EMA も inf になり、
        # 以降の比較がすべて無意味になったまま計算が続く。
        if not math.isfinite(v):
            raise ValueError("価格が有限の数ではありません")
        if not (v > 0):
            raise ValueError("価格は正の数でなければなりません")
        return v

    @field_validator("volume")
    @classmethod
    def _volume_non_negative(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if not math.isfinite(v):
            raise ValueError("出来高が有限の数ではありません")
        if v < 0:
            raise ValueError("出来高は負になりません")
        return v

    @model_validator(mode="after")
    def _ohlc_consistent(self) -> "Candle":
        if self.high < self.low:
            raise ValueError("high が low を下回っています")
        if self.high < self.open or self.high < self.close:
            raise ValueError("high が open/close を下回っています")
        if self.low > self.open or self.low > self.close:
            raise ValueError("low が open/close を上回っています")
        return self


class CandleSeries(BaseModel):
    """1銘柄・1時間足ぶんの足の並び。時刻は昇順・重複なしを保証する。"""

    pair: str
    timeframe: str
    candles: List[Candle]

    @model_validator(mode="after")
    def _ordered(self) -> "CandleSeries":
        ts = [c.timestamp for c in self.candles]
        if any(ts[i] >= ts[i + 1] for i in range(len(ts) - 1)):
            raise ValueError("足の時刻が昇順ではない、または重複しています")
        return self

    def __len__(self) -> int:
        return len(self.candles)

    @property
    def closes(self) -> List[float]:
        return [c.close for c in self.candles]

    @property
    def highs(self) -> List[float]:
        return [c.high for c in self.candles]

    @property
    def lows(self) -> List[float]:
        return [c.low for c in self.candles]

    @property
    def last(self) -> Optional[Candle]:
        return self.candles[-1] if self.candles else None


class Quote(BaseModel):
    """気配値。"""

    pair: str
    bid: float
    ask: float
    timestamp: datetime

    @field_validator("bid", "ask")
    @classmethod
    def _finite_positive(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("気配値が有限の数ではありません")
        if not (v > 0):
            raise ValueError("気配値は正の数でなければなりません")
        return v

    @field_validator("timestamp")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _spread_valid(self) -> "Quote":
        if self.ask < self.bid:
            raise ValueError("ask が bid を下回っています")
        return self

    def age_seconds(self, now: Optional[datetime] = None) -> float:
        """この気配値が何秒前のものか。負にはしない（未来は0扱い）。"""
        now = now or datetime.now(timezone.utc)
        return max(0.0, (now - self.timestamp).total_seconds())

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


class SpreadInfo(BaseModel):
    pair: str
    spread_price: float
    spread_pips: float
    timestamp: datetime


class ProviderStatus(BaseModel):
    name: str
    state: ProviderState
    detail: str = ""
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------- 分析結果

class IndicatorSnapshot(BaseModel):
    """ある時間足の、最後の足における指標の値。"""

    timeframe: str
    ema20: Optional[float] = None
    ema50: Optional[float] = None
    ema200: Optional[float] = None
    sma20: Optional[float] = None
    sma50: Optional[float] = None
    sma200: Optional[float] = None
    rsi14: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    stoch_k: Optional[float] = None
    stoch_d: Optional[float] = None
    atr14: Optional[float] = None
    atr_percentile: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_width: Optional[float] = None
    bb_width_percentile: Optional[float] = None
    adx14: Optional[float] = None
    pivot: Optional[float] = None
    pivot_r1: Optional[float] = None
    pivot_s1: Optional[float] = None
    previous_high: Optional[float] = None
    previous_low: Optional[float] = None
    resistance: Optional[float] = None
    support: Optional[float] = None
    swing_high: Optional[float] = None
    swing_low: Optional[float] = None
    close: Optional[float] = None


class MarketStructure(BaseModel):
    structure: StructureState = StructureState.UNKNOWN
    last_swing_high: Optional[float] = None
    last_swing_low: Optional[float] = None
    bos: bool = False
    choch: bool = False
    pattern: List[str] = Field(default_factory=list, description="HH/HL/LH/LL の並び")


class RegimeResult(BaseModel):
    regime: Regime
    confidence: float = Field(ge=0, le=100, description="0-100の点数。確率ではない。")
    news_state: NewsState = NewsState.UNKNOWN
    factors: Dict[str, Any] = Field(default_factory=dict)
    reasons: List[str] = Field(default_factory=list)


class ScoreBreakdown(BaseModel):
    """配点の内訳。``config/signal_weights.json`` の9項目に対応する。

    ``correlation`` と ``news`` は材料を持っていないので常に 0。
    **0 が入っているのは「中立」ではなく「分かっていない」という意味。**
    """

    trend: float = 0.0
    momentum: float = 0.0
    support_resistance: float = 0.0
    volatility: float = 0.0
    price_action: float = 0.0
    correlation: float = 0.0
    news: float = 0.0
    session: float = 0.0
    spread_risk: float = 0.0

    @property
    def total(self) -> float:
        return round(
            self.trend + self.momentum + self.support_resistance
            + self.volatility + self.price_action + self.correlation
            + self.news + self.session + self.spread_risk, 2)


class TimeframeBias(BaseModel):
    timeframe: str
    direction: Direction = Direction.NEUTRAL
    structure: StructureState = StructureState.UNKNOWN
    note: str = ""


class AnalysisResult(BaseModel):
    """1通貨ペアの分析結果。仕様書の出力様式にあわせている。"""

    # **知らない項目を黙って捨てない。** 既定の pydantic は未知の引数を
    # 無視するので、綴りを間違えた項目が「入ったつもり」で消える。実際、
    # correlation を別のクラスに足してしまい、API に出ないまま気づけなかった。
    model_config = {"extra": "forbid"}

    pair: str
    timestamp: datetime
    direction: Direction
    signal: Signal
    score: float = Field(ge=0, le=100)
    quality: SetupQuality
    score_breakdown: ScoreBreakdown
    regime: Regime
    regime_score: float
    # どの戦略で向きを決めたか。**混ぜて集計しないために残す。**
    # 効いていない戦略が、効いている戦略の成績に紛れて見えなくなる。
    strategy: Optional[str] = None
    news_state: NewsState = NewsState.UNKNOWN
    # 相関の内訳。材料が無ければ state は UNAVAILABLE。
    correlation: Optional[Dict[str, Any]] = None
    # 指標の状況。停止は強制条件、余裕は点数。
    news: Optional[Dict[str, Any]] = None
    h1_bias: TimeframeBias
    h4_bias: TimeframeBias
    m15_setup: TimeframeBias
    m5_context: TimeframeBias
    market_structure: MarketStructure
    spread: Optional[SpreadInfo] = None
    # この判断がいつまで有効か。依存しているものの中でいちばん早く切れる
    # ものに合わせる。長いほうに合わせると、根拠が失われた BUY を
    # 画面に出し続けることになる。
    valid_until: Optional[datetime] = None
    data_quality: DataQuality = DataQuality.OK
    # 強制条件で弾かれたか。点数が低くて見送りなのとは**別物**。
    # これを区別しないと、検証で「費用が見合わない場面」まで基準に混ざり、
    # 点数の良し悪しを測れなくなる。実際そうなった。
    hard_blocked: bool = False
    # **強制条件で止まったか**だけを持つ。hard_blocked は期限切れでも立つ
    # （expired_view が立てる）ので、それと分けておく。朝のボードは期限の
    # 話ではなく場面の話をするので、こちらを見る。混ぜると「様子見の水準
    # です」のような説明まで「除外の理由」として出てしまった。
    filter_blocked: bool = False
    indicators: Dict[str, IndicatorSnapshot] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    invalidation_reasons: List[str] = Field(default_factory=list)

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """期限が切れているか。**読み出すたびに確かめる。**

        作った時点で有効でも、キャッシュから読んだ時点・画面に出た時点で
        切れていることがある。切れた判断を BUY/SELL のまま見せない。
        """
        if self.valid_until is None:
            return False
        return (now or datetime.now(timezone.utc)) >= self.valid_until

    def seconds_remaining(self, now: Optional[datetime] = None) -> Optional[float]:
        if self.valid_until is None:
            return None
        return (self.valid_until
                - (now or datetime.now(timezone.utc))).total_seconds()

    def expired_view(self, now: Optional[datetime] = None) -> "AnalysisResult":
        """期限切れとして返す姿。**BUY/SELL を残さない。**"""
        if not self.is_expired(now):
            return self
        return self.model_copy(update={
            "signal": Signal.NO_TRADE,
            "direction": Direction.NEUTRAL,
            "hard_blocked": True,
            "invalidation_reasons": list(self.invalidation_reasons)
            + ["この判断は有効期限が切れています（作り直しが必要）"],
        })


class RankingEntry(BaseModel):
    rank: int
    pair: str
    direction: Direction
    signal: Signal
    score: float
    quality: SetupQuality
    regime: Regime
    # どの戦略で向きを決めたか。一覧でも見えるようにする。
    # **戦略ごとに別物なので、混ぜて眺めると読み違える。**
    strategy: Optional[str] = None
    # 支援する銘柄かどうか。**費用で分けている**（focus.py）。
    # 勝てるかどうかでは分けていない。それは測れていない。
    tier: str = "unknown"
    spread_pips: Optional[float] = None
    warning_count: int = 0
    data_quality: DataQuality = DataQuality.OK
    # 一覧でも期限を持たせる。画面側でも切れていないか確かめられるように。
    valid_until: Optional[datetime] = None
    # 見送りの理由（先頭のみ）。壊れたペアを黙って消さず、理由付きで残す。
    reason: Optional[str] = None


class RankingResponse(BaseModel):
    version: str
    generated_at: datetime
    provider: ProviderStatus
    entries: List[RankingEntry]
    analyzed: int
    failed: int


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    market_data: str
    provider_state: ProviderState
    timestamp: datetime
