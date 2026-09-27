# -*- coding: utf-8 -*-
"""指標の計算。

外部のテクニカル系ライブラリは使わない。式が見えないと、値がおかしいときに
「こちらの使い方が悪いのか、向こうの実装が違うのか」を切り分けられないため。

戻り値は必ず入力と同じ長さの配列で、計算できない先頭は None にする。長さを
そろえておかないと、添字がずれても気づけない。

RSI・ATR・ADX は Wilder の平滑化を使う（一般的な定義に合わせるため）。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

Num = Optional[float]


# ---------------------------------------------------------------- 基本

def sma(values: Sequence[float], period: int) -> List[Num]:
    n = len(values)
    out: List[Num] = [None] * n
    if period <= 0 or n < period:
        return out
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: Sequence[float], period: int) -> List[Num]:
    """最初の値は単純平均で起こす（一般的な作り方）。"""
    n = len(values)
    out: List[Num] = [None] * n
    if period <= 0 or n < period:
        return out
    k = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _wilder(values: Sequence[Num], period: int) -> List[Num]:
    """Wilder の平滑化。RSI・ATR・ADX で使う。"""
    n = len(values)
    out: List[Num] = [None] * n
    vals = [v for v in values if v is not None]
    if len(vals) < period:
        return out
    start = next(i for i, v in enumerate(values) if v is not None)
    first_end = start + period
    if first_end > n:
        return out
    acc = sum(v for v in values[start:first_end] if v is not None)
    prev = acc / period
    out[first_end - 1] = prev
    for i in range(first_end, n):
        v = values[i] or 0.0
        prev = (prev * (period - 1) + v) / period
        out[i] = prev
    return out


def rsi(closes: Sequence[float], period: int = 14) -> List[Num]:
    n = len(closes)
    out: List[Num] = [None] * n
    if n <= period:
        return out
    gains: List[Num] = [None] * n
    losses: List[Num] = [None] * n
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)
    ag = _wilder(gains, period)
    al = _wilder(losses, period)
    for i in range(n):
        if ag[i] is None or al[i] is None:
            continue
        if al[i] == 0:
            out[i] = 100.0
        else:
            rs = ag[i] / al[i]
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def macd(closes: Sequence[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> Tuple[List[Num], List[Num], List[Num]]:
    ef, es = ema(closes, fast), ema(closes, slow)
    line: List[Num] = [None if (a is None or b is None) else a - b
                       for a, b in zip(ef, es)]
    # シグナル線は MACD 線の EMA。None を除いた並びで計算して戻す。
    idx = [i for i, v in enumerate(line) if v is not None]
    sig: List[Num] = [None] * len(closes)
    if len(idx) >= signal:
        vals = [line[i] for i in idx]
        sm = ema(vals, signal)
        for j, i in enumerate(idx):
            sig[i] = sm[j]
    hist: List[Num] = [None if (a is None or b is None) else a - b
                       for a, b in zip(line, sig)]
    return line, sig, hist


def true_range(highs: Sequence[float], lows: Sequence[float],
               closes: Sequence[float]) -> List[Num]:
    n = len(closes)
    out: List[Num] = [None] * n
    for i in range(n):
        if i == 0:
            out[i] = highs[i] - lows[i]
        else:
            pc = closes[i - 1]
            out[i] = max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc))
    return out


def atr(highs: Sequence[float], lows: Sequence[float],
        closes: Sequence[float], period: int = 14) -> List[Num]:
    return _wilder(true_range(highs, lows, closes), period)


def bollinger(closes: Sequence[float], period: int = 20, std: float = 2.0
              ) -> Tuple[List[Num], List[Num], List[Num], List[Num]]:
    """中心線・上限・下限・幅（中心比）を返す。"""
    n = len(closes)
    mid = sma(closes, period)
    up: List[Num] = [None] * n
    low: List[Num] = [None] * n
    width: List[Num] = [None] * n
    # 移動和で求める。毎バー窓を作り直して総和を取ると O(本数×期間) になり、
    # 26銘柄×4時間足では効いてくる。和と二乗和を持ち回れば O(本数)。
    #
    # 分散 = E[x^2] - E[x]^2 は桁落ちしやすいので、平均からの偏差で持つ
    # のが本来は安全。ここは価格（正で桁が揃う）かつ期間が短いので、
    # 二乗和の形でも実用上の差は出ない。**ただし負にはなりうるので 0 で止める。**
    s = 0.0
    sq = 0.0
    for i in range(n):
        x = closes[i]
        s += x
        sq += x * x
        if i >= period:
            old_x = closes[i - period]
            s -= old_x
            sq -= old_x * old_x
        if mid[i] is None:
            continue
        m = s / period
        var = max(sq / period - m * m, 0.0)
        sd = math.sqrt(var)
        up[i] = m + std * sd
        low[i] = m - std * sd
        width[i] = (up[i] - low[i]) / m if m else None
    return mid, up, low, width


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> List[Num]:
    n = len(closes)
    out: List[Num] = [None] * n
    if n < period * 2:
        return out
    plus_dm: List[Num] = [None] * n
    minus_dm: List[Num] = [None] * n
    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
    tr = true_range(highs, lows, closes)
    atr_s = _wilder(tr, period)
    p_s = _wilder(plus_dm, period)
    m_s = _wilder(minus_dm, period)
    dx: List[Num] = [None] * n
    for i in range(n):
        if not atr_s[i] or p_s[i] is None or m_s[i] is None or atr_s[i] == 0:
            continue
        pdi = 100.0 * p_s[i] / atr_s[i]
        mdi = 100.0 * m_s[i] / atr_s[i]
        denom = pdi + mdi
        dx[i] = 0.0 if denom == 0 else 100.0 * abs(pdi - mdi) / denom
    return _wilder(dx, period)


def stochastic(highs: Sequence[float], lows: Sequence[float],
               closes: Sequence[float], k_period: int = 14, d_period: int = 3
               ) -> Tuple[List[Num], List[Num]]:
    n = len(closes)
    k: List[Num] = [None] * n
    for i in range(n):
        if i < k_period - 1:
            continue
        hh = max(highs[i - k_period + 1: i + 1])
        ll = min(lows[i - k_period + 1: i + 1])
        k[i] = 50.0 if hh == ll else 100.0 * (closes[i] - ll) / (hh - ll)
    idx = [i for i, v in enumerate(k) if v is not None]
    d: List[Num] = [None] * n
    if len(idx) >= d_period:
        vals = [k[i] for i in idx]
        sm = sma(vals, d_period)
        for j, i in enumerate(idx):
            d[i] = sm[j]
    return k, d


# ---------------------------------------------------------------- 水準

def pivot_points(prev_high: float, prev_low: float, prev_close: float
                 ) -> Dict[str, float]:
    """古典的なピボット。ひとつ前の足の高安終から作る。"""
    p = (prev_high + prev_low + prev_close) / 3.0
    return {
        "pivot": p,
        "r1": 2 * p - prev_low,
        "s1": 2 * p - prev_high,
        "r2": p + (prev_high - prev_low),
        "s2": p - (prev_high - prev_low),
    }


def rolling_extremes(highs: Sequence[float], lows: Sequence[float],
                     lookback: int = 60) -> Tuple[Num, Num]:
    """直近 lookback 本の高値・安値。単純な抵抗帯・支持帯として使う。"""
    if not highs or not lows:
        return None, None
    h = max(highs[-lookback:])
    l = min(lows[-lookback:])
    return h, l


def swing_points(highs: Sequence[float], lows: Sequence[float],
                 left: int = 2, right: int = 2, lookback: int = 120
                 ) -> Tuple[List[int], List[int]]:
    """転換点の位置を返す（高値側, 安値側）。

    左右 ``left``/``right`` 本より高い（低い）点を転換点とみなす。感度は設定で
    変えられるようにしてある。ここを固定すると、特定の相場に合わせ込む形に
    なりやすい。
    """
    n = len(highs)
    start = max(0, n - lookback)
    sh: List[int] = []
    sl: List[int] = []
    for i in range(max(start, left), n - right):
        wh = highs[i - left: i + right + 1]
        wl = lows[i - left: i + right + 1]
        if highs[i] == max(wh) and wh.count(highs[i]) == 1:
            sh.append(i)
        if lows[i] == min(wl) and wl.count(lows[i]) == 1:
            sl.append(i)
    return sh, sl


def percentile_of_last(values: Sequence[Num], lookback: int = 100) -> Num:
    """直近 lookback 本の中で、最後の値が下から何割の位置にあるか（0-1）。

    同じ値が並ぶ場合を正しく扱う。「より小さい数」だけを数えると、値がほぼ
    一定の系列で 0 か 1 に振り切れてしまう。実際それで、滑らかな相場を
    「変動が異常」と判定して取引を止める不具合を出した。
    同順位は半分だけ数える（中間順位）ことで、一定の系列は 0.5 になる。
    """
    vals = [v for v in values[-lookback:] if v is not None]
    if len(vals) < 5 or values[-1] is None:
        return None
    last = values[-1]
    below = sum(1 for v in vals if v < last)
    ties = sum(1 for v in vals if v == last)
    return (below + ties / 2.0) / len(vals)


def relative_level(values: Sequence[Num], lookback: int = 100) -> Num:
    """直近の中央値に対する、最後の値の比。

    パーセンタイルは定義上かならず 0〜1 に散らばるので、「異常な大きさ」の
    判定には使えない。値がほぼ一定の系列でも、最後の値はどこかの順位に来る。
    実際それで、滑らかな相場を「変動が異常」と判定して全部の取引を止める
    不具合を出した。

    比なら尺度に依らず、1.0 から離れているほど異常、と素直に読める。
    """
    vals = sorted(v for v in values[-lookback:] if v is not None)
    if len(vals) < 5 or values[-1] is None:
        return None
    mid = len(vals) // 2
    median = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0
    if not median:
        return None
    return values[-1] / median


def slope(values: Sequence[Num], lookback: int = 5) -> Num:
    """直近 lookback 本ぶんの変化量（1本あたり）。"""
    vals = [v for v in values[-(lookback + 1):] if v is not None]
    if len(vals) < 2:
        return None
    return (vals[-1] - vals[0]) / (len(vals) - 1)


# ---------------------------------------------------------------- まとめ

def compute_all(series, cfg: Dict) -> Dict[str, List[Num]]:
    """1つの足の並びから、指標をまとめて計算する。

    戻り値の配列はすべて入力と同じ長さ。添字のずれを起こさないため。
    """
    highs, lows, closes = series.highs, series.lows, series.closes
    e = cfg["ema"]
    bb_cfg = cfg["bollinger"]
    macd_cfg = cfg["macd"]
    st_cfg = cfg["stochastic"]

    bb_mid, bb_up, bb_low, bb_width = bollinger(
        closes, bb_cfg["period"], bb_cfg["std"])
    macd_line, macd_sig, macd_hist = macd(
        closes, macd_cfg["fast"], macd_cfg["slow"], macd_cfg["signal"])
    k, d = stochastic(highs, lows, closes,
                      st_cfg["k_period"], st_cfg["d_period"])

    out: Dict[str, List[Num]] = {
        "ema20": ema(closes, e["fast"]),
        "ema50": ema(closes, e["mid"]),
        "ema200": ema(closes, e["slow"]),
        "rsi14": rsi(closes, cfg["rsi"]["period"]),
        "macd": macd_line, "macd_signal": macd_sig, "macd_hist": macd_hist,
        "stoch_k": k, "stoch_d": d,
        "atr14": atr(highs, lows, closes, cfg["atr"]["period"]),
        "bb_middle": bb_mid, "bb_upper": bb_up,
        "bb_lower": bb_low, "bb_width": bb_width,
        "adx14": adx(highs, lows, closes, cfg["adx"]["period"]),
    }
    for p in cfg["sma"]["periods"]:
        out[f"sma{p}"] = sma(closes, p)
    return out
