# -*- coding: utf-8 -*-
"""設定の読み込み。

方針が2つある。

1. **売買ロジックの値は config/ の JSON に置き、版管理する。**
   しきい値をコードに散らすと、どこを変えたか追えなくなる。
2. **環境ごとに変わる値だけを環境変数にする。**
   接続先やログ水準がそれにあたる。秘密はコードに書かない。

同じ値を2か所に置かない。ここが唯一の読み込み口になる。
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel, Field

# fx-ai-system/ をプロジェクト基点とする（このファイルは backend/app/config.py）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


def _strip_comments(obj: Any) -> Any:
    """``_`` で始まる鍵を落とす。

    設定に注記を書けるようにしてあるが、それをデータとして読むと壊れる。
    実際、``min_candles`` の ``_comment`` を時間足として扱い、全26銘柄が
    「足を取得できていません」で見送りになる不具合を出した。
    読み込みの時点で落としておけば、同じ間違いはどこでも起きない。
    """
    if isinstance(obj, dict):
        return {k: _strip_comments(v) for k, v in obj.items()
                if not (isinstance(k, str) and k.startswith("_"))}
    if isinstance(obj, list):
        return [_strip_comments(v) for v in obj]
    return obj


def load_dotenv(path: Path | None = None) -> None:
    """``.env`` を環境変数に流し込む。

    すでに環境にある値は **上書きしない**。本番で外から渡した設定を
    置き忘れの ``.env`` が黙って上書きするほうが危ないため。

    依存を増やさずに済ませている。読むのは ``KEY=VALUE`` の行だけで、
    ``#`` から始まる行と空行は飛ばす。値の前後の引用符は外す。
    """
    path = path or (PROJECT_ROOT / ".env")
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _load_json(name: str, optional: bool = False) -> Dict[str, Any]:
    """設定を読む。

    ``optional`` は「まだ測っていない」ことがありうる設定のためにある。
    **無いものを既定値で埋めない。** 空の辞書を返して、使う側が
    「持っていない」と分かるようにする。
    """
    path = CONFIG_DIR / name
    if not path.exists():
        if optional:
            return {}
        raise FileNotFoundError(f"設定ファイルが見つかりません: {path}")
    with path.open(encoding="utf-8") as f:
        return _strip_comments(json.load(f))


class PairSpec(BaseModel):
    """1通貨ペアの定義。ペアごとの分岐をコードに書かないための入れ物。"""

    symbol: str
    base: str
    quote: str
    pip: float = Field(gt=0, description="1pip の値幅。円ペアは0.01、それ以外は0.0001")
    digits: int = Field(ge=0, le=8)
    enabled: bool = True

    def to_pips(self, price_diff: float) -> float:
        """値幅を pips に直す。"""
        return price_diff / self.pip


class Settings(BaseModel):
    """環境ごとの設定。秘密はここに書かず、環境変数から読む。"""

    env: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./database/fx.sqlite3"
    # 画面に出した判断を残すか。**既定は残す。**
    # 残さないと、あとから「あのときどう判断したか」を辿れない。
    journal_enabled: bool = True
    market_data_provider: str = "mock"
    csv_data_dir: str = "./data"
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    api_version: str = "0.1.0"

    # ---- 置き場の解決 ----
    # 相対指定は「起動したディレクトリ」ではなく **プロジェクト基点** に付ける。
    # backend/ から起動したときに backend/database/ が別にできてしまい、
    # 同じ設定なのに中身が違う、という事故を実際に起こした。
    @staticmethod
    def _anchor(value: str) -> str:
        path = Path(value)
        return str(path if path.is_absolute() else (PROJECT_ROOT / path).resolve())

    @property
    def csv_dir(self) -> str:
        return self._anchor(self.csv_data_dir)

    @property
    def log_dir(self) -> str:
        return self._anchor("logs")

    @property
    def resolved_database_url(self) -> str:
        """sqlite の相対パスだけ基点に付け直す。他の DBMS はそのまま返す。"""
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            return self.database_url
        rest = self.database_url[len(prefix):]
        if not rest or rest.startswith("/") or rest.startswith(":"):
            return self.database_url      # :memory: と絶対パスは触らない
        return prefix + self._anchor(rest).replace("\\", "/")

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        origins = os.getenv("FX_CORS_ORIGINS", "http://localhost:3000")
        return cls(
            env=os.getenv("FX_ENV", "development"),
            log_level=os.getenv("FX_LOG_LEVEL", "INFO"),
            database_url=os.getenv("FX_DATABASE_URL", "sqlite:///./database/fx.sqlite3"),
            journal_enabled=os.getenv("FX_JOURNAL", "1") not in ("0", "false", "False"),
            market_data_provider=os.getenv("FX_MARKET_DATA_PROVIDER", "mock"),
            csv_data_dir=os.getenv("FX_CSV_DATA_DIR", "./data"),
            cors_origins=[o.strip() for o in origins.split(",") if o.strip()],
            api_version=os.getenv("FX_API_VERSION", "0.1.0"),
        )


class TradingConfig(BaseModel):
    """版管理する売買ロジックの設定一式。"""

    pairs: List[PairSpec]
    timeframes: Dict[str, Any]
    indicators: Dict[str, Any]
    regime: Dict[str, Any]
    signal: Dict[str, Any]
    filters: Dict[str, Any]
    backtest: Dict[str, Any]
    spreads: Dict[str, Any]
    account: Dict[str, Any]
    swap: Dict[str, Any]
    calendar: Dict[str, Any]
    correlation: Dict[str, Any]
    news: Dict[str, Any]
    # 朝のボードの決まり（値幅・レンジ・警戒帯など）。
    board: Dict[str, Any]
    # どの銘柄を支援するか。**手で書かない。**
    # scripts/measure_pair_value.py が実データから測って書き出す。
    # 測っていなければ空で、その場合は全銘柄が「未測定」になる。
    focus: Dict[str, Any]
    # この先どれだけ動きうるかの幅。**向きの予想ではない。**
    # scripts/measure_forecast.py が実データから測って書き出す。
    # 測っていなければ空で、画面にも幅を出さない。
    forecast: Dict[str, Any]

    # ---- 参照用の便利メソッド ----
    def pair(self, symbol: str) -> PairSpec:
        for p in self.pairs:
            if p.symbol == symbol.upper():
                return p
        raise KeyError(f"未対応の通貨ペアです: {symbol}")

    def enabled_pairs(self) -> List[PairSpec]:
        return [p for p in self.pairs if p.enabled]

    def supported_timeframes(self) -> List[str]:
        return list(self.timeframes["supported"])

    def timeframe_minutes(self, tf: str) -> int:
        try:
            return int(self.timeframes["minutes"][tf])
        except KeyError as exc:
            raise KeyError(f"未対応の時間足です: {tf}") from exc

    def role_timeframe(self, role: str) -> str:
        return self.timeframes["roles"][role]

    def spread_price(self, symbol: str, wide: bool = False) -> float:
        """業者の実勢スプレッドを価格の単位で返す。

        **1銘柄1.2pips で一律に置くのは駄目。** GBPNZD は実測10pips、
        USDJPY は0.9pips で、10倍以上ちがう。一律に置くと、広い銘柄の
        成績を実際よりずっと良く見せてしまう。
        """
        table = self.spreads["wide" if wide else "narrow"]
        v = table.get(symbol.upper())
        if v is not None:
            return float(v)
        return float(self.spreads["default_pips"]) * self.pair(symbol).pip

    def max_spread_pips(self, symbol: str) -> float:
        table = self.filters["spread"]["max_pips"]
        return float(table.get(symbol.upper(), table["default"]))


@lru_cache(maxsize=1)
def get_trading_config() -> TradingConfig:
    """売買ロジックの設定を読む。1度だけ読んで使い回す。"""
    pairs_raw = _load_json("pairs.json")["pairs"]
    return TradingConfig(
        pairs=[PairSpec(**p) for p in pairs_raw],
        timeframes=_load_json("timeframes.json"),
        indicators=_load_json("indicators.json"),
        regime=_load_json("regime.json"),
        signal=_load_json("signal_weights.json"),
        filters=_load_json("filters.json"),
        backtest=_load_json("backtest.json"),
        spreads=_load_json("spreads.json"),
        account=_load_json("account.json"),
        swap=_load_json("swap.json"),
        calendar=_load_json("market_calendar.json"),
        correlation=_load_json("correlation.json"),
        news=_load_json("news.json"),
        board=_load_json("board.json", optional=True),
        focus=_load_json("focus.json", optional=True),
        forecast=_load_json("forecast.json", optional=True),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def reset_config_cache() -> None:
    """試験で設定を差し替えるとき用。"""
    get_trading_config.cache_clear()
    get_settings.cache_clear()
