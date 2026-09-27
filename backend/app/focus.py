# -*- coding: utf-8 -*-
"""どの銘柄を支援するか。

**26銘柄すべてを同じ顔で並べるのをやめる。** 実測では、向きが出た場面の
77〜100% がスプレッドの強制条件で消えていた。費用が見合わない銘柄を
並べておくと、画面は賑やかだが**出るのは見送りばかり**になる。

区分は ``config/focus.json`` に置く。**手で書かない。**
``scripts/measure_pair_value.py`` が実データから測って書き出す。

| 区分 | 意味 |
|---|---|
| ``focus`` | 費用の面で土俵に乗る。**支援する** |
| ``watch`` | 場面によっては乗る。並べるが前には出さない |
| ``off``   | 普段から費用が重すぎる。**出番が来ない** |

**勝てるかどうかでは分けていない。それは測れていない**
（docs/backtesting.md）。分けているのは費用の面だけ。

測っていなければ全銘柄が ``unknown`` になる。**既定で focus にしない。**
測る前のものを「支援できる」と言わないため。
"""
from __future__ import annotations

from typing import Dict, List

from .config import TradingConfig

UNKNOWN = "unknown"
ORDER = {"focus": 0, "watch": 1, "off": 2, UNKNOWN: 3}

LABEL = {
    "focus": "支援する",
    "watch": "様子を見る",
    "off": "対象外（費用が重い）",
    UNKNOWN: "未測定",
}


def table(cfg: TradingConfig) -> Dict[str, Dict]:
    return (getattr(cfg, "focus", None) or {}).get("pairs") or {}


def tier_of(cfg: TradingConfig, symbol: str) -> str:
    """その銘柄の区分。測っていなければ ``unknown``。"""
    row = table(cfg).get(symbol.upper())
    if not row:
        return UNKNOWN
    tier = str(row.get("tier") or UNKNOWN)
    return tier if tier in ORDER else UNKNOWN


def symbols(cfg: TradingConfig, tier: str = "focus") -> List[str]:
    """その区分の銘柄。**並び順は設定の順（測った順）のまま。**"""
    return [s for s, row in table(cfg).items()
            if str(row.get("tier")) == tier]


def measured(cfg: TradingConfig) -> bool:
    return bool(table(cfg))


def as_dict(cfg: TradingConfig) -> Dict:
    """画面に出す形。**測っていないことも、そのまま出す。**"""
    raw = getattr(cfg, "focus", None) or {}
    rows = table(cfg)
    counts: Dict[str, int] = {}
    for symbol in (p.symbol for p in cfg.enabled_pairs()):
        tier = tier_of(cfg, symbol)
        counts[tier] = counts.get(tier, 0) + 1
    return {
        "measured": bool(rows),
        "measured_at": raw.get("measured_at"),
        "timeframe": raw.get("timeframe"),
        "spread_limit_ratio": raw.get("spread_limit_ratio"),
        "spread_basis": raw.get("spread_basis"),
        "thresholds": raw.get("thresholds"),
        "counts": counts,
        "labels": LABEL,
        "pairs": rows,
        "note": ("費用（スプレッド ÷ M15 の ATR）だけで分けています。"
                 "**勝てるかどうかでは分けていません。それは測れて"
                 "いません。**"),
    }
