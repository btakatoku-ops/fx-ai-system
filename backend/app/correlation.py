# -*- coding: utf-8 -*-
"""相関（Phase 2 で埋める配点10点）。

**どの銘柄が何と相関するかを決め打ちしない。**

「USDJPY はドル指数と相関する」と表に書いてしまうと、実際に相関が切れて
いる期間でもその前提で加点し続ける。相関は変わるものなので、**期間ごとに
測ってから使う**。測って弱ければ、その材料は使わない。

やること。

1. 通貨ペアと材料の**収益率**を、時刻が揃う範囲で並べる
2. 相関係数を測る
3. 弱い（``min_abs_correlation`` 未満）材料は捨てる
4. 残った材料の直近の動きから「この銘柄はどちらへ行きやすいか」を出す
5. 判断の向きと一致する度合いを |相関| で重み付けして平均する

材料が足りなければ **UNKNOWN**（0点）。「中立で半分」にはしない。
持っていない材料に点を付けるのは架空の加点で、点数の意味を壊す。
"""
from __future__ import annotations

import csv
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .config import PROJECT_ROOT

log = logging.getLogger(__name__)

PHASE = 2
DRIVER_DIR = PROJECT_ROOT / "data" / "drivers"


# ------------------------------------------------------------ 材料の読み込み


@lru_cache(maxsize=32)
def load_driver(name: str, timeframe: str,
                directory: Optional[str] = None) -> Tuple[Tuple[datetime, float], ...]:
    """材料の終値を読む。無ければ空を返す（例外にしない）。

    材料が無いのは異常ではなく「まだ繋いでいない」だけ。
    その場合は相関を UNKNOWN として扱えばよい。
    """
    base = Path(directory) if directory else DRIVER_DIR
    path = base / f"{name}_{timeframe}.csv"
    if not path.exists():
        return ()
    out: List[Tuple[datetime, float]] = []
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    t = datetime.fromisoformat(row["timestamp"])
                    c = float(row["close"])
                except (KeyError, ValueError, TypeError):
                    continue
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                if math.isfinite(c) and c > 0:
                    out.append((t.astimezone(timezone.utc), c))
    except OSError as exc:
        log.warning("材料を読めません %s: %s", name, exc)
        return ()
    out.sort(key=lambda r: r[0])
    return tuple(out)


def clear_cache() -> None:
    load_driver.cache_clear()


# ------------------------------------------------------------ 統計


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """ピアソンの相関係数。片方でも動きが無ければ None。

    分母が0のとき0を返してはいけない。「相関が無い」と「測れない」は
    別のことで、混ぜると測れない場面に点が付く。
    """
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    denom = math.sqrt(sxx * syy)
    if denom <= 0:
        return None
    r = sxy / denom
    if not math.isfinite(r):
        return None
    return max(-1.0, min(1.0, r))


def _returns(values: Sequence[float]) -> List[float]:
    return [(values[i + 1] - values[i]) / values[i]
            for i in range(len(values) - 1) if values[i] > 0]


def _floor(t: datetime, minutes: int) -> datetime:
    """時刻を時間足の枠に落とす。"""
    if minutes <= 0:
        return t
    total = t.hour * 60 + t.minute
    start = (total // minutes) * minutes
    return t.replace(hour=start // 60, minute=start % 60,
                     second=0, microsecond=0)


def _align(pair_series: Sequence[Tuple[datetime, float]],
           driver_series: Sequence[Tuple[datetime, float]],
           bar_minutes: int = 0) -> Tuple[List[float], List[float]]:
    """時刻が揃う分だけを並べる。

    材料は取引時間も刻みも違う。米10年債は毎時 :20、S&P500 は :30 に
    刻まれていて、為替の :00 とは一致しない。そのままだと突き合わせが
    1本も成立しない。

    そこで ``bar_minutes`` が与えられたら、双方を時間足の枠に落としてから
    突き合わせる。**枠内のずれはそのまま残る**ので相関は弱く出るが、
    弱く出るのは安全側（使えないと判定されるだけ）。

    **無い時刻は埋めない。** 埋めると、動いていない時間の値を動いたことに
    してしまう。
    """
    if bar_minutes > 0:
        d: Dict[datetime, float] = {}
        for t, v in driver_series:
            d[_floor(t, bar_minutes)] = v      # 同じ枠は後の値を採る
        keyed = [(_floor(t, bar_minutes), v) for t, v in pair_series]
    else:
        d = dict(driver_series)
        keyed = list(pair_series)

    xs: List[float] = []
    ys: List[float] = []
    seen = set()
    for t, v in keyed:
        if t in seen:
            continue
        w = d.get(t)
        if w is not None:
            seen.add(t)
            xs.append(v)
            ys.append(w)
    return xs, ys


# ------------------------------------------------------------ 判定


@dataclass
class DriverView:
    """1つの材料についての見立て。"""

    name: str
    label: str
    correlation: float
    overlap: int
    driver_move: float
    implied: int          # +1 上 / -1 下
    agrees: bool

    def as_dict(self) -> Dict:
        return {
            "name": self.name, "label": self.label,
            "correlation": round(self.correlation, 3),
            "overlap": self.overlap,
            "driver_move": round(self.driver_move, 6),
            "implied": self.implied, "agrees": self.agrees,
        }


@dataclass
class CorrelationResult:
    """相関の評価。

    ``state`` は3つある。**「測れない」と「測ったが弱い」を区別する。**

    - ``KNOWN``       : 測れて、使える材料があった
    - ``WEAK``        : 材料は揃っているが、どれも相関が弱かった
    - ``UNAVAILABLE`` : そもそも突き合わせる材料が無い（未取得・時刻が合わない）

    この区別が要る理由。``WEAK`` は「確かめた結果、支えが無い」なので
    0点にして満点にも数える（**確認できないことは減点**）。
    ``UNAVAILABLE`` は「確かめようがない」ので、満点から外す。
    外さないと、材料を持たない環境では全体が沈んで何も出なくなる。
    """

    state: str = "UNKNOWN"
    ratio: float = 0.0            # 0.0〜1.0。配点に掛ける
    drivers: List[DriverView] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict:
        return {
            "state": self.state, "ratio": round(self.ratio, 4),
            "drivers": [d.as_dict() for d in self.drivers],
            "skipped": self.skipped, "reasons": self.reasons,
        }


def evaluate(
    pair_series: Sequence[Tuple[datetime, float]],
    direction_sign: int,
    cfg: Dict,
    directory: Optional[str] = None,
) -> CorrelationResult:
    """相関から見て、その向きが支持されるか。

    ``direction_sign`` は +1（上）/ -1（下）。0 のときは評価しない。
    """
    res = CorrelationResult()
    if direction_sign == 0:
        res.reasons.append("向きが定まっていないため相関を評価しません")
        return res

    tf = cfg["timeframe"]
    window = int(cfg["window_bars"])
    min_overlap = int(cfg["min_overlap_bars"])
    min_abs = float(cfg["min_abs_correlation"])
    move_bars = int(cfg["move_bars"])
    bar_minutes = int(cfg.get("bar_minutes", 0))
    max_lag = int(cfg.get("max_driver_lag_bars", 3))

    recent_pair = list(pair_series)[-window:]
    if len(recent_pair) < min_overlap + 1:
        res.reasons.append(
            f"銘柄側の足が {len(recent_pair)} 本しかなく、相関を測れません")
        return res

    weighted = 0.0
    total_weight = 0.0
    measurable = False        # 突き合わせ自体はできたか
    pair_last = recent_pair[-1][0]
    for name, spec in cfg["drivers"].items():
        series = load_driver(name, tf, directory)
        if not series:
            res.skipped.append(f"{name}: 材料が未取得")
            continue

        # **古い材料を「直近の動き」として使わない。**
        #
        # 下では材料の最後の足（dx[-1]）から直近の動きを出している。
        # 材料の取り込みが止まっていると、その「直近」が何時間も前の
        # 動きになる。時刻の重なりは窓が広いので足りてしまい、**古い値が
        # いまの動きとして点数に入る**。相関は取り込みが手動なので、
        # ここは実際に起きる。
        #
        # 比べるのは壁時計ではなく**銘柄側の最後の足**にする。過去を
        # さかのぼって検証するときも同じ判定にするため。
        if max_lag > 0 and bar_minutes > 0:
            lag = (pair_last - series[-1][0]).total_seconds() / 60.0
            if lag > max_lag * bar_minutes:
                res.skipped.append(
                    f"{name}: 材料が {lag / 60:.1f} 時間古く、"
                    f"直近の動きとして使えません")
                continue

        px, dx = _align(recent_pair, series, bar_minutes)
        if len(px) < min_overlap + 1:
            res.skipped.append(f"{name}: 時刻が揃う足が {len(px)} 本で不足")
            continue

        measurable = True     # 時刻が揃った＝確かめられた
        r = pearson(_returns(px), _returns(dx))
        if r is None:
            res.skipped.append(f"{name}: 相関を計算できません")
            continue
        if abs(r) < min_abs:
            res.skipped.append(f"{name}: 相関 {r:+.2f} が弱く、使いません")
            continue

        if len(dx) <= move_bars or dx[-1 - move_bars] <= 0:
            res.skipped.append(f"{name}: 直近の動きを測れません")
            continue
        move = (dx[-1] - dx[-1 - move_bars]) / dx[-1 - move_bars]
        if move == 0:
            res.skipped.append(f"{name}: 直近の動きがありません")
            continue

        implied = 1 if (r > 0) == (move > 0) else -1
        agrees = implied == direction_sign
        res.drivers.append(DriverView(
            name=name, label=spec.get("label", name), correlation=r,
            overlap=len(px), driver_move=move, implied=implied, agrees=agrees))
        weighted += abs(r) * (1.0 if agrees else 0.0)
        total_weight += abs(r)

    if len(res.drivers) < int(cfg.get("min_drivers", 1)) or total_weight <= 0:
        if measurable:
            res.state = "WEAK"
            res.reasons.append(
                "材料は揃っていますが、どれも相関が弱く支えになりません")
        else:
            res.state = "UNAVAILABLE"
            res.reasons.append(
                "相関を突き合わせる材料がありません（推測では埋めません）")
        return res

    res.state = "KNOWN"
    res.ratio = weighted / total_weight
    agree = [d.label for d in res.drivers if d.agrees]
    disagree = [d.label for d in res.drivers if not d.agrees]
    if agree:
        res.reasons.append(f"{'・'.join(agree)} が向きを支持")
    if disagree:
        res.reasons.append(f"{'・'.join(disagree)} は向きと逆")
    return res
