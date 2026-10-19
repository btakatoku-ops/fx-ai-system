# -*- coding: utf-8 -*-
"""今日の FX の判断（2026-10-19、利用者の指定）。**材料を並べるだけでなく、答えを1つ出す。**

利用者の声：「見ていても判断までたどりつかない」。ボードは除外の理由を並べ、Market Radar は
毎日別の銘柄に「買い」を出し、検証の欄は「効く決まりは無い」と言う。どれも正しいが、
結局どうするのかが書いていなかった。

決まった順に確かめ、最初に当てはまった答えを出す（**見送りも正しい判断として出す**）：

1. 市場が閉まっている → 「判断はありません」
2. 取り込みが止まっている（health の⚠：取り込み・朝の処理）→ 「材料が古いので建てない」
3. 建てられない時刻（刻限の近く・過ぎた）→ 「今は建てない」
4. ボードが「そろった」で、利確の目安が計画と同じ計算で出せる銘柄がある
   - Market Radar（24時間先）が逆を向いていたら、その銘柄は見送り
   - 残れば「候補あり」（計画の数字を添える）
5. どれも無ければ「見送り（取引しない）」と、多かった理由・次に見る時刻

**予測ではない。** 候補が出ても、勝てる根拠ではない（検証で費用を超える決まりは見つかっていない）。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Optional

from . import day_trade, sessions
from .board import fx_day_start

JST = timezone(timedelta(hours=9))
LOOK_AFTER = timedelta(minutes=15)             # 時間帯の始まりから15分後（ページが作り直された後）


def next_look(now: datetime, board_cfg: Optional[Dict] = None) -> datetime:
    """次に見る時刻：朝 8:45／ロンドンの始まり＋15分／NY の始まり＋15分。過ぎていれば次の平日 8:45。"""
    j = now.astimezone(JST)
    for k in range(0, 8):
        d = (j + timedelta(days=k)).date()
        if d.weekday() >= 5:
            continue
        morning = datetime.combine(d, time(8, 45), JST)
        day = fx_day_start(morning)
        times = [morning]
        for s in (sessions.LONDON, sessions.NEWYORK):
            at = sessions.open_at(s, day, board_cfg)
            if at is not None:
                times.append(at + LOOK_AFTER)
        for t in sorted(times):
            if t > now:
                return t
    return now + timedelta(days=1)


def _label(t: datetime, now: datetime) -> str:
    j, n = t.astimezone(JST), now.astimezone(JST)
    day = "今日" if j.date() == n.date() else "明日" if j.date() == n.date() + timedelta(days=1) else f"{j:%m/%d}"
    return f"{day} {j:%H:%M}"


def _short(reason: str) -> str:
    r = reason.replace("**", "")
    for key, label in (("向きが逆", "上位足と下位足の向きが食い違い"),
                       ("ATR の", "スプレッドが値動きに比べて大きい"),
                       ("介入警戒帯", "介入警戒帯の中"),
                       ("普段の", "今日はもう大きく動いた"),
                       ("古すぎ", "足が古い"),
                       ("指標", "重要指標の前後"),
                       ("テクニカルとファンダ", "テクニカルとファンダが逆"),
                       ("刻限", "刻限が近い・過ぎた")):
        if key in r:
            return label
    return r[:24]


def decide(boards: List[Dict], extras: Dict, now: datetime, cfg) -> Dict:
    mr = (extras or {}).get("mr") or {}
    views = {} if mr.get("stale") else (mr.get("views") or {})   # 古い Market Radar は使わない
    look = next_look(now, getattr(cfg, "board", None))
    base = {"next_look": look.isoformat(), "next_look_label": _label(look, now),
            "candidates": [], "skipped": [], "reasons": []}

    if boards and not any((b.get("facts") or {}).get("market_open") for b in boards):
        return {**base, "verdict": "closed", "title": "市場が閉まっています（週末・休場）",
                "lead": "判断はありません。"}

    bad = [x for x in (extras or {}).get("health") or []
           if x.get("state") == "warn" and x.get("key") in ("refresh", "morning")]
    if bad:
        return {**base, "verdict": "stale", "title": "材料が古いので、建てません",
                "lead": "・".join(f'{x["label"]}：{x["detail"]}' for x in bad)}

    ok, why = day_trade.can_open(cfg.filters, now)
    if not ok:
        return {**base, "verdict": "wait", "title": "今は建てません",
                "lead": (why or "建てられない時刻です").replace("**", "")}

    for b in boards:
        state = (b.get("verdict") or {}).get("state")
        tp = b.get("tp_preview") or {}
        if state not in ("aligned_up", "aligned_down"):
            continue
        lean = "up" if state == "aligned_up" else "down"
        if tp.get("status") != "PLAN_OK":
            base["skipped"].append({"pair": b["pair"], "lean": lean,
                                    "why": "計画の条件を満たさない：" + "・".join(
                                        str(x).replace("**", "") for x in (tp.get("stops") or [])[:1])})
            continue
        v = views.get(b["pair"])
        mr_txt, mr_ok = "Market Radar は予測なし", True
        if v:
            same = v["lean"] == lean
            mr_txt = (f'Market Radar・{mr.get("horizon")}も{"同じ向き" if same else "逆の向き"}'
                      f'（確信度 {v["confidence"] * 100:.0f}%{"・シグナルあり" if v["signal"] else ""}）')
            mr_ok = same or not v["signal"]          # 逆でも、Market Radar 側がシグナルでなければ止めない
        item = {"pair": b["pair"], "lean": lean, "mr": mr_txt,
                "entry": tp.get("entry"), "stop": tp.get("stop"),
                "tp1": (tp.get("targets") or [{}])[0].get("price"), "digits": b.get("digits", 3)}
        (base["candidates"] if mr_ok else base["skipped"]).append(
            item if mr_ok else {"pair": b["pair"], "lean": lean, "why": mr_txt + " → 見送り"})

    if base["candidates"]:
        names = "、".join(f'{c["pair"]} {"買い" if c["lean"] == "up" else "売り"}' for c in base["candidates"])
        return {**base, "verdict": "candidate", "title": f"候補あり：{names}",
                "lead": "計画ボタンで MT4 の売値・買値を入れて、最終の数字を確かめてください。"}

    cnt = Counter()
    for b in boards:                                   # 1銘柄につき同じ理由は1回（M15 と M5 の「古い」など）
        for k in {_short(str(r)) for r in (b.get("verdict") or {}).get("exclude") or []}:
            cnt[k] += 1
    reasons = [f"{k}（{n}銘柄）" for k, n in cnt.most_common(2)]
    if not any((b.get("verdict") or {}).get("state") in ("aligned_up", "aligned_down") for b in boards):
        reasons.append("材料がそろった銘柄がない")
    return {**base, "verdict": "skip", "title": "今日は見送り（取引しない）が判断です",
            "lead": "そろって計画が出せる銘柄がありません。", "reasons": reasons}
