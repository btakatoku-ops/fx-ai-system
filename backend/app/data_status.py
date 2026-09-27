# -*- coding: utf-8 -*-
"""取り込んだ材料の鮮度。

**なぜ要るか。** 取り込みは手動なのに、古くなったことが画面から分からな
かった。とくに経済指標の予定表は36時間で古くなり、そこから先は
**全銘柄がいきなり見送りになる**。理由は各銘柄の詳細の奥にしか出ないので、
「昨日まで動いていたのに、今日は全部 NO_TRADE」という壊れ方に見える。

ここでは、材料ごとに次の3つを返す。

- ``state``     : ``OK`` / ``SOON``（もうすぐ古くなる）/ ``STALE`` / ``MISSING``
- ``age``       : どれだけ経ったか
- ``refresh``   : 取り込み直すコマンド

**推測で埋めない。** 読めない・無いものは ``MISSING`` にする。「たぶん
大丈夫」を返すくらいなら、分からないと言うほうがいい。
"""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .config import PROJECT_ROOT, TradingConfig

log = logging.getLogger(__name__)

# もうすぐ古くなる、とみなす割合。上限の8割を過ぎたら知らせる。
SOON_RATIO = 0.8


@dataclass
class Source:
    key: str
    label: str
    state: str = "MISSING"
    detail: str = ""
    as_of: Optional[str] = None
    age_hours: Optional[float] = None
    limit_hours: Optional[float] = None
    refresh: str = ""
    blocks_trading: bool = False
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict:
        return {
            "key": self.key, "label": self.label, "state": self.state,
            "detail": self.detail, "as_of": self.as_of,
            "age_hours": (round(self.age_hours, 1)
                          if self.age_hours is not None else None),
            "limit_hours": self.limit_hours,
            "refresh": self.refresh,
            "blocks_trading": self.blocks_trading,
            "notes": self.notes,
        }


def _classify(age_h: float, limit_h: Optional[float]) -> str:
    if limit_h is None or limit_h <= 0:
        return "OK"
    if age_h > limit_h:
        return "STALE"
    if age_h >= limit_h * SOON_RATIO:
        return "SOON"
    return "OK"


def _parse_iso(value: object) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _calendar(cfg: TradingConfig, now: datetime) -> Source:
    s = Source(key="calendar", label="経済指標の予定表",
               refresh="python scripts/fetch_calendar.py",
               blocks_trading=True)
    limit = float(cfg.news.get("source_max_age_hours", 36))
    s.limit_hours = limit
    # **置き場を2か所に書かない。** news が読んでいるのと同じ場所を見る。
    # 別々に持つと、片方だけ差し替えたときに「古いのに OK」と出る。
    from . import news as news_mod

    path = Path(news_mod.CALENDAR_PATH)
    if not path.exists():
        s.detail = "取り込んでいません。指標の有無を判断できないため、点数は0点のままです"
        return s
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        s.detail = f"読めません（{exc}）"
        return s

    fetched = _parse_iso(payload.get("fetched_at"))
    events = payload.get("events") or []
    if fetched is None:
        s.detail = "取得日時がありません。古いかどうかを確かめられないので建てません"
        s.state = "STALE"
        return s

    s.as_of = fetched.isoformat()
    s.age_hours = (now - fetched).total_seconds() / 3600.0
    s.state = _classify(s.age_hours, limit)
    s.detail = f"{len(events)} 件"
    if events:
        first = _parse_iso(events[0].get("at"))
        last = _parse_iso(events[-1].get("at"))
        if first and last:
            s.detail += f"（収録 {first:%m/%d} 〜 {last:%m/%d}）"
    if s.state == "STALE":
        s.notes.append(
            "**古い予定表で「指標なし」と判断しないため、全銘柄を見送ります。**"
            "取り込み直してください")
    elif s.state == "SOON":
        remain = max(0.0, limit - s.age_hours)
        s.notes.append(
            f"あと {remain:.0f} 時間で古くなります。そこから先は全銘柄が"
            f"見送りになります")
    return s


def _last_timestamp(path: Path) -> Optional[datetime]:
    """CSV の最後の行の時刻。**読めなければ None。推測しない。**"""
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
    except OSError:
        return None
    for row in reversed(rows[1:]):
        if row and row[0]:
            dt = _parse_iso(row[0])
            if dt:
                return dt
    return None


def _drivers(cfg: TradingConfig, now: datetime) -> Source:
    s = Source(key="drivers", label="相関の材料",
               refresh="python scripts/fetch_drivers.py")
    # ここは**知らせるための目安**。実際に使うかどうかを決めるのは
    # correlation.max_driver_lag_bars で、銘柄側の最後の足と比べて
    # 遅れていれば、その材料は使わない（0点になり、満点からも外れる）。
    # **建てるのは止めない。**
    limit = float(cfg.correlation.get("source_max_age_hours", 24))
    s.limit_hours = limit
    directory = PROJECT_ROOT / "data" / "drivers"
    names = list((cfg.correlation.get("drivers") or {}).keys())
    if not directory.exists() or not names:
        s.detail = "取り込んでいません。相関は測れず、その回は満点から外れます"
        return s

    newest: Optional[datetime] = None
    found = 0
    missing: List[str] = []
    for name in names:
        path = directory / f"{name}_{cfg.correlation['timeframe']}.csv"
        if not path.exists():
            missing.append(name)
            continue
        ts = _last_timestamp(path)
        if ts is None:
            missing.append(name)
            continue
        found += 1
        newest = ts if newest is None or ts > newest else newest

    if newest is None:
        s.detail = "どの材料も読めません"
        return s
    s.as_of = newest.isoformat()
    # **閉場していた時間を古さに数えない。** 相関の材料も相場のデータ
    # なので、市場が閉じているあいだは新しい足が出ない。壁時計で測ると
    # 週末じゅう「古い」と言い続けることになる（足の鮮度は engine 側で
    # 同じ考え方にしてある）。
    from .freshness import market_minutes_between

    s.age_hours = market_minutes_between(newest, now) / 60.0
    s.state = _classify(s.age_hours, limit)
    s.detail = f"{found} / {len(names)} 件"
    if missing:
        s.notes.append(f"読めない材料: {', '.join(missing)}")
    lag_bars = int(cfg.correlation.get("max_driver_lag_bars", 3))
    tf_minutes = int(cfg.correlation.get("bar_minutes", 60))
    if s.state != "OK":
        s.notes.append(
            f"取り込み直してください。銘柄の最後の足から "
            f"{lag_bars * tf_minutes / 60:.0f} 時間より遅れた材料は使いません"
            f"（その回は相関が0点になり、満点からも外れます）")
    return s


def _bars(cfg: TradingConfig, now: datetime, provider_name: str) -> Source:
    """相場の足そのものの鮮度。

    **「取り込み忘れ」と「配信元に穴がある」を分ける。** どちらも
    「足が古い」に見えるが、やることが正反対になる。前者は取り込めば
    直り、後者は取り込んでも直らない。

    実際に見た例（2026-09-23 23:45 UTC / Yahoo）。M5・M15・H1 のどれも
    19:25〜23:00 が丸ごと無い。取り込んだ直後なのに H1 は 4.7 時間前に
    なり、鮮度の条件で止まる。**取り込み直しても直らない。**
    """
    s = Source(key="bars", label="相場の足",
               refresh="python scripts/fetch_yahoo.py --out data/real")
    if provider_name != "csv":
        s.state = "OK"
        s.detail = f"供給元は {provider_name}（ファイルを見ていません）"
        return s

    from .config import get_settings
    from .focus import symbols as focus_symbols
    from .freshness import market_minutes_between

    entry_tf = cfg.backtest["entry_timeframe"]
    bias_tf = cfg.role_timeframe("bias")
    directory = Path(get_settings().csv_dir)
    targets = focus_symbols(cfg, "focus") or [
        p.symbol for p in cfg.enabled_pairs()[:3]]
    # 許容は engine と同じ考え方。上位足1本ぶんを目安にする。
    s.limit_hours = cfg.timeframe_minutes(bias_tf) / 60.0 * 2

    worst: Optional[float] = None
    worst_label = ""
    fetched: Optional[datetime] = None
    for symbol in targets:
        for tf in (entry_tf, bias_tf):
            path = directory / f"{symbol}_{tf}.csv"
            ts = _last_timestamp(path)
            if ts is None:
                continue
            age = market_minutes_between(ts, now) / 60.0
            if worst is None or age > worst:
                worst, worst_label = age, f"{symbol} {tf}"
            try:
                wrote = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                fetched = wrote if fetched is None or wrote > fetched else fetched
            except OSError:
                pass

    if worst is None:
        s.detail = "足を読めません。取り込んでください"
        return s

    s.age_hours = worst
    s.as_of = fetched.isoformat() if fetched else None
    s.state = _classify(worst, s.limit_hours)
    s.detail = f"いちばん古いのは {worst_label}"

    if s.state != "OK" and fetched is not None:
        since_fetch = (now - fetched).total_seconds() / 3600.0
        if since_fetch < 1.0:
            # **取り込んだ直後に古い＝配信元の穴。** 取り込み直しても直らない。
            s.notes.append(
                f"取り込みは {since_fetch * 60:.0f} 分前に済んでいます。"
                f"それでも足が古いので、**配信元にその時間帯のデータが"
                f"ありません**。取り込み直しても直りません。"
                f"配信が追いつくまで待つことになります")
            s.refresh = "（取り込み直しでは直りません）"
        else:
            s.notes.append("取り込み直してください")
    return s


def _swap(cfg: TradingConfig, now: datetime) -> Source:
    s = Source(key="swap", label="スワップ表",
               refresh="python scripts/fetch_swap_gaitame.py → import_swap.py")
    swap = cfg.swap if isinstance(getattr(cfg, "swap", None), dict) else {}
    if not swap or not swap.get("available"):
        s.detail = "取り込んでいません。計画のスワップ欄は空になります"
        return s
    days = float(swap.get("stale_after_days", 7))
    s.limit_hours = days * 24
    as_of = _parse_iso(swap.get("as_of"))
    if as_of is None:
        s.detail = "as_of がありません"
        s.state = "STALE"
        return s
    s.as_of = as_of.date().isoformat()
    s.age_hours = (now - as_of).total_seconds() / 3600.0
    s.state = _classify(s.age_hours, s.limit_hours)
    s.detail = f"{len(swap.get('table') or {})} 銘柄　{swap.get('source', '')}"
    if s.state != "OK":
        s.notes.append("古いスワップは目安になりません。取り込み直してください")
    return s


def collect(cfg: TradingConfig, provider_name: str,
            now: Optional[datetime] = None) -> Dict:
    """材料ごとの鮮度をまとめる。"""
    now = now or datetime.now(timezone.utc)
    sources = [_bars(cfg, now, provider_name), _calendar(cfg, now),
               _drivers(cfg, now), _swap(cfg, now)]

    # いちばん悪い状態を全体の状態にする。**良いほうに寄せない。**
    order = {"OK": 0, "MISSING": 1, "SOON": 2, "STALE": 3}
    worst = max(sources, key=lambda s: order.get(s.state, 0))
    blocking = [s for s in sources if s.state == "STALE" and s.blocks_trading]

    from .freshness import is_market_open

    return {
        "checked_at": now.isoformat(),
        "provider": provider_name,
        # **閉まっているときに「作り直せ」と言わない。** 作り直しても
        # 新しい気配値は出てこない。出せない理由が違う。
        "market_open": is_market_open(now),
        "state": worst.state,
        "blocking": [s.key for s in blocking],
        "sources": [s.as_dict() for s in sources],
    }
