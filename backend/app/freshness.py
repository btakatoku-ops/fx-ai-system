# -*- coding: utf-8 -*-
"""足の素性と鮮度を確かめる。

**本数が揃っていることと、正しい足であることは別。**

H1 を頼んだのに2時間間隔の足が240本返ってきても、本数だけ数えていれば
「揃っている」と判断してしまう。指標は1時間足のつもりで計算され、ATR も
EMA も意味の違う数字になる。それでも画面には何事もなく点数が出る。
**気づけないまま間違えるのが、いちばん危ない形。**

ここでは3つを見る。

1. **素性** — 足の間隔が、その時間足として正しいか
2. **欠落** — 抜けている区間が、市場が閉まっていた説明で片づくか
3. **鮮度** — 未来の足が混ざっていないか、最後の足が完成しているか

為替は週末に閉まる（概ね金曜 22:00 UTC 〜 日曜 22:00 UTC）。そこを跨ぐ
空白は正常なので、欠落として数えない。数えると、月曜の朝に必ず
「データが壊れている」と言い出す道具になる。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import logging
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

from .models import CandleSeries

# 為替の週末。金曜の終わりから日曜の終わりまで（設定で上書きできる）。
_FX_CLOSE_WEEKDAY = 4        # 金曜
_FX_CLOSE_HOUR = 22          # 22:00 UTC
_FX_OPEN_WEEKDAY = 6         # 日曜
_FX_OPEN_HOUR = 22           # 22:00 UTC


def _calendar() -> dict:
    """休場の設定。読めなければ週末だけの既定に戻す。

    設定が壊れているときに例外で止めるのではなく、**安全側の既定**
    （週末のみ休場）で続ける。休場を少なく見積もるほうが、本当の欠落を
    見逃さない方向に働く。
    """
    try:
        from .config import get_trading_config
        return get_trading_config().calendar or {}
    except Exception as exc:                     # noqa: BLE001
        # **黙って既定に落ちない。** 設定を直したつもりで直っていない、
        # という状態に気づけなくなる。続けるが、跡は残す。
        log.warning("市場カレンダーを読めません。既定（週末のみ休場）で"
                    "続けます: %s", exc)
        return {}


def is_holiday(day: date, cal: Optional[dict] = None) -> bool:
    """その日が休場か。

    **入れてよいのは為替市場そのものが実質閉まる日だけ。** 各国の銀行
    休業日は流動性が薄くなるだけで市場は開いている。閉場として扱うと、
    その日の本当のデータ欠落を見逃す。
    """
    cal = _calendar() if cal is None else cal
    if day.isoformat() in (cal.get("closed_dates") or []):
        return True
    return day.strftime("%m-%d") in (cal.get("recurring_closed") or [])


def is_market_closed_gap(start: datetime, end: datetime) -> bool:
    """この空白が「市場が閉まっていた」で説明できるか。

    週末を跨いでいれば説明できる。跨いでいなければ、それは欠落。
    """
    if end <= start:
        return False
    cal = _calendar()
    cur = start
    # 1日ずつ進めて、途中に閉場帯（週末・休場日）が入っているかを見る
    while cur < end:
        nxt = min(end, cur + timedelta(days=1))
        if _in_weekend(cur, cal) or _in_weekend(nxt, cal):
            return True
        cur = nxt
    return _in_weekend(start, cal) or _in_weekend(end, cal)


def _us_dst(t: datetime) -> bool:
    """米国の夏時間か。3月の第2日曜〜11月の第1日曜。

    **為替の週の切れ目は、ニューヨークの夕方で決まる。** そこが夏時間で
    1時間ずれるので、UTC で固定すると毎年半年ぶんずれる。
    """
    year = t.year
    # 3月の第2日曜 02:00 ET（= 07:00 UTC）
    march = datetime(year, 3, 8, 7, tzinfo=timezone.utc)
    start = march + timedelta(days=(6 - march.weekday()) % 7)
    # 11月の第1日曜 02:00 ET（= 06:00 UTC）
    nov = datetime(year, 11, 1, 6, tzinfo=timezone.utc)
    end = nov + timedelta(days=(6 - nov.weekday()) % 7)
    return start <= t < end


def _weekend_hours(w: dict, t: datetime) -> Tuple[int, int]:
    """その時刻に当てはまる、閉場と再開の時刻（UTC）。

    夏時間の欄があればそちらを使う。無ければ従来どおり固定値。
    **設定に無いものを勝手に作らない。**
    """
    close_h = w.get("close_hour", _FX_CLOSE_HOUR)
    open_h = w.get("open_hour", _FX_OPEN_HOUR)
    if _us_dst(t):
        close_h = w.get("close_hour_dst", close_h)
        open_h = w.get("open_hour_dst", open_h)
    return int(close_h), int(open_h)


def _in_weekend(t: datetime, cal: Optional[dict] = None) -> bool:
    """閉場中か。週末に加えて、設定された休場日も見る。"""
    if is_holiday(t.date(), cal):
        return True
    w = (cal if cal is not None else _calendar()).get("weekend") or {}
    close_wd = w.get("close_weekday", _FX_CLOSE_WEEKDAY)
    open_wd = w.get("open_weekday", _FX_OPEN_WEEKDAY)
    close_h, open_h = _weekend_hours(w, t)

    wd, hour = t.weekday(), t.hour
    if wd == close_wd and hour >= close_h:
        return True
    if wd == 5:                                   # 土曜は終日
        return True
    if wd == open_wd and hour < open_h:
        return True
    return False


def market_minutes_between(start: datetime, end: datetime,
                           step_minutes: int = 5) -> float:
    """開いていた時間だけを分で数える。閉場していた分は数えない。

    **これを壁時計で測ると、毎週月曜の朝に必ず壊れる。**
    日曜22:00に再開した直後、直前の H4 足は52時間前になる。許容が
    「3本ぶん＝12時間」なら、正常な足が「古すぎ」で弾かれ、全銘柄が
    止まる。市場が閉まっていた時間は、データが古くなった時間ではない。

    刻みで数える単純な実装にしてある。``step_minutes`` より細かい端数は
    切り上げ側に寄るが、鮮度の判定に使う分には十分。
    """
    if end <= start:
        return 0.0
    cal = _calendar()
    total = 0.0
    cur = start
    step = timedelta(minutes=step_minutes)
    while cur < end:
        nxt = min(cur + step, end)
        if not _in_weekend(cur, cal):
            total += (nxt - cur).total_seconds() / 60.0
        cur = nxt
    return total


@dataclass
class IntegrityReport:
    """足の素性の診断結果。"""

    timeframe: str
    expected_minutes: int
    bars: int = 0
    dominant_minutes: Optional[int] = None
    matching_ratio: float = 0.0
    unexplained_gaps: int = 0
    future_bars: int = 0
    unclosed_last_bar: bool = False
    ok: bool = True
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "timeframe": self.timeframe,
            "expected_minutes": self.expected_minutes,
            "bars": self.bars,
            "dominant_minutes": self.dominant_minutes,
            "matching_ratio": round(self.matching_ratio, 4),
            "unexplained_gaps": self.unexplained_gaps,
            "future_bars": self.future_bars,
            "unclosed_last_bar": self.unclosed_last_bar,
            "ok": self.ok, "reasons": self.reasons,
        }


def check_series(series: CandleSeries, expected_minutes: int,
                 now: Optional[datetime] = None,
                 min_matching_ratio: float = 0.8,
                 max_unexplained_gaps: int = 5) -> IntegrityReport:
    """1本の系列が、その時間足として筋が通っているかを見る。"""
    now = now or datetime.now(timezone.utc)
    rep = IntegrityReport(timeframe=series.timeframe,
                          expected_minutes=expected_minutes,
                          bars=len(series))
    ts = [c.timestamp for c in series.candles]
    if len(ts) < 2:
        rep.ok = False
        rep.reasons.append(f"{series.timeframe}: 足が {len(ts)} 本しかなく、"
                           f"間隔を確かめられません")
        return rep

    gaps = [int((ts[i + 1] - ts[i]).total_seconds() // 60)
            for i in range(len(ts) - 1)]
    counts = Counter(gaps)
    rep.dominant_minutes = counts.most_common(1)[0][0]
    rep.matching_ratio = counts.get(expected_minutes, 0) / len(gaps)

    # --- 素性 ---
    # いちばん多い間隔が期待と違うなら、それは別の時間足の足。
    if rep.dominant_minutes != expected_minutes:
        rep.ok = False
        rep.reasons.append(
            f"{series.timeframe}: 足の間隔が {rep.dominant_minutes} 分で、"
            f"{expected_minutes} 分ではありません。"
            f"別の時間足の足が渡されています")
    elif rep.matching_ratio < min_matching_ratio:
        rep.ok = False
        rep.reasons.append(
            f"{series.timeframe}: 正しい間隔の足が {rep.matching_ratio:.0%} "
            f"しかありません（{min_matching_ratio:.0%} 必要）")

    # --- 欠落（市場が閉まっていた説明が付くものは数えない）---
    for i, g in enumerate(gaps):
        if g <= expected_minutes:
            continue
        if is_market_closed_gap(ts[i], ts[i + 1]):
            continue
        rep.unexplained_gaps += 1
    if rep.unexplained_gaps > max_unexplained_gaps:
        rep.ok = False
        rep.reasons.append(
            f"{series.timeframe}: 説明の付かない欠落が {rep.unexplained_gaps} 箇所"
            f"あります（{max_unexplained_gaps} 箇所まで）")

    # --- 未来の足 ---
    rep.future_bars = sum(1 for t in ts if t > now)
    if rep.future_bars:
        rep.ok = False
        rep.reasons.append(
            f"{series.timeframe}: まだ来ていない時刻の足が "
            f"{rep.future_bars} 本あります")

    # --- 未確定の足 ---
    # 時刻が足の開始を指すなら、確定するのは開始 + 期間。
    # 未確定の足を確定として扱うと、高値・安値・終値が途中の値になる。
    period = timedelta(minutes=expected_minutes)
    if ts[-1] + period > now:
        rep.unclosed_last_bar = True
        rep.reasons.append(
            f"{series.timeframe}: 最後の足がまだ完成していません")

    return rep


def check_all(series_by_tf: dict, minutes_by_tf: dict,
              now: Optional[datetime] = None,
              min_matching_ratio: float = 0.8,
              max_unexplained_gaps: int = 5) -> List[IntegrityReport]:
    out: List[IntegrityReport] = []
    for tf, series in series_by_tf.items():
        minutes = minutes_by_tf.get(tf)
        if not minutes:
            continue
        out.append(check_series(series, minutes, now,
                                min_matching_ratio, max_unexplained_gaps))
    return out


# ------------------------------------------------------------------ 期限


def earliest(*values: Optional[datetime]) -> Optional[datetime]:
    """None を無視して、いちばん早い時刻を返す。"""
    real = [v for v in values if v is not None]
    return min(real) if real else None


def valid_until(now: datetime, analysis_ttl_seconds: float,
                quote_timestamp: Optional[datetime] = None,
                quote_max_age_seconds: Optional[float] = None,
                next_bar_close: Optional[datetime] = None) -> datetime:
    """この判断がいつまで有効か。

    **依存しているものの中でいちばん早く切れるものに合わせる。**
    分析そのものの寿命が60秒でも、使った気配値があと10秒で古すぎに
    なるなら、この判断も10秒で終わり。長いほうに合わせると、
    すでに根拠が失われた BUY を画面に出し続けることになる。
    """
    candidates: List[Optional[datetime]] = [
        now + timedelta(seconds=analysis_ttl_seconds)]
    if quote_timestamp is not None and quote_max_age_seconds is not None:
        candidates.append(quote_timestamp
                          + timedelta(seconds=quote_max_age_seconds))
    candidates.append(next_bar_close)
    out = earliest(*candidates)
    # 既に切れている場合も「いま」より前は返さない（負の残りは呼び手が見る）
    return out if out is not None else now


def next_bar_close(last_bar: datetime, minutes: int) -> datetime:
    """いま使っている足が確定した次に、状況が変わりうる時刻。"""
    return last_bar + timedelta(minutes=minutes * 2)

def is_market_open(t: Optional[datetime] = None) -> bool:
    """いま市場が開いているか。

    **閉まっているときに「候補が出ない」のは異常ではない。** それを
    伝えないと、週末に開いた利用者が「壊れている」と読む。
    """
    return not _in_weekend(t or datetime.now(timezone.utc))
