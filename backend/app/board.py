# -*- coding: utf-8 -*-
"""朝のボード。**予測はしない。事実を揃え、やめるべき時をはっきり言う。**

これまでの道具は「代わりに決めよう」として、決められなかった。中心の
BUY/SELL に当たる根拠が無い（実データで、向きを揃えるとコイン投げと差が
無かった）ので、周りをいくら固めても判断材料にならなかった。

ここでは役割を変える。

| 段 | 中身 | 性質 |
|---|---|---|
| 事実 | 価格の位置、前日の高安、今日の値幅の消化率、市場時間、刻限 | 当たり外れなし |
| 要因 | トレンド・モメンタム・ファンダを▲▼■で並べる | 意見を含む。出典を付ける |
| 判定 | 除外すべきものは機械がはっきり除外する。残りは材料を並べるだけ | **「発注」とは書かない** |

**除外は機械、実行は人。** 実測で効果が確認できたのは「見送る判断」
（強制条件、+15〜18%, p<0.001）だけなので、機械にはそこを任せる。

## 書かないこと

- 「発注」「買い推奨」のような、実行を促す言葉
- 到達確率・期待値のような、測っていない数字
- ファンダの見立てを機械が作ること（使う人が入れる。無ければ「未入力」）
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config import PROJECT_ROOT, TradingConfig
from .freshness import _us_dst

log = logging.getLogger(__name__)

FUNDAMENTALS_PATH = PROJECT_ROOT / "data" / "fundamentals.json"

UP, DOWN, FLAT, NONE = "up", "down", "neutral", "none"
MARK = {UP: "▲", DOWN: "▼", FLAT: "■", NONE: "—"}


# ------------------------------------------------------------ 為替の1日

def fx_day_start(t: datetime) -> datetime:
    """``t`` が属する為替の1日の始まり（UTC）。

    **為替の1日はニューヨーク17時で替わる。** 夏時間で1時間ずれるので、
    UTC で 21:00（夏）/ 22:00（冬）。日本時間の0時で切ると、東京の朝の
    値動きが「前日」に入ってしまい、前日の高安も今日の値幅もずれる。
    """
    t = t.astimezone(timezone.utc)
    for back in range(0, 3):
        day = (t - timedelta(days=back)).replace(minute=0, second=0,
                                                microsecond=0)
        hour = 21 if _us_dst(day.replace(hour=12)) else 22
        start = day.replace(hour=hour)
        if start <= t:
            return start
    return t.replace(hour=0, minute=0, second=0, microsecond=0)


def _aligned(t: datetime, minutes: int) -> bool:
    return not t.second and not t.microsecond and         (t.hour * 60 + t.minute) % minutes == 0


def _group_days(bars: Sequence) -> Dict[datetime, List]:
    out: Dict[datetime, List] = {}
    for b in bars:
        out.setdefault(fx_day_start(b.timestamp), []).append(b)
    return out


# ------------------------------------------------------------ 事実

@dataclass
class Level:
    price: float
    label: str
    kind: str                     # support / resistance / open / round

    def as_dict(self) -> Dict:
        return {"price": self.price, "label": self.label, "kind": self.kind}


@dataclass
class Facts:
    price: Optional[float] = None
    price_at: Optional[str] = None
    today_open: Optional[float] = None
    today_high: Optional[float] = None
    today_low: Optional[float] = None
    today_change_pct: Optional[float] = None
    prev_high: Optional[float] = None
    prev_low: Optional[float] = None
    prev_note: Optional[str] = None
    adr: Optional[float] = None
    adr_days: int = 0
    used_ratio: Optional[float] = None
    range_high: Optional[float] = None
    range_low: Optional[float] = None
    position: Optional[float] = None
    room_up_adr: Optional[float] = None
    room_down_adr: Optional[float] = None
    round_above: Optional[float] = None
    round_below: Optional[float] = None
    levels: List[Level] = field(default_factory=list)
    session: str = ""
    minutes_to_deadline: Optional[float] = None
    deadline_jst: Optional[str] = None
    next_event: Optional[str] = None
    minutes_to_event: Optional[float] = None
    event_note: Optional[str] = None
    market_open: bool = True
    day_note: Optional[str] = None

    def as_dict(self) -> Dict:
        d = {k: v for k, v in self.__dict__.items() if k != "levels"}
        d["levels"] = [lv.as_dict() for lv in self.levels]
        return d


def _round_levels(price: float, step: float) -> Tuple[float, float]:
    below = (price // step) * step
    above = below + step
    return round(above, 6), round(below, 6)


def _session(now: datetime, cfg: TradingConfig) -> str:
    s = cfg.signal.get("session") or {}
    h = now.astimezone(timezone.utc).hour
    names = []
    for key, label in (("tokyo", "東京"), ("london", "ロンドン"),
                       ("newyork", "ニューヨーク")):
        w = s.get(key)
        if w and w["start"] <= h < w["end"]:
            names.append(label)
    return "・".join(names) if names else "主要市場の外"


def facts_for(cfg: TradingConfig, symbol: str, h1: Sequence, m15: Sequence,
              now: datetime) -> Facts:
    """価格まわりの事実。**予測は含めない。**"""
    bc = cfg.board
    spec = cfg.pair(symbol)
    f = Facts()

    # **時刻が揃っていない行は足ではない。** 配信元が末尾に付ける「いまの
    # 気配」（22:59:00 など）が紛れていると、前日の高安が同じ値になる。
    # 取り込み側でも落としているが、古いファイルのために、ここでも落とす。
    #
    # **閉場中に始まる足も除く。** 夏時間の金曜は 21:00 UTC で閉じるのに、
    # 配信元は 21:00 始まりの足を1本付けてくる。それが「新しい1日」になり、
    # 今日の高値と安値が同じ値、という壊れ方で出た。
    from .freshness import is_market_open

    h1 = [b for b in h1
          if _aligned(b.timestamp, 60) and is_market_open(b.timestamp)]
    m15 = [b for b in m15
           if _aligned(b.timestamp, 15) and is_market_open(b.timestamp)]
    if not m15:
        return f

    last = m15[-1]
    f.price = float(last.close)
    f.price_at = last.timestamp.isoformat()

    # --- 今日（為替の1日） ---
    f.market_open = is_market_open(now)
    today_start = fx_day_start(now)
    if not any(b.timestamp >= today_start for b in m15):
        # **閉まっている日に「今日」を空で出さない。** 直近の取引日を
        # 今日として見せ、そうしていることを書く。
        today_start = fx_day_start(last.timestamp)
        f.day_note = ("市場が閉まっているため、直近の取引日"
                      f"（{today_start.astimezone(timezone(timedelta(hours=9))):%m/%d} 朝〜）"
                      "の値で出しています")
    today = [b for b in m15 if b.timestamp >= today_start]
    if today:
        f.today_open = float(today[0].open)
        f.today_high = max(float(b.high) for b in today)
        f.today_low = min(float(b.low) for b in today)
        if f.today_open:
            f.today_change_pct = round(
                (f.price - f.today_open) / f.today_open * 100, 3)

    # --- 過去の日（1時間足から） ---
    days = _group_days(h1)
    need = int(bc.get("min_bars_per_day", 18))
    past = sorted(d for d in days if d < today_start)
    full = [d for d in past if len(days[d]) >= need]

    if past:
        prev = days[past[-1]]
        f.prev_high = max(float(b.high) for b in prev)
        f.prev_low = min(float(b.low) for b in prev)
        if len(prev) < need:
            # **欠けた日をそのまま「前日」として出さない。** 高安が狭く見える。
            f.prev_note = (f"前日は足が {len(prev)} 本しかなく"
                           f"（{need} 本未満）、高安が狭く出ている可能性があります")

    n_adr = int(bc.get("adr_days", 20))
    ranges = [max(float(b.high) for b in days[d]) - min(float(b.low) for b in days[d])
              for d in full[-n_adr:]]
    if len(ranges) >= 5:
        f.adr = sum(ranges) / len(ranges)
        f.adr_days = len(ranges)
        if f.today_high is not None and f.adr > 0:
            f.used_ratio = round((f.today_high - f.today_low) / f.adr, 3)

    n_range = int(bc.get("range_days", 20))
    window = [b for d in past[-n_range:] for b in days[d]] + list(today)
    if window:
        f.range_high = max(float(b.high) for b in window)
        f.range_low = min(float(b.low) for b in window)
        span = f.range_high - f.range_low
        if span > 0:
            f.position = round((f.price - f.range_low) / span, 3)
        if f.adr and f.adr > 0:
            f.room_up_adr = round((f.range_high - f.price) / f.adr, 2)
            f.room_down_adr = round((f.price - f.range_low) / f.adr, 2)

    step = (bc.get("round_step") or {}).get(
        "jpy" if spec.pip >= 0.01 else "other", 0.5 if spec.pip >= 0.01 else 0.005)
    f.round_above, f.round_below = _round_levels(f.price, float(step))

    # --- 帯に描く節目。**出所の違う水準を混ぜて並べる。どれも事実。** ---
    lv: List[Level] = []
    if f.range_low is not None:
        lv.append(Level(f.range_low, f"{n_range}日安値", "support"))
    if f.range_high is not None:
        lv.append(Level(f.range_high, f"{n_range}日高値", "resistance"))
    if f.prev_low is not None:
        lv.append(Level(f.prev_low, "前日安値", "support"))
    if f.prev_high is not None:
        lv.append(Level(f.prev_high, "前日高値", "resistance"))
    if f.today_open is not None:
        lv.append(Level(f.today_open, "今日の始値", "open"))
    lv.append(Level(f.round_below, "キリ番", "round"))
    lv.append(Level(f.round_above, "キリ番", "round"))
    f.levels = sorted(lv, key=lambda x: x.price)

    # --- 時間 ---
    f.session = _session(now, cfg)
    from .day_trade import exit_deadline

    dl = exit_deadline(cfg.filters, now)
    if dl is not None:
        f.minutes_to_deadline = round((dl - now).total_seconds() / 60.0)
        f.deadline_jst = dl.astimezone(
            timezone(timedelta(hours=9))).strftime("%m/%d %H:%M")

    try:
        from .news import load_calendar

        _, _, events = load_calendar()
        relevant = set(cfg.news.get("relevant_impacts", ["High", "Medium"]))
        for e in events:
            if e.at > now and e.impact in relevant and e.relevant_to(spec):
                f.next_event = f"{e.currency or '—'} {e.title}（{e.impact}）"
                f.minutes_to_event = round((e.at - now).total_seconds() / 60.0)
                break
        if f.next_event is None:
            # **「予定なし」と「予定表の外」を混ぜない。** 予定表が今週ぶん
            # しか無いと、週末は「指標なし」に見えてしまう。
            if events and events[-1].at < now:
                f.event_note = (f"予定表は {events[-1].at.astimezone(timezone(timedelta(hours=9))):%m/%d} "
                                f"までしか入っていません。この先の指標は分かりません")
            elif not events:
                f.event_note = "予定表を取り込んでいません"
            else:
                f.event_note = "予定表の範囲では、この銘柄に関わる重要指標はありません"
    except Exception as exc:                     # noqa: BLE001
        log.warning("指標の予定を読めません: %s", exc)
    return f


# ------------------------------------------------------------ 要因

@dataclass
class Factor:
    key: str
    label: str
    view: str = NONE
    text: str = ""
    source: Optional[str] = None
    as_of: Optional[str] = None
    stale: bool = False

    def as_dict(self) -> Dict:
        return {"key": self.key, "label": self.label, "view": self.view,
                "mark": MARK[self.view], "text": self.text,
                "source": self.source, "as_of": self.as_of,
                "stale": self.stale}


def _last(ind: Optional[Any], key: str) -> Optional[float]:
    if ind is None:
        return None
    v = getattr(ind, key, None)
    return float(v) if v is not None else None


def trend_factor(analysis) -> Factor:
    """H4 と H1 の EMA20/50 の並び。**両方そろったときだけ▲▼。**"""
    f = Factor("trend", "トレンド")
    ind = analysis.indicators or {}
    marks = []
    parts = []
    for tf in ("H4", "H1"):
        e20, e50 = _last(ind.get(tf), "ema20"), _last(ind.get(tf), "ema50")
        if e20 is None or e50 is None:
            parts.append(f"{tf} 不明")
            continue
        up = e20 > e50
        marks.append(UP if up else DOWN)
        parts.append(f"{tf} は EMA20 が EMA50 の{'上' if up else '下'}")
    if len(marks) == 2 and marks[0] == marks[1]:
        f.view = marks[0]
    elif marks:
        f.view = FLAT
    f.text = "、".join(parts)
    return f


def momentum_factor(analysis, cfg: TradingConfig) -> Factor:
    """M15 の RSI と MACD。**同じ側にそろったときだけ▲▼。**"""
    f = Factor("momentum", "モメンタム")
    m = (analysis.indicators or {}).get("M15")
    rsi, hist = _last(m, "rsi14"), _last(m, "macd_hist")
    if rsi is None or hist is None:
        f.text = "RSI か MACD が取れていません"
        return f
    mc = cfg.board.get("momentum") or {}
    up_at, down_at = float(mc.get("rsi_up", 55)), float(mc.get("rsi_down", 45))
    if rsi >= up_at and hist > 0:
        f.view = UP
    elif rsi <= down_at and hist < 0:
        f.view = DOWN
    else:
        f.view = FLAT
    f.text = (f"RSI {rsi:.0f}・MACD は0の{'上' if hist > 0 else '下'}"
              + ("（どちらも上向き）" if f.view == UP else
                 "（どちらも下向き）" if f.view == DOWN else "（そろっていない）"))
    return f


def load_fundamentals(path: Optional[Path] = None) -> Dict[str, Dict]:
    p = Path(path) if path else FUNDAMENTALS_PATH
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("views") or {}
    except (OSError, ValueError) as exc:
        log.warning("ファンダの見立てを読めません: %s", exc)
        return {}


def fundamentals_factor(cfg: TradingConfig, symbol: str, now: datetime,
                        views: Optional[Dict[str, Dict]] = None) -> Factor:
    """使う人が入れた見立て。**機械は作らない。無ければ「未入力」。**"""
    f = Factor("fundamentals", "ファンダ")
    row = (views if views is not None else load_fundamentals()).get(symbol)
    if not row:
        f.text = "未入力（このアプリは経済や政策を判断しません）"
        return f
    view = str(row.get("view") or "")
    if view not in (UP, DOWN, FLAT):
        f.text = f"見立ての書き方が読めません: {view!r}"
        return f
    f.text = str(row.get("note") or "")
    f.source = row.get("source")
    f.as_of = row.get("as_of")
    try:
        as_of = datetime.fromisoformat(str(f.as_of)).replace(tzinfo=timezone.utc)
        age = (now - as_of).total_seconds() / 86400.0
    except (TypeError, ValueError):
        age = None
    limit = float(cfg.board.get("fundamentals_stale_days", 7))
    if age is None or age > limit:
        # **古い見立てで一致・対立を判断しない。** 見せるが、数えない。
        f.stale = True
        f.view = NONE
        f.text = (f"{f.text}（{'日付なし' if age is None else f'{age:.0f}日前'}"
                  f"の見立て。{limit:.0f}日を過ぎたので判定に使いません）")
        return f
    f.view = view
    return f


# ------------------------------------------------------------ 判定

def _zone_hit(cfg: TradingConfig, symbol: str,
              price: Optional[float]) -> Optional[Dict]:
    if price is None:
        return None
    for z in (cfg.board.get("caution_zones") or {}).get(symbol, []):
        try:
            if float(z["from"]) <= price <= float(z["to"]):
                return z
        except (KeyError, TypeError, ValueError):
            continue
    return None


def verdict_for(cfg: TradingConfig, symbol: str, analysis, facts: Facts,
                factors: Sequence[Factor], now: datetime) -> Dict:
    """除外するか、材料がそろっているか。**「発注」とは書かない。**"""
    bc = cfg.board
    exclude: List[str] = []
    cautions: List[str] = []

    # 1) エンジンの強制条件（鮮度・費用・上位足の対立・指標の前後など）
    #
    # **強制条件で止まったものだけ**を除外の理由にする。期限切れは信号の
    # 話で、ボードの話ではない。点数の説明（「様子見の水準です」）も除外の
    # 理由ではない。
    closed = not facts.market_open
    if getattr(analysis, "filter_blocked", False):
        for r in analysis.invalidation_reasons or []:
            if "有効期限が切れています" in r:
                continue
            # 閉まっている日に「刻限まであと何分」は、終わった日の話
            if closed and "手仕舞いの刻限" in r:
                continue
            exclude.append(r.replace("**", ""))     # 端末向けの強調は画面に出さない
            if len(exclude) >= 2:
                break

    # 2) 手で入れた警戒帯（介入など）
    zone = _zone_hit(cfg, symbol, facts.price)
    if zone:
        msg = (f"{zone.get('label', '警戒帯')}（{zone['from']}〜{zone['to']}）"
               f"の中です。{zone.get('why', '')}")
        (exclude if zone.get("action", "exclude") == "exclude"
         else cautions).append(msg)

    # 3) 日計りの刻限（**閉まっている日は時間の話をしない**）
    from .day_trade import can_open

    ok, why = can_open(cfg.filters, now)
    if not closed and not ok and why:
        why = why.replace("**", "")
        if why not in exclude:                   # 強制条件の側と同じ文なら1回だけ
            exclude.append(why)

    # 4) 今日の値幅（閉まっている日は、終わった日の話なので判定に使わない）
    if not closed and facts.used_ratio is not None:
        if facts.used_ratio >= float(bc.get("exhaust_exclude_ratio", 1.2)):
            exclude.append(f"今日はすでに普段の {facts.used_ratio:.1f} 倍動いています。"
                           f"動き切った後の追いかけになりやすいので除外します")
        elif facts.used_ratio >= float(bc.get("exhaust_caution_ratio", 0.9)):
            cautions.append(f"今日の値幅は普段の {facts.used_ratio:.1f} 倍まで来ています")

    # 5) 要因の一致・対立
    views = {f.key: f.view for f in factors}
    directional = [v for v in views.values() if v in (UP, DOWN)]
    ups, downs = directional.count(UP), directional.count(DOWN)
    trend, fund = views.get("trend"), views.get("fundamentals")
    if trend in (UP, DOWN) and fund in (UP, DOWN) and trend != fund:
        exclude.append("テクニカルとファンダが逆を向いています（対立）")

    lean = None
    if ups >= 2 and downs == 0:
        lean = UP
    elif downs >= 2 and ups == 0:
        lean = DOWN

    # 6) そろった向きに余地があるか
    min_room = float(bc.get("min_room_adr", 0.3))
    if lean == UP and facts.room_up_adr is not None and facts.room_up_adr < min_room:
        cautions.append(f"上の余地が普段の値幅の {facts.room_up_adr:.1f} 倍しかありません"
                        f"（{bc.get('range_days', 20)}日高値まで）")
    if lean == DOWN and facts.room_down_adr is not None and facts.room_down_adr < min_room:
        cautions.append(f"下の余地が普段の値幅の {facts.room_down_adr:.1f} 倍しかありません"
                        f"（{bc.get('range_days', 20)}日安値まで）")

    if closed:
        cautions.insert(0, "市場が閉まっています。開いてから改めて見てください")

    if exclude:
        state, label = "excluded", "除外"
    elif lean == UP:
        state, label = "aligned_up", "材料は上にそろっている"
    elif lean == DOWN:
        state, label = "aligned_down", "材料は下にそろっている"
    else:
        state, label = "mixed", "まちまち"

    return {
        "state": state,
        "label": label,
        "lean": lean,
        "counts": {"up": ups, "down": downs,
                   "neutral": sum(1 for v in views.values() if v == FLAT),
                   "none": sum(1 for v in views.values() if v == NONE)},
        "exclude": exclude,
        "cautions": cautions,
        # **決めるのは使う人。** ボードは実行を促さない。
        "note": ("除外の理由があれば見送りを勧めます。それ以外は材料を並べて"
                 "いるだけで、決めるのはご自身です。材料がそろった日の成績は"
                 "まだ測れていません（記録して採点中）。"),
    }


def build_board(cfg: TradingConfig, provider, symbol: str,
                analysis, now: Optional[datetime] = None) -> Dict:
    """1銘柄のボード。"""
    now = now or datetime.now(timezone.utc)
    h1 = m15 = []
    try:
        h1 = provider.get_candles(symbol, "H1", limit=24 * 40).candles
        m15 = provider.get_candles(symbol, "M15", limit=4 * 24 * 3).candles
    except Exception as exc:                     # noqa: BLE001
        log.warning("ボードの足を取れません %s: %s", symbol, exc)

    facts = facts_for(cfg, symbol, h1, m15, now)
    factors = [trend_factor(analysis), momentum_factor(analysis, cfg),
               fundamentals_factor(cfg, symbol, now)]
    verdict = verdict_for(cfg, symbol, analysis, facts, factors, now)
    brief = _brief_for(symbol, now)
    _brief_cautions(verdict, brief)
    spec = cfg.pair(symbol)
    return {
        "pair": symbol,
        "digits": spec.digits,
        "generated_at": now.isoformat(),
        "facts": facts.as_dict(),
        "factors": [f.as_dict() for f in factors],
        "verdict": verdict,
        "brief": brief,
    }


def _brief_for(symbol: str, now: datetime) -> Optional[Dict]:
    """取り込んだ FXモーニングブリーフの、この銘柄の判定（無ければ None）。"""
    from . import brief_import

    return brief_import.for_pair(brief_import.load_latest(), symbol, now)


def _brief_cautions(verdict: Dict, brief: Optional[Dict]) -> None:
    """ブリーフの判定を「気をつけること」に添える。

    **除外の理由にはしない。** ブリーフの判定はまだ測れていない（採点中）。
    アプリの除外は、強制条件と明示した決まりだけ。古いブリーフは使わない。
    """
    if not brief or brief.get("age_days") not in (0,):
        return
    lean = verdict.get("lean")
    b = brief.get("lean")
    if brief.get("excluded"):
        verdict["cautions"].append("FXモーニングブリーフでは除外・見送りの判定です")
    elif lean in (UP, DOWN) and b in (UP, DOWN) and lean != b:
        verdict["cautions"].append(
            "FXモーニングブリーフとこのボードで、向きが逆です")


# ------------------------------------------------------------ 文字で出す

def as_text(board: Dict) -> List[str]:
    """端末・朝の確認向けの数行。**「発注」とは書かない。**"""
    f, v, d = board["facts"], board["verdict"], board.get("digits", 3)
    fmt = (lambda x: "—" if x is None else f"{x:.{d}f}")
    lines = [f"■ {board['pair']}  {v['label']}"]
    for fac in board["factors"]:
        src = ""
        if fac.get("source"):
            src = f"（{fac['source']}・{fac.get('as_of') or '日付なし'}）"
        lines.append(f"    {fac['label']:<6} {fac['mark']}  {fac['text']}{src}")
    if f.get("price") is not None:
        used = (f"普段の{f['used_ratio']:.1f}倍" if f.get("used_ratio") is not None
                else "値幅不明")
        pos = (f"20日の{f['position'] * 100:.0f}%の位置"
               if f.get("position") is not None else "")
        lines.append(f"    価格    {fmt(f['price'])}  今日 {fmt(f.get('today_low'))}〜"
                     f"{fmt(f.get('today_high'))}（{used}）  {pos}")
        near = [lv for lv in f.get("levels", [])
                if lv["kind"] in ("support", "resistance")]
        if near:
            lines.append("    節目    " + "  ".join(
                f"{fmt(lv['price'])}({lv['label']})" for lv in near))
    br = board.get("brief")
    if br:
        mk = {UP: "▲", DOWN: "▼", FLAT: "■", "conflict": "■対立"}.get(br.get("lean"), "—")
        ja = {"trend": "トレンド", "momentum": "モメンタム",
              "fundamentals": "ファンダ", "range": "レンジ"}
        sig = " ".join(f"{ja.get(k, k)}{v['mark']}"
                       for k, v in (br.get("signals") or {}).items())
        age = br.get("age_days")
        when = "今日" if age == 0 else f"{age}日前" if age is not None else "日付不明"
        lines.append(f"    ブリーフ {mk}{'（除外・見送り）' if br.get('excluded') else ''}"
                     f"  {sig}  （{br.get('brief_date')}・{when}）")
    if f.get("day_note"):
        lines.append(f"    ※ {f['day_note']}")
    for x in v.get("exclude", []):
        lines.append(f"    → 除外: {x}")
    for x in v.get("cautions", []):
        lines.append(f"    → 注意: {x}")
    return lines
