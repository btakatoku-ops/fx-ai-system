# -*- coding: utf-8 -*-
"""公開ページ（ダッシュボード）の各欄に載せる材料を集める。I/O はここだけ。

どの欄も**実際のデータ**だけを出す。飾りの図は作らない。

- chart … その取引日の M15 の値動き、近い水準、**この先の値動きの幅**（向きは示さない）。
  幅は ``config/forecast.json``（scripts/measure_forecast.py が実データで測り、別の期間で
  約8割を覆うことを確かめた ATR の倍率）から引く。**測った範囲（32本＝8時間）より先は描かない。**
  （2026-10-09 まで、始値からの √時間 の近似を描いていた。測っていない形だったので置き換えた）
- tape  … 残したボード（朝・時間帯）と、その後の値動き（採点済みなら）。
- score … 採点の件数と、そろった日・ブリーフの向きが当たった件数。**30件に届くまで
  割合は名乗らない**（board_report と同じ決まり）。
- events … これからの重要指標（支援する銘柄の通貨だけ）。
- feed  … 銘柄ごとの最後の足と、その古さ。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from . import board_log, day_trade, news
from statistics import median

from .indicator_engine import atr as atr_series
from .indicator_engine import ema, macd, rsi
from .board import fx_day_start
from .config import TradingConfig

log = logging.getLogger(__name__)

MIN_SAMPLES = 30


def forecast_band(cfg: TradingConfig, bars) -> Optional[Dict]:
    """最後の足から先の「約8割が収まった幅」。測っていなければ None（推測で描かない）。"""
    fc = getattr(cfg, "forecast", None) or {}
    horizons = fc.get("horizons") or {}
    if not horizons or fc.get("timeframe") != "M15" or len(bars) < 30:
        return None
    period = int(fc.get("atr_period", 14))
    a = atr_series([float(b.high) for b in bars], [float(b.low) for b in bars],
                   [float(b.close) for b in bars], period)
    last_atr = a[-1] if a else None
    if not last_atr or last_atr <= 0:
        return None
    close = float(bars[-1].close)
    levels = []
    for key in sorted(horizons, key=lambda k: int(k)):
        mult = horizons[key].get("atr_multiple")
        if mult is None:
            continue
        w = float(mult) * float(last_atr)
        levels.append({"bars": int(key), "low": close - w, "high": close + w,
                       "coverage": horizons[key].get("measured_coverage")})
    if not levels:
        return None
    covs = [lv["coverage"] for lv in levels if lv["coverage"] is not None]
    return {"close": close, "atr": float(last_atr), "levels": levels,
            "quantile": fc.get("quantile"),
            "coverage_min": min(covs) if covs else None,
            "coverage_max": max(covs) if covs else None,
            "measured_at": fc.get("measured_at")}


FIB_RETRACE = (0.382, 0.5, 0.618)
FIB_EXTEND = (1.272, 1.618)


def fib(bars, n: int = 96) -> Optional[Dict]:
    """直近 n 本（M15 で24時間）の高値・安値から引くフィボナッチ（利用者の指定、2026-10-11）。

    安値のあとに高値が来ていれば上げの脚、逆なら下げの脚。押し・戻し（38.2/50/61.8%）は
    脚の中、伸び（127.2/161.8%）は脚の先。**慣習の線で、このアプリでは当たりやすさを測って
    いない**（画面にもそう書く）。
    """
    win = list(bars)[-n:]
    if len(win) < 8:
        return None
    hi_i = max(range(len(win)), key=lambda i: float(win[i].high))
    lo_i = min(range(len(win)), key=lambda i: float(win[i].low))
    hi, lo = float(win[hi_i].high), float(win[lo_i].low)
    rng = hi - lo
    if rng <= 0:
        return None
    up = lo_i < hi_i
    levels = []
    for r in FIB_RETRACE:
        levels.append({"ratio": r, "kind": "retrace", "price": hi - r * rng if up else lo + r * rng})
    for e in FIB_EXTEND:
        levels.append({"ratio": e, "kind": "extend", "price": lo + e * rng if up else hi - e * rng})
    return {"direction": "up" if up else "down", "high": hi, "low": lo,
            "high_at": win[hi_i].timestamp.isoformat(), "low_at": win[lo_i].timestamp.isoformat(),
            "bars": len(win), "levels": levels}


def chart(cfg: TradingConfig, provider, symbol: str, facts: Dict) -> Optional[Dict]:
    """その取引日（閉場中なら直近の取引日）の M15 の動き。"""
    try:
        # 20日ぶん（値動きの大きさを普段と比べるため）。チャート・帯・フィボは直近200本だけ使う
        long_bars = provider.get_candles(symbol, "M15", limit=VOL_DAYS * 96 + 200).candles
    except Exception as exc:                     # noqa: BLE001
        log.warning("チャートの足を取れません %s: %s", symbol, exc)
        return None
    if not long_bars:
        return None
    bars = long_bars[-200:]
    start = fx_day_start(bars[-1].timestamp)
    day = [b for b in bars if b.timestamp >= start]
    # 週末は、閉まる直前に始まった取引日の足が数本だけ残る。それでは何も見えないので、
    # **足が2時間ぶんに満たなければ、ひとつ前の取引日を描く**（文字の「直近の取引日」とそろえる）
    if len(day) < 8:
        prev = fx_day_start(start - timedelta(minutes=1))
        older = [b for b in bars if prev <= b.timestamp < start]
        if len(older) >= 8:
            start, day = prev, older
            bars = [b for b in bars if b.timestamp < fx_day_start(bars[-1].timestamp)]
    if not day:
        return None
    deadline = day_trade.exit_deadline(cfg.filters, start + timedelta(minutes=1))
    mc = (getattr(cfg, "board", None) or {}).get("momentum") or {}
    return {
        "day_start": start.isoformat(),
        "deadline": deadline.isoformat() if deadline else None,
        "open": float(day[0].open),
        "adr": facts.get("adr"),
        "bars": [[int((b.timestamp - start).total_seconds() // 60),
                  float(b.high), float(b.low), float(b.close), float(b.open)] for b in day],
        "last_bar_at": day[-1].timestamp.isoformat(),
        "band": forecast_band(cfg, bars),
        "fib": fib(bars),
        "ema": h1_ema(provider, symbol, start),
        "osc": oscillators(bars, start),
        "rsi_up": float(mc.get("rsi_up", 55)), "rsi_down": float(mc.get("rsi_down", 45)),
        "vol": vol_state(long_bars),
    }


VOL_DAYS = 20
VOL_LOW, VOL_HIGH = 0.8, 1.2


def h1_ema(provider, symbol: str, start) -> List[List[float]]:
    """H1 の EMA20・EMA50（ボードの「トレンド」の根拠の片方）。点は足が**確定した時刻**に置く。"""
    try:
        h1 = provider.get_candles(symbol, "H1", limit=24 * 15).candles
    except Exception:                            # noqa: BLE001
        return []
    closes = [float(b.close) for b in h1]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    out = []
    for b, a, c in zip(h1, e20, e50):
        done = b.timestamp + timedelta(hours=1)
        if done >= start and a is not None and c is not None:
            out.append([int((done - start).total_seconds() // 60), float(a), float(c)])
    return out


def oscillators(bars, start) -> List[List[Optional[float]]]:
    """M15 の RSI14 と MACD ヒストグラム（ボードの「モメンタム」の根拠）。その日の足だけ返す。"""
    closes = [float(b.close) for b in bars]
    r = rsi(closes, 14)
    _, _, h = macd(closes)
    return [[int((b.timestamp - start).total_seconds() // 60), r[i], h[i]]
            for i, b in enumerate(bars) if b.timestamp >= start]


def vol_state(bars) -> Optional[Dict]:
    """いまの M15 の ATR が、直近20日の中央値の何倍か。

    **向きではなく費用の重さの目安。** 再生の実測（2026-10-18、1,876時刻）で、1.2倍以上の
    場面は向きを問わず平均 −0.10R、普段どおりは −0.23R だった（費用の割合が小さくなるため）。"""
    a = atr_series([float(b.high) for b in bars], [float(b.low) for b in bars],
                   [float(b.close) for b in bars], 14)
    vals = [v for v in a[-VOL_DAYS * 96:] if v]
    if len(vals) < 96 * 5 or not a or not a[-1]:
        return None
    med = median(vals)
    ratio = a[-1] / med if med else None
    if ratio is None:
        return None
    kind = "high" if ratio >= VOL_HIGH else "low" if ratio < VOL_LOW else "normal"
    return {"ratio": round(ratio, 3), "atr": a[-1], "median": med, "kind": kind,
            "days": round(len(vals) / 96, 1)}


def _move(r: Dict, key: str = "move_adr") -> Optional[float]:
    """向きをそろえる前の値動き（上が正、ADR 単位）。"""
    m = r.get(key)
    if m is None:
        return None
    return -m if r.get("lean") == "down" else m


def tape(database_url: str, limit: int = 24) -> List[Dict]:
    rows = board_log.recent(database_url, limit=limit)
    out = []
    for r in rows:
        out.append({
            "recorded_at": r.get("recorded_at"), "session": r.get("session") or "tokyo",
            "pair": r.get("pair"), "state": r.get("state"), "lean": r.get("lean"),
            "brief_lean": r.get("brief_lean"), "price": r.get("price"),
            "scored": bool(r.get("scored_at")), "move": _move(r),
            "seg_move": _move(r, "seg_move_adr"),
        })
    out.sort(key=lambda r: r["recorded_at"] or "", reverse=True)
    return out


def score(database_url: str) -> Dict:
    rows = board_log.recent(database_url, limit=5000)
    done = [r for r in rows if r.get("scored_at")]

    def hits(rs, lean_key):
        n = h = 0
        for r in rs:
            lean, mv = r.get(lean_key), _move(r)
            if lean not in ("up", "down") or mv is None or mv == 0:
                continue
            n += 1
            h += 1 if (mv > 0) == (lean == "up") else 0
        return {"n": n, "hits": h, "rate": (h / n if n >= MIN_SAMPLES else None)}

    excluded = [r for r in done if r.get("state") == "excluded"]
    swing = [abs(r.get("best_adr") or 0) + abs(r.get("worst_adr") or 0) for r in excluded]
    return {
        "recorded": len(rows), "scored": len(done),
        "aligned": hits(done, "lean"), "brief": hits(done, "brief_lean"),
        "excluded": len(excluded),
        "excluded_swing": (sum(swing) / len(swing)) if swing else None,
        "min_samples": MIN_SAMPLES,
    }


def events(symbols: Sequence[str], now: datetime, hours: int = 36, n: int = 8) -> List[Dict]:
    cur = {s[:3] for s in symbols} | {s[3:] for s in symbols}
    _, _, evs = news.load_calendar()
    out = []
    for e in evs:
        if e.at < now or e.at > now + timedelta(hours=hours):
            continue
        if e.currency and e.currency not in cur:
            continue
        if e.impact not in ("High", "Medium"):
            continue
        out.append({"at": e.at.isoformat(), "title": e.title,
                    "currency": e.currency, "impact": e.impact})
        if len(out) >= n:
            break
    return out


DXY_BARS = 4                     # 直近4時間（signal_lab.dxy_4h と同じ）
DXY_STALE_HOURS = 3              # 取引時間中は毎時取り込むので、3時間より古ければ「古い」と書く


def dxy_state(now: datetime, drivers_dir=None, replay_dir=None) -> Optional[Dict]:
    """ドル指数（DXY）の直近4時間の向き。**参考・測定中**（判定には入れない）。

    再生の試験（scripts/signal_lab.py、毎週土曜）で、試した候補のうち唯一「向きを問わず」を
    上回った（2026-10-18: 736件・向きの的中56%）。数字は最新の試験結果から読む（毎週変わる）。
    確定した1時間足だけを使う（足の時刻＋60分で確定）。"""
    from pathlib import Path as _P

    from . import signal_lab as S
    from .config import PROJECT_ROOT

    s = S.load_driver("DXY", _P(drivers_dir) if drivers_dir else S.DRIVERS_DIR)
    if s is None:
        return None
    n = s.upto(now)
    if n <= DXY_BARS:
        return None
    close, before = s.c[n - 1], s.c[n - 1 - DXY_BARS]
    last_at = s.close_ts[n - 1]
    out: Dict = {"change": close - before, "pct": (close - before) / before * 100 if before else None,
                 "close": close, "last_at": last_at.isoformat(),
                 "stale": (now - last_at) > timedelta(hours=DXY_STALE_HOURS),
                 "spark": [s.c[i] for i in range(max(0, n - 24), n)], "stats": None}
    rdir = _P(replay_dir) if replay_dir else PROJECT_ROOT / "logs" / "replay"
    files = sorted(rdir.glob("20??-??-??-signals.json")) if rdir.exists() else []
    if files:
        try:
            import json as _json
            d = _json.loads(files[-1].read_text(encoding="utf-8")).get("dxy_4h") or {}
            if d.get("signal", {}).get("n"):
                out["stats"] = {"n": d["signal"]["n"], "win_rate": d["signal"]["win_rate"],
                                "avg_r": d["signal"]["avg_r"], "base_r": d["same_times"]["avg_r"],
                                "dir_rate": d["direction"]["rate"], "dir_lo": d["direction"]["lo"],
                                "dir_hi": d["direction"]["hi"], "both_halves": d.get("both_halves_better"),
                                "measured": files[-1].name[:10]}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    # **約2年の検証（scripts/long_lab.py）の結果も並べる。** 8週間の結果だけでは、たまたまの
    # 相場（ドル高の時期など）に引きずられる。2026-10-18 の検証では、2年では効果が無かった。
    longs = sorted(rdir.glob("long-20??-??-??.json")) if rdir.exists() else []
    if longs:
        try:
            import json as _json
            d = _json.loads(longs[-1].read_text(encoding="utf-8"))
            e = d["results"]["dxy"]["1.5"]
            out["long"] = {"first": d["first"], "last": d["last"], "split": d["split"],
                           "a_n": e["前半"]["n"], "a_dir": e["前半"]["dir"], "a_edge": e["前半"]["edge"],
                           "b_n": e["後半"]["n"], "b_dir": e["後半"]["dir"], "b_edge": e["後半"]["edge"]}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return out


def verify_state(replay_dir=None) -> Optional[Dict]:
    """測った結果のまとめ（毎週土曜の検証の最新）。**飾らずに、そのまま出す。**

    - 8週の再生（scripts/replay_boards.py）… ボードが「そろった」場面と、向きを問わずの比較
    - 約2年（scripts/long_lab.py）… 前半で見つけ・後半で確かめた結果。費用を超える決まりがあるか
    - 数日持ち（scripts/swing_lab.py）… 費用の重さが日計りとどれだけ違うか"""
    import json as _json
    from pathlib import Path as _P

    from .config import PROJECT_ROOT

    rdir = _P(replay_dir) if replay_dir else PROJECT_ROOT / "logs" / "replay"
    if not rdir.exists():
        return None

    def latest(pattern):
        files = sorted(rdir.glob(pattern))
        if not files:
            return None, None
        try:
            return files[-1], _json.loads(files[-1].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return files[-1], None

    out: Dict = {}
    f, d = latest("20??-??-??.json")
    if d and d.get("summary"):
        s = d["summary"]
        g = s["groups"].get("ボードが「そろった」（除外なし）", {}).get("tp1", {})
        out["replay"] = {"date": f.name[:10], "boards": s.get("boards"), "n": g.get("n"),
                         "avg_r": g.get("avg_r"), "base_r": s["baseline"]["tp1"].get("avg_r"),
                         "excluded_share": s.get("excluded_share")}
    f, d = latest("long-20??-??-??.json")
    if d:
        res = d.get("results") or {}
        lean = (res.get("lean") or {}).get("1.5") or {}
        winners = [k for k, v in res.items() for m, e in v.items()
                   if (e["前半"].get("edge") or -1) > 0 and (e["後半"].get("edge") or -1) > 0
                   and (e["後半"].get("r_lo") or -1) > 0]
        out["long"] = {"date": f.name[5:15], "first": d.get("first"), "last": d.get("last"),
                       "lean_a": (lean.get("前半") or {}).get("avg_r"), "lean_b": (lean.get("後半") or {}).get("avg_r"),
                       "base_a": (lean.get("前半") or {}).get("base_r"), "base_b": (lean.get("後半") or {}).get("base_r"),
                       "winners": sorted(set(winners))}
    f, d = latest("swing-20??-??-??.json")
    if d:
        b = d.get("基準：向きを問わず|5|None") or {}
        out["swing"] = {"date": f.name[6:16], "base_a": (b.get("前半") or {}).get("avg_r"),
                        "base_b": (b.get("後半") or {}).get("avg_r")}
    return out or None


def collect(cfg: TradingConfig, provider, boards: List[Dict], now: datetime,
            database_url: Optional[str]) -> Dict:
    """各欄の材料をまとめて集める。1つ失敗しても他は出す。"""
    out: Dict = {"charts": {}, "tape": [], "score": None, "events": []}
    try:
        out["dxy"] = dxy_state(now)
    except Exception as exc:                     # noqa: BLE001
        log.warning("ドル指数を読めません: %s", exc)
    try:
        from . import health
        out["health"] = health.check(now)
        out["verify"] = verify_state()
    except Exception as exc:                     # noqa: BLE001
        log.warning("健康診断ができません: %s", exc)
    for b in boards:
        try:
            ch = chart(cfg, provider, b["pair"], b.get("facts") or {})
            if ch:
                out["charts"][b["pair"]] = ch
        except Exception as exc:                 # noqa: BLE001
            log.warning("チャートを作れません %s: %s", b.get("pair"), exc)
    if database_url:
        try:
            out["tape"] = tape(database_url)
            out["score"] = score(database_url)
        except Exception as exc:                 # noqa: BLE001
            log.warning("記録を読めません: %s", exc)
    try:
        out["events"] = events([b["pair"] for b in boards], now)
    except Exception as exc:                     # noqa: BLE001
        log.warning("指標を読めません: %s", exc)
    return out
