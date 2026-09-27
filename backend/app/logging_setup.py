# -*- coding: utf-8 -*-
"""ログの設定。

**例外を黙って握りつぶさない。** 記録せずに握りつぶすと、静かに壊れたまま
動き続ける。何が起きたか分からないまま数字だけが出てくる状態が、いちばん
危ない。

秘密はログに出さない。
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_SECRET_KEYS = ("password", "token", "secret", "api_key", "apikey",
                "authorization", "cookie", "credential", "passwd")


class ReasonCode:
    """記録に付ける理由コード。

    **後から機械で数えられる形にしておく。** 文言だけだと、表現を変えた
    とたんに集計が壊れる。何がどれだけ起きているかを追えなくなる。
    """

    PROVIDER_ERROR = "PROVIDER_ERROR"
    PROVIDER_OFFLINE = "PROVIDER_OFFLINE"
    PROVIDER_NOT_IMPLEMENTED = "PROVIDER_NOT_IMPLEMENTED"
    INVALID_CANDLE = "INVALID_CANDLE"
    INVALID_TIMEFRAME = "INVALID_TIMEFRAME"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    STALE_DATA = "STALE_DATA"
    UNCLOSED_BAR = "UNCLOSED_BAR"
    NATIVE_TIMEFRAME_MISMATCH = "NATIVE_TIMEFRAME_MISMATCH"
    ANALYSIS_ERROR = "ANALYSIS_ERROR"
    ANALYSIS_EXPIRED = "ANALYSIS_EXPIRED"
    HARD_FILTER = "HARD_FILTER"
    HIGHER_BIAS_CONFLICT = "HIGHER_BIAS_CONFLICT"
    HIGHER_BIAS_UNKNOWN = "HIGHER_BIAS_UNKNOWN"
    EXTREME_SPREAD = "EXTREME_SPREAD"
    ABNORMAL_VOLATILITY = "ABNORMAL_VOLATILITY"
    CSV_INVALID_INPUT = "CSV_INVALID_INPUT"
    CSV_UNREADABLE = "CSV_UNREADABLE"
    API_ERROR = "API_ERROR"


class JsonFormatter(logging.Formatter):
    """1行1件のJSONで出す。後から機械で読めるように。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key in ("pair", "timeframe", "provider", "reason",
                    "reason_code", "signal", "elapsed_ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(_redact(payload), ensure_ascii=False)


def _redact(obj):
    if isinstance(obj, dict):
        return {k: ("***" if any(s in k.lower() for s in _SECRET_KEYS)
                    else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def setup_logging(level: str = "INFO", log_dir: str | Path = "logs") -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level.upper())

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(JsonFormatter())
    root.addHandler(stream)

    try:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        fileh = logging.FileHandler(path / "app.log", encoding="utf-8")
        fileh.setFormatter(JsonFormatter())
        root.addHandler(fileh)
    except OSError as exc:      # 書けなくても標準出力には残す
        root.warning("ログをファイルに書けません: %s", exc)
