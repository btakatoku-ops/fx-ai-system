# -*- coding: utf-8 -*-
"""朝の確認。**今日この道具が使えるのか、から書く。**

毎朝いちばん知りたいのは「今日、判断支援を受けられるのか」で、次が
「受けられないなら何をすればいいか」。26銘柄の点数を眺めるのはその後。

実際、予定表が古いだけで3日間まるごと見送りになっていたのに、気づくには
銘柄の詳細を開いて理由を読むしかなかった。**それを朝に1枚で出す。**

## 出す順番

1. **今日使えるか**（``ready``）。使えないなら、その理由とやること。
2. **支援する銘柄の今**（``config/focus.json`` の focus だけ）。
3. **今日の指標**（発表前後は建てないので、時間帯を先に知っておく）。
4. **昨日からの変化**（判断の記録より）。

## 書かないこと

**今日の見通しは書かない。** 優位性は測れていない（docs/backtesting.md）。
「今日は買い目線」のような文は、根拠が無いまま読み手を動かす。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .config import TradingConfig
from .data_status import collect as collect_data_status
from .focus import tier_of
from .freshness import is_market_open
from .news import load_calendar
from .trade_logger import recent as recent_decisions
from .trade_logger import summary as decision_summary

log = logging.getLogger(__name__)


def _upcoming_events(cfg: TradingConfig, now: datetime,
                     hours: int = 24) -> List[Dict[str, Any]]:
    """これから ``hours`` 時間の重要指標。**予定表が無ければ空。**"""
    _, _, events = load_calendar()
    if not events:
        return []
    relevant = set(cfg.news.get("relevant_impacts", ["High", "Medium"]))
    until = now + timedelta(hours=hours)
    out: List[Dict[str, Any]] = []
    for e in events:
        if e.at < now or e.at > until or e.impact not in relevant:
            continue
        window = (cfg.news.get("blackout") or {}).get(e.impact) or {}
        out.append({
            "at": e.at.isoformat(),
            "title": e.title,
            "currency": e.currency,
            "impact": e.impact,
            "minutes_away": round((e.at - now).total_seconds() / 60.0),
            "blackout_from": (
                e.at - timedelta(minutes=float(window.get("before_minutes", 0)))
            ).isoformat() if window else None,
            "blackout_to": (
                e.at + timedelta(minutes=float(window.get("after_minutes", 0)))
            ).isoformat() if window else None,
        })
    out.sort(key=lambda x: x["at"])
    return out


def _changes_since(database_url: str, now: datetime,
                   hours: int = 24) -> List[Dict[str, Any]]:
    """昨日から判断が変わったもの。**変わっていないものは出さない。**"""
    since = now - timedelta(hours=hours)
    rows = recent_decisions(database_url, limit=200)
    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            decided = datetime.fromisoformat(str(r["decided_at"]))
        except (TypeError, ValueError):
            continue
        if decided.tzinfo is None:
            decided = decided.replace(tzinfo=timezone.utc)
        if decided < since:
            continue
        out.append({
            "pair": r["pair"], "decided_at": r["decided_at"],
            "signal": r["signal"], "direction": r["direction"],
            "score": r["score"], "strategy": r["strategy"],
            "provider": r["provider"], "reason": r["reason"],
            "outcome": r["outcome"], "r_multiple": r["r_multiple"],
        })
    return out


def build(cfg: TradingConfig, provider_name: str, database_url: str,
          results: Optional[List] = None,
          now: Optional[datetime] = None,
          boards: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """朝の確認を組み立てる。

    ``results`` は ``AnalysisResult`` の一覧（省略可）。渡さなければ
    銘柄の今の状態は空で返す。**ここで分析し直さない。**
    """
    now = now or datetime.now(timezone.utc)
    status = collect_data_status(cfg, provider_name, now=now)

    # --- 今日この道具が使えるか ---
    blockers: List[str] = []
    todo: List[str] = []
    for src in status["sources"]:
        if src["state"] == "STALE" and src["blocks_trading"]:
            blockers.append(f"{src['label']}が古すぎます"
                            f"（{src['age_hours']:.0f} 時間前 / 上限 "
                            f"{src['limit_hours']:.0f} 時間）")
        if src["state"] in ("STALE", "SOON", "MISSING"):
            todo.append(src["refresh"])
    if provider_name == "mock":
        blockers.append("いまは合成データで動いています。実勢の値ではありません")
        todo.append("FX_MARKET_DATA_PROVIDER=csv と FX_CSV_DATA_DIR=./data/real")

    # **閉まっているときに候補が出ないのは異常ではない。**
    # 伝えないと、週末に開いた利用者が「壊れている」と読む。
    open_now = is_market_open(now)

    focus_rows: List[Dict[str, Any]] = []
    for res in results or []:
        tier = tier_of(cfg, res.pair)
        if tier != "focus":
            continue
        focus_rows.append({
            "pair": res.pair,
            "signal": res.signal.value,
            "direction": res.direction.value,
            "score": round(res.score, 1),
            "regime": res.regime.value,
            "strategy": res.strategy,
            "spread_pips": (round(res.spread.spread_pips, 2)
                            if res.spread else None),
            "reason": (res.invalidation_reasons or [None])[0],
        })
    focus_rows.sort(key=lambda r: -r["score"])

    actionable = [r for r in focus_rows
                  if r["signal"] in ("BUY", "SELL")]

    return {
        "generated_at": now.isoformat(),
        "provider": provider_name,
        # **使えないときに「使える」と書かない。**
        "ready": not blockers,
        "blockers": blockers,
        "todo": sorted(set(todo)),
        "data_state": status["state"],
        "market_open": open_now,
        "focus": focus_rows,
        # 方向性ボード。**予測ではない。** 事実と要因と除外の理由。
        "boards": boards or [],
        "actionable": [r["pair"] for r in actionable],
        "events": _upcoming_events(cfg, now),
        "changes": _changes_since(database_url, now),
        "journal": decision_summary(database_url),
        "note": ("今日の見通しは書きません。優位性は測れていません"
                 "（docs/backtesting.md）。出しているのは、いまの場面と"
                 "止めている理由だけです。"),
    }


def as_text(brief: Dict[str, Any]) -> str:
    """端末向けの1枚。**画面が無いときはこれを読む。**"""
    when = datetime.fromisoformat(brief["generated_at"]).astimezone()
    lines: List[str] = []
    lines.append(f"=== FX 朝の確認　{when:%Y-%m-%d %H:%M} ===")
    lines.append("")
    if brief["ready"] and not brief.get("market_open", True):
        lines.append("市場が閉まっています。"
                     "候補が出ないのは正常です（道具の側は使えます）。")
    elif brief["ready"]:
        lines.append("今日は判断支援を受けられます。")
    else:
        lines.append("【今日は判断支援を受けられません】")
        for b in brief["blockers"]:
            lines.append(f"  × {b}")
    # **使える朝でも、手当てが要るものは出す。**
    # 以前は使えないときだけ出していたので、スワップ表が10日古いのに
    # 「今日は使えます」の一言で終わっていた。止まっていないことと、
    # 手当てが要らないことは別。
    if brief["todo"]:
        lines.append("  手当てが要るもの:" if brief["ready"] else "  やること:")
        for t in brief["todo"]:
            lines.append(f"    $ {t}")
    lines.append("")

    if brief.get("boards"):
        from .board import as_text as board_text

        lines.append("--- 方向性ボード（予測ではありません。決めるのはご自身です）---")
        for b in brief["boards"]:
            lines.extend(board_text(b))
            lines.append("")

    lines.append(f"--- 支援する銘柄（{len(brief['focus'])} 件）---")
    if not brief["focus"]:
        lines.append("  対象がありません"
                     "（scripts/measure_pair_value.py で決めます）")
    for r in brief["focus"]:
        head = f"  {r['pair']:<8} {r['signal']:<9} 点数 {r['score']:>5.1f}"
        tail = f"　{r['reason'] or ''}"
        lines.append(head + tail)
    if brief["actionable"]:
        lines.append(f"  → 売買の候補: {', '.join(brief['actionable'])}")
    else:
        lines.append("  → 売買の候補はありません")
    lines.append("")

    lines.append(f"--- これからの指標（{len(brief['events'])} 件）---")
    if not brief["events"]:
        lines.append("  予定表に、これから24時間の重要指標はありません")
    for e in brief["events"][:8]:
        at = datetime.fromisoformat(e["at"]).astimezone()
        lines.append(f"  {at:%m/%d %H:%M} [{e['impact']}] "
                     f"{e['currency'] or '—'} {e['title']}")
    lines.append("")

    lines.append(f"--- 昨日からの判断の変化（{len(brief['changes'])} 件）---")
    for c in brief["changes"][:10]:
        at = datetime.fromisoformat(c["decided_at"]).astimezone()
        mark = "（合成）" if c["provider"] == "mock" else ""
        lines.append(f"  {at:%m/%d %H:%M} {c['pair']:<8} "
                     f"{c['signal']}{mark}")
    if not brief["changes"]:
        lines.append("  変化なし")
    lines.append("")
    lines.append(brief["note"])
    return "\n".join(lines)
