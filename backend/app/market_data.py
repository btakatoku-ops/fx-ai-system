# -*- coding: utf-8 -*-
"""相場データの供給元。

**供給元を後から差し替えても、分析の中身を書き直さなくて済む**ようにする。
そのために抽象の口を先に決め、実装はその後ろに隠す。

まだ繋いでいない供給元は、それらしく動く真似をさせず `NotImplementedError`
を投げる。動いているように見えて実は架空、という状態が一番危ない。
"""
from __future__ import annotations

import csv
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import PairSpec, get_trading_config
from .models import (
    Candle,
    CandleSeries,
    ProviderState,
    ProviderStatus,
    Quote,
    SpreadInfo,
)
from .synthetic import generate_series, scenario_for_pair

log = logging.getLogger(__name__)


class MarketDataError(RuntimeError):
    """供給元の側の問題。呼ぶ側は NO_TRADE に倒すこと。"""


# ---------------------------------------------------------------- 抽象

class MarketDataProvider(ABC):
    """相場データの供給元が満たすべき口。"""

    name: str = "abstract"

    # 合成データか。**実勢の材料（ドル指数など）と突き合わせてよいのは
    # 実勢データだけ。** 合成と実勢の相関は、時刻がたまたま合っただけの
    # 数字になる。
    is_synthetic: bool = False

    @abstractmethod
    def get_latest_price(self, pair: str) -> Quote:
        """最新の気配値。"""

    @abstractmethod
    def get_candles(self, pair: str, timeframe: str, limit: int = 300) -> CandleSeries:
        """足の並び。新しいものが末尾。"""

    @abstractmethod
    def get_spread(self, pair: str) -> SpreadInfo:
        """いまのスプレッド。"""

    @abstractmethod
    def provider_status(self) -> ProviderStatus:
        """供給元の状態。"""

    # ---- 共通の助け ----
    def _pair_spec(self, pair: str) -> PairSpec:
        return get_trading_config().pair(pair)

    def _validate_timeframe(self, timeframe: str) -> int:
        cfg = get_trading_config()
        if timeframe not in cfg.supported_timeframes():
            raise MarketDataError(f"未対応の時間足です: {timeframe}")
        return cfg.timeframe_minutes(timeframe)


# ---------------------------------------------------------------- 合成データ

def _last_closed_bar(minutes: int,
                     now: Optional[datetime] = None) -> datetime:
    """いま時点で最後に確定した足の開始時刻。

    合成データの終端をここに合わせる。**「起動した時刻」に固定しない。**
    固定すると、サーバーを動かし続けたときに足が古くなり、鮮度の条件で
    全銘柄が止まる。足の境目ごとに作り直せば、常に確定した最新の足になる。
    """
    now = now or datetime.now(timezone.utc)
    total = now.hour * 60 + now.minute
    start = (total // minutes) * minutes
    floored = now.replace(hour=start // 60, minute=start % 60,
                          second=0, microsecond=0)
    return floored - timedelta(minutes=minutes)


class MockMarketDataProvider(MarketDataProvider):
    """合成データの供給元。開発と試験で使う。

    返すのは**架空の値**であり、実勢ではない。状態にも mock と明示する。
    """

    name = "mock"
    is_synthetic = True

    def __init__(self, scenario: Optional[str] = None,
                 end_time: Optional[datetime] = None) -> None:
        self._scenario = scenario
        # 足の終端。既定は「いま」。
        #
        # 検証では **必ず固定する**。既定のままだと走らせるたびに終端が動き、
        # 同じ条件で走らせても結果が少しずつ変わる。実際、全銘柄の検証を
        # 2回走らせて勝ち数が 2148 と 2129 に割れた。数字が揺れる道具では、
        # 差が出たのか揺れただけなのか区別できない。
        self._end_time = end_time
        self._cache: Dict[tuple, CandleSeries] = {}

    def _series(self, pair: str, timeframe: str, limit: int) -> CandleSeries:
        spec = self._pair_spec(pair)
        minutes = self._validate_timeframe(timeframe)
        scenario = self._scenario or scenario_for_pair(pair)
        # **足の境目を鍵に含める。** 含めないと、起動時に作った足を
        # 使い続けることになる。数時間動かしただけで全銘柄が「古すぎ」で
        # 止まり、画面が使えなくなる（実際そうなった）。
        anchor_key = self._end_time or _last_closed_bar(minutes)
        key = (spec.symbol, timeframe, limit, scenario, anchor_key)
        if key not in self._cache:
            start = 150.0 if spec.quote == "JPY" else 1.2
            # **確定した足だけを返す。**
            #
            # 足の時刻は「始まり」を指す規約なので、時刻 T の足が確定するのは
            # T + 期間。終端を「いま」にすると、最後の1本は進行中の足になる。
            # 進行中の足を確定として扱ったのが market-radar で的中率を
            # 29.6% まで落とした原因なので、合成側でも作らない。
            anchor = self._end_time or _last_closed_bar(minutes)
            if self._end_time is not None:
                anchor = anchor - timedelta(minutes=minutes)
            self._cache[key] = generate_series(
                pair=spec.symbol, timeframe=timeframe, limit=limit,
                scenario=scenario, start_price=start, pip=spec.pip,
                minutes=minutes, end_time=anchor,
            )
        return self._cache[key]

    def get_candles(self, pair: str, timeframe: str, limit: int = 300) -> CandleSeries:
        if limit <= 0:
            raise MarketDataError("limit は1以上にしてください")
        return self._series(pair, timeframe, limit)

    def get_latest_price(self, pair: str) -> Quote:
        spec = self._pair_spec(pair)
        series = self._series(spec.symbol, "M5", 300)
        last = series.last
        if last is None:
            raise MarketDataError(f"足がありません: {pair}")
        half = spec.pip * 0.6
        return Quote(pair=spec.symbol, bid=last.close - half,
                     ask=last.close + half, timestamp=last.timestamp)

    def get_spread(self, pair: str) -> SpreadInfo:
        q = self.get_latest_price(pair)
        spec = self._pair_spec(pair)
        return SpreadInfo(pair=spec.symbol, spread_price=q.spread,
                          spread_pips=spec.to_pips(q.spread), timestamp=q.timestamp)

    def provider_status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name, state=ProviderState.OK,
            detail="合成データ。実勢の値ではありません。",
        )


# ---------------------------------------------------------------- CSV

# 読み込んだ CSV を覚えておく。鍵は (場所, 更新時刻, 大きさ)。
# **取り込み直せば鍵が変わるので、古い中身が残ることはない。**
_CSV_CACHE: Dict[Tuple[str, int, int], Tuple[List["Candle"], List[str]]] = {}
_CSV_CACHE_MAX = 200


class CSVMarketDataProvider(MarketDataProvider):
    """CSV から読む供給元。

    置き場所: ``<data_dir>/<PAIR>_<TIMEFRAME>.csv``
    見出し:   ``timestamp,open,high,low,close[,volume]``

    壊れた行は黙って直さず、飛ばして警告に積む。直すと後から追えなくなる。
    """

    name = "csv"

    def __init__(self, data_dir: str | Path, wide_spread: bool = False) -> None:
        self.data_dir = Path(data_dir)
        # 平常時のスプレッドか、広がったときのスプレッドか。
        # 実勢は指標発表前後で広がるので、検証は両方で見る。
        self.wide_spread = wide_spread
        self.last_errors: List[str] = []

    def _path(self, pair: str, timeframe: str) -> Path:
        # 外から来た文字列をそのまま繋がない。上位へ抜ける経路を塞ぐ。
        spec = self._pair_spec(pair)
        self._validate_timeframe(timeframe)
        name = f"{spec.symbol}_{timeframe}.csv"
        path = (self.data_dir / name).resolve()
        root = self.data_dir.resolve()
        if root not in path.parents and path.parent != root:
            raise MarketDataError("データ置き場の外は読めません")
        return path

    def _read_file(self, path: Path) -> Tuple[List[Candle], List[str]]:
        """CSV を読む。**中身が変わるまで読み直さない。**

        以前は呼ばれるたびに全行を読み直していた。1銘柄の分析で4つの
        時間足、さらに気配値でもう1回。26銘柄を並べると130回ぶんになり、
        M5 は17,000行あるので、一覧の表示に**60秒**かかっていた。
        実勢データに切り替えた瞬間に画面が使えなくなる、という形で出た。

        取り込み直せばファイルの更新時刻と大きさが変わるので、そこを
        鍵にする。**中身を推測しない。** 同じファイルなら同じ結果、
        違うファイルなら読み直す。
        """
        try:
            stat = path.stat()
        except OSError as exc:
            raise MarketDataError(f"CSV を読めません: {path.name}（{exc}）")
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        cached = _CSV_CACHE.get(key)
        if cached is not None:
            return cached

        rows: List[Candle] = []
        errors: List[str] = []
        with path.open(encoding="utf-8", newline="") as f:
            for i, row in enumerate(csv.DictReader(f), start=2):
                try:
                    rows.append(Candle(
                        timestamp=_parse_time(row["timestamp"]),
                        open=float(row["open"]), high=float(row["high"]),
                        low=float(row["low"]), close=float(row["close"]),
                        volume=float(row["volume"]) if row.get("volume") else None,
                    ))
                except Exception as exc:   # 行単位で拾い、全体は落とさない
                    errors.append(f"{path.name} {i}行目: {exc}")
        rows.sort(key=lambda c: c.timestamp)
        # 同じ時刻が重なっていたら後の行を採る
        dedup: Dict[datetime, Candle] = {}
        for c in rows:
            dedup[c.timestamp] = c
        ordered = [dedup[k] for k in sorted(dedup)]

        # 覚えておくのは少数のファイルだけ。**際限なく溜めない。**
        if len(_CSV_CACHE) > _CSV_CACHE_MAX:
            _CSV_CACHE.clear()
        _CSV_CACHE[key] = (ordered, errors)
        return ordered, errors

    def get_candles(self, pair: str, timeframe: str, limit: int = 300) -> CandleSeries:
        path = self._path(pair, timeframe)
        if not path.exists():
            raise MarketDataError(f"CSV がありません: {path.name}")
        spec = self._pair_spec(pair)
        ordered, errors = self._read_file(path)
        self.last_errors = errors
        if errors:
            log.warning("CSV に不正な行があります: %d件 (%s)",
                        len(errors), path.name)
        if not ordered:
            raise MarketDataError(f"読める足がありません: {path.name}")
        return CandleSeries(pair=spec.symbol, timeframe=timeframe,
                            candles=ordered[-limit:])

    def get_latest_price(self, pair: str) -> Quote:
        """最新の気配値。"""

    @abstractmethod
    def get_candles(self, pair: str, timeframe: str, limit: int = 300) -> CandleSeries:
        """足の並び。新しいものが末尾。"""

    @abstractmethod
    def get_spread(self, pair: str) -> SpreadInfo:
        """いまのスプレッド。"""

    @abstractmethod
    def provider_status(self) -> ProviderStatus:
        """供給元の状態。"""

    # ---- 共通の助け ----
    def _pair_spec(self, pair: str) -> PairSpec:
        return get_trading_config().pair(pair)

    def _validate_timeframe(self, timeframe: str) -> int:
        cfg = get_trading_config()
        if timeframe not in cfg.supported_timeframes():
            raise MarketDataError(f"未対応の時間足です: {timeframe}")
        return cfg.timeframe_minutes(timeframe)


# ---------------------------------------------------------------- 合成データ

def _last_closed_bar(minutes: int,
                     now: Optional[datetime] = None) -> datetime:
    """いま時点で最後に確定した足の開始時刻。

    合成データの終端をここに合わせる。**「起動した時刻」に固定しない。**
    固定すると、サーバーを動かし続けたときに足が古くなり、鮮度の条件で
    全銘柄が止まる。足の境目ごとに作り直せば、常に確定した最新の足になる。
    """
    now = now or datetime.now(timezone.utc)
    total = now.hour * 60 + now.minute
    start = (total // minutes) * minutes
    floored = now.replace(hour=start // 60, minute=start % 60,
                          second=0, microsecond=0)
    return floored - timedelta(minutes=minutes)


class MockMarketDataProvider(MarketDataProvider):
    """合成データの供給元。開発と試験で使う。

    返すのは**架空の値**であり、実勢ではない。状態にも mock と明示する。
    """

    name = "mock"
    is_synthetic = True

    def __init__(self, scenario: Optional[str] = None,
                 end_time: Optional[datetime] = None) -> None:
        self._scenario = scenario
        # 足の終端。既定は「いま」。
        #
        # 検証では **必ず固定する**。既定のままだと走らせるたびに終端が動き、
        # 同じ条件で走らせても結果が少しずつ変わる。実際、全銘柄の検証を
        # 2回走らせて勝ち数が 2148 と 2129 に割れた。数字が揺れる道具では、
        # 差が出たのか揺れただけなのか区別できない。
        self._end_time = end_time
        self._cache: Dict[tuple, CandleSeries] = {}

    def _series(self, pair: str, timeframe: str, limit: int) -> CandleSeries:
        spec = self._pair_spec(pair)
        minutes = self._validate_timeframe(timeframe)
        scenario = self._scenario or scenario_for_pair(pair)
        # **足の境目を鍵に含める。** 含めないと、起動時に作った足を
        # 使い続けることになる。数時間動かしただけで全銘柄が「古すぎ」で
        # 止まり、画面が使えなくなる（実際そうなった）。
        anchor_key = self._end_time or _last_closed_bar(minutes)
        key = (spec.symbol, timeframe, limit, scenario, anchor_key)
        if key not in self._cache:
            start = 150.0 if spec.quote == "JPY" else 1.2
            # **確定した足だけを返す。**
            #
            # 足の時刻は「始まり」を指す規約なので、時刻 T の足が確定するのは
            # T + 期間。終端を「いま」にすると、最後の1本は進行中の足になる。
            # 進行中の足を確定として扱ったのが market-radar で的中率を
            # 29.6% まで落とした原因なので、合成側でも作らない。
            anchor = self._end_time or _last_closed_bar(minutes)
            if self._end_time is not None:
                anchor = anchor - timedelta(minutes=minutes)
            self._cache[key] = generate_series(
                pair=spec.symbol, timeframe=timeframe, limit=limit,
                scenario=scenario, start_price=start, pip=spec.pip,
                minutes=minutes, end_time=anchor,
            )
        return self._cache[key]

    def get_candles(self, pair: str, timeframe: str, limit: int = 300) -> CandleSeries:
        if limit <= 0:
            raise MarketDataError("limit は1以上にしてください")
        return self._series(pair, timeframe, limit)

    def get_latest_price(self, pair: str) -> Quote:
        spec = self._pair_spec(pair)
        series = self._series(spec.symbol, "M5", 300)
        last = series.last
        if last is None:
            raise MarketDataError(f"足がありません: {pair}")
        half = spec.pip * 0.6
        return Quote(pair=spec.symbol, bid=last.close - half,
                     ask=last.close + half, timestamp=last.timestamp)

    def get_spread(self, pair: str) -> SpreadInfo:
        q = self.get_latest_price(pair)
        spec = self._pair_spec(pair)
        return SpreadInfo(pair=spec.symbol, spread_price=q.spread,
                          spread_pips=spec.to_pips(q.spread), timestamp=q.timestamp)

    def provider_status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name, state=ProviderState.OK,
            detail="合成データ。実勢の値ではありません。",
        )


# ---------------------------------------------------------------- CSV

class CSVMarketDataProvider(MarketDataProvider):
    """CSV から読む供給元。

    置き場所: ``<data_dir>/<PAIR>_<TIMEFRAME>.csv``
    見出し:   ``timestamp,open,high,low,close[,volume]``

    壊れた行は黙って直さず、飛ばして警告に積む。直すと後から追えなくなる。
    """

    name = "csv"

    def __init__(self, data_dir: str | Path, wide_spread: bool = False) -> None:
        self.data_dir = Path(data_dir)
        # 平常時のスプレッドか、広がったときのスプレッドか。
        # 実勢は指標発表前後で広がるので、検証は両方で見る。
        self.wide_spread = wide_spread
        self.last_errors: List[str] = []

    def _path(self, pair: str, timeframe: str) -> Path:
        # 外から来た文字列をそのまま繋がない。上位へ抜ける経路を塞ぐ。
        spec = self._pair_spec(pair)
        self._validate_timeframe(timeframe)
        name = f"{spec.symbol}_{timeframe}.csv"
        path = (self.data_dir / name).resolve()
        root = self.data_dir.resolve()
        if root not in path.parents and path.parent != root:
            raise MarketDataError("データ置き場の外は読めません")
        return path

    def _read_file(self, path: Path) -> Tuple[List[Candle], List[str]]:
        """CSV を読む。**中身が変わるまで読み直さない。**

        以前は呼ばれるたびに全行を読み直していた。1銘柄の分析で4つの
        時間足、さらに気配値でもう1回。26銘柄を並べると130回ぶんになり、
        M5 は17,000行あるので、一覧の表示に**60秒**かかっていた。
        実勢データに切り替えた瞬間に画面が使えなくなる、という形で出た。

        取り込み直せばファイルの更新時刻と大きさが変わるので、そこを
        鍵にする。**中身を推測しない。** 同じファイルなら同じ結果、
        違うファイルなら読み直す。
        """
        try:
            stat = path.stat()
        except OSError as exc:
            raise MarketDataError(f"CSV を読めません: {path.name}（{exc}）")
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        cached = _CSV_CACHE.get(key)
        if cached is not None:
            return cached

        rows: List[Candle] = []
        errors: List[str] = []
        with path.open(encoding="utf-8", newline="") as f:
            for i, row in enumerate(csv.DictReader(f), start=2):
                try:
                    rows.append(Candle(
                        timestamp=_parse_time(row["timestamp"]),
                        open=float(row["open"]), high=float(row["high"]),
                        low=float(row["low"]), close=float(row["close"]),
                        volume=float(row["volume"]) if row.get("volume") else None,
                    ))
                except Exception as exc:   # 行単位で拾い、全体は落とさない
                    errors.append(f"{path.name} {i}行目: {exc}")
        rows.sort(key=lambda c: c.timestamp)
        # 同じ時刻が重なっていたら後の行を採る
        dedup: Dict[datetime, Candle] = {}
        for c in rows:
            dedup[c.timestamp] = c
        ordered = [dedup[k] for k in sorted(dedup)]

        # 覚えておくのは少数のファイルだけ。**際限なく溜めない。**
        if len(_CSV_CACHE) > _CSV_CACHE_MAX:
            _CSV_CACHE.clear()
        _CSV_CACHE[key] = (ordered, errors)
        return ordered, errors

    def get_candles(self, pair: str, timeframe: str, limit: int = 300) -> CandleSeries:
        path = self._path(pair, timeframe)
        if not path.exists():
            raise MarketDataError(f"CSV がありません: {path.name}")
        spec = self._pair_spec(pair)
        ordered, errors = self._read_file(path)
        self.last_errors = errors
        if errors:
            log.warning("CSV に不正な行があります: %d件 (%s)",
                        len(errors), path.name)
        if not ordered:
            raise MarketDataError(f"読める足がありません: {path.name}")
        return CandleSeries(pair=spec.symbol, timeframe=timeframe,
                            candles=ordered[-limit:])

    def _unused_legacy_read(self, path, spec, limit, timeframe):
        rows: List[Candle] = []
        errors: List[str] = []
        with path.open(encoding="utf-8", newline="") as f:
            for i, row in enumerate(csv.DictReader(f), start=2):
                try:
                    rows.append(Candle(
                        timestamp=_parse_time(row["timestamp"]),
                        open=float(row["open"]), high=float(row["high"]),
                        low=float(row["low"]), close=float(row["close"]),
                        volume=float(row["volume"]) if row.get("volume") else None,
                    ))
                except Exception as exc:   # 行単位で拾い、全体は落とさない
                    errors.append(f"{path.name} {i}行目: {exc}")
        self.last_errors = errors
        if errors:
            log.warning("CSV に不正な行があります: %d件 (%s)", len(errors), path.name)
        if not rows:
            raise MarketDataError(f"読める足がありません: {path.name}")
        rows.sort(key=lambda c: c.timestamp)
        # 同じ時刻が重なっていたら後の行を採る
        dedup: Dict[datetime, Candle] = {}
        for c in rows:
            dedup[c.timestamp] = c
        ordered = [dedup[k] for k in sorted(dedup)]
        return CandleSeries(pair=spec.symbol, timeframe=timeframe,
                            candles=ordered[-limit:])

    def get_latest_price(self, pair: str) -> Quote:
        spec = self._pair_spec(pair)
        cfg = get_trading_config()
        for tf in ("M1", "M5", "M15", "H1"):
            if tf not in cfg.supported_timeframes():
                continue
            try:
                series = self.get_candles(spec.symbol, tf, limit=2)
            except MarketDataError:
                continue
            last = series.last
            if last:
                # 業者の実勢スプレッドを使う。銘柄で10倍以上ちがうので、
                # 一律の値を置くと広い銘柄の成績を大きく良く見せてしまう。
                half = cfg.spread_price(spec.symbol, self.wide_spread) / 2.0
                return Quote(pair=spec.symbol, bid=last.close - half,
                             ask=last.close + half, timestamp=last.timestamp)
        raise MarketDataError(f"最新値を取れません: {pair}")

    def get_spread(self, pair: str) -> SpreadInfo:
        q = self.get_latest_price(pair)
        spec = self._pair_spec(pair)
        return SpreadInfo(pair=spec.symbol, spread_price=q.spread,
                          spread_pips=spec.to_pips(q.spread), timestamp=q.timestamp)

    def provider_status(self) -> ProviderStatus:
        if not self.data_dir.exists():
            return ProviderStatus(name=self.name, state=ProviderState.OFFLINE,
                                  detail=f"置き場がありません: {self.data_dir}")
        files = list(self.data_dir.glob("*.csv"))
        if not files:
            return ProviderStatus(name=self.name, state=ProviderState.DEGRADED,
                                  detail="CSV が1つもありません")
        return ProviderStatus(name=self.name, state=ProviderState.OK,
                              detail=f"{len(files)} 個の CSV を認識しています")


def _parse_time(value: str) -> datetime:
    v = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ------------------------------------------------- まだ繋いでいない供給元

class _UnimplementedProvider(MarketDataProvider):
    """繋いでいない供給元の共通の親。真似をせず、はっきり失敗する。"""

    reason = "この供給元はまだ実装されていません"

    def _fail(self):
        raise NotImplementedError(self.reason)

    def get_latest_price(self, pair: str) -> Quote:
        self._fail()

    def get_candles(self, pair: str, timeframe: str, limit: int = 300) -> CandleSeries:
        self._fail()

    def get_spread(self, pair: str) -> SpreadInfo:
        self._fail()

    def provider_status(self) -> ProviderStatus:
        return ProviderStatus(name=self.name,
                              state=ProviderState.NOT_IMPLEMENTED,
                              detail=self.reason)


class MT4MarketDataProvider(_UnimplementedProvider):
    name = "mt4"
    reason = ("MT4 との接続は未実装です。接続方式が決まるまで、"
              "それらしい値を返すことはしません。")


class ExternalAPIProvider(_UnimplementedProvider):
    name = "external_api"
    reason = ("外部APIの供給元は未実装です。実在する提供元と契約が決まるまで、"
              "エンドポイントを勝手に作りません。")


class FutureBrokerProvider(_UnimplementedProvider):
    name = "future_broker"
    reason = "業者接続は未実装です。この段階では発注も接続も行いません。"


# ---------------------------------------------------------------- 生成

_PROVIDERS = {
    "mock": lambda s: MockMarketDataProvider(),
    "csv": lambda s: CSVMarketDataProvider(s.csv_dir),
    "mt4": lambda s: MT4MarketDataProvider(),
    "external_api": lambda s: ExternalAPIProvider(),
    "future_broker": lambda s: FutureBrokerProvider(),
}


def build_provider(settings) -> MarketDataProvider:
    key = (settings.market_data_provider or "mock").lower()
    if key not in _PROVIDERS:
        raise MarketDataError(f"未知の供給元です: {key}")
    return _PROVIDERS[key](settings)
