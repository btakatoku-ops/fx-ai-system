# -*- coding: utf-8 -*-
"""Yahoo Finance から実勢の為替データを取り寄せ、CSV に落とす。

**これはアプリの一部ではない。** 取り込みだけを行う道具で、書き出した CSV を
`csv` 供給元が読む。アプリ側が実行中に外部へ繋ぎにいく作りにはしない
（供給元の抽象を保つため。mt4 / external_api / future_broker は未実装のまま）。

守っていること。

1. **正体を名乗る。** ブラウザのふりをしない。素直な User-Agent で通る。
2. **進行中の足を落とす。** Yahoo は「まだ終わっていない最後の1本」を
   混ぜて返す。それを確定した足として扱うと、指標が「直前に大きく動いた」
   と誤認する。market-radar で的中率が29.6%まで落ちた原因がこれだった。
3. **欠けた足は捨てる。埋めない。** 補間すると、壊れたデータで計算した
   結果が正常な結果と見分けられなくなる。
4. **H4 は自分で作る。** Yahoo に4時間足は無いので、1時間足を4本ずつ
   まとめる。境目は 00:00 UTC 起点にそろえる。

使い方::

    python scripts/fetch_yahoo.py --out data/real
    python scripts/fetch_yahoo.py --pairs USDJPY,EURUSD --out data/real
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# ブラウザのふりはしない。何者かを正直に名乗る。
UA = "fx-ai-system/0.1 (personal research; contact via repository owner)"
BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"

# Yahoo が intraday で遡れる範囲。ここを超えて頼んでも返ってこない。
# 5m / 15m は60日まで、1h は730日まで。4h は無いので1hから作る。
FETCH_PLAN: List[Tuple[str, str, str]] = [
    # (こちらの時間足, Yahoo の interval, range)
    ("M5", "5m", "60d"),
    ("M15", "15m", "60d"),
    ("H1", "1h", "180d"),   # 730日も要らない。M15の60日＋H4の立ち上がりで足りる
]
RESAMPLE = {"H4": ("H1", 4)}     # H4 は H1 を4本ずつ

# 取引時間中の取り直し（--recent）。**直近だけ取って、いまのファイルに継ぎ足す。**
# 5分ごとに60日ぶんを取り直すと、相手先にも回線にも重い（1回で約1MB×銘柄）。
RECENT_PLAN: List[Tuple[str, str, str]] = [
    ("M5", "5m", "1d"),
    ("M15", "15m", "5d"),
    ("H1", "1h", "5d"),
]


def read_csv(path: Path) -> List[Dict]:
    """書き出した CSV を読み戻す（--recent で継ぎ足すため）。無ければ空。"""
    if not path.exists():
        return []
    out: List[Dict] = []
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                out.append({
                    "timestamp": datetime.fromisoformat(r["timestamp"]),
                    "open": float(r["open"]), "high": float(r["high"]),
                    "low": float(r["low"]), "close": float(r["close"]),
                    "volume": float(r["volume"] or 0),
                })
            except (KeyError, ValueError):
                continue                          # 壊れた行は捨てる（推測で直さない）
    return out


def merge_rows(old: List[Dict], new: List[Dict]) -> List[Dict]:
    """継ぎ足す。**同じ時刻は新しく取った方を採る**（確定し直した値）。"""
    merged = {r["timestamp"]: r for r in old}
    for r in new:
        merged[r["timestamp"]] = r
    return [merged[k] for k in sorted(merged)]


def yahoo_symbol(pair: str) -> str:
    """USDJPY -> USDJPY=X。Yahoo の為替はこの形。"""
    return f"{pair.upper()}=X"


def get_json(url: str, timeout: int = 30, retries: int = 4) -> Optional[Dict]:
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 404:
                return None                      # 銘柄が無い。再試行しない
            time.sleep(2.0 * (attempt + 1))      # 429 などは間を空けて
        except Exception as exc:                 # 接続断・タイムアウト
            last = exc
            time.sleep(2.0 * (attempt + 1))
    print(f"    取得できません: {last}", file=sys.stderr)
    return None


def parse_chart(payload: Dict) -> List[Dict]:
    """Yahoo の返事を足の並びに直す。欠けた足は**捨てる**。"""
    try:
        res = payload["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        return []

    out: List[Dict] = []
    for i in range(len(ts)):
        o, h, low, c = (q["open"][i], q["high"][i], q["low"][i], q["close"][i])
        if None in (o, h, low, c):
            continue                     # 欠損。埋めない
        if not (h >= max(o, c) and low <= min(o, c) and low > 0):
            continue                     # 辻褄が合わない足。直さず捨てる
        vol = (q.get("volume") or [None] * len(ts))[i] or 0
        out.append({
            "timestamp": datetime.fromtimestamp(ts[i], timezone.utc),
            "open": float(o), "high": float(h),
            "low": float(low), "close": float(c), "volume": float(vol),
        })
    out.sort(key=lambda r: r["timestamp"])

    # 同じ時刻が二重に来ることがある。後から来たほうを残す。
    dedup: Dict[datetime, Dict] = {}
    for r in out:
        dedup[r["timestamp"]] = r
    return [dedup[k] for k in sorted(dedup)]


def drop_misaligned(rows: List[Dict], minutes: int) -> Tuple[List[Dict], int]:
    """時刻が足の区切りに揃っていない行を落とす。

    **Yahoo は最後に「いまの気配」を足のような顔で付けてくる。** 実際に
    見た例: 1時間足の末尾に ``22:59:00`` や ``23:43:40``。これは足ではなく
    その瞬間の値段なので、高値・安値・始値の意味を持たない。

    未確定の足を落とす処理（``drop_incomplete``）では拾えない。週末に
    取り込むと、金曜 22:59 は「もう終わった時刻」なので確定済みに見える。
    実際に全78ファイルに計60行紛れ込んでいて、朝のボードでは「前日の
    高値と安値が同じ値」という壊れ方で出た。
    """
    out: List[Dict] = []
    dropped = 0
    for r in rows:
        t = r["timestamp"]
        if t.second or t.microsecond or (t.hour * 60 + t.minute) % minutes:
            dropped += 1
            continue
        out.append(r)
    return out, dropped


def drop_incomplete(rows: List[Dict], minutes: int,
                    now: Optional[datetime] = None) -> Tuple[List[Dict], int]:
    """確定していない足を、末尾から**全部**落とす。

    Yahoo は進行中の足を混ぜて返す。その足の終値は「いまの値段」でしか
    なく、高値・安値も途中までの値。**確定した足として扱ってはいけない。**

    判定は時刻で行う。**足 t が確定するのは t + 期間 のとき。** 以前は
    「間隔が不揃い」「期間の半分も経っていない」という目安で1本だけ
    落としていたが、それでは足りなかった。

    実際に起きたこと（2026-09-15）。取り寄せた直後なのに、engine が
    H1・M15・M5 のすべてで「最後の足が未確定」と言って全銘柄を見送った。
    21:03 時点で M15 の最後が 21:00（確定は 21:15）、H1 の最後が 21:00
    （確定は 22:00）。**1本落としても、その手前がまだ確定していない。**
    取り寄せたその足で一度も判断できない、という状態になっていた。

    落とす方向は安全側。時計がずれていて確定済みの足を落としても、
    判断できる材料が1本減るだけで済む。逆は、未確定の足を確定として
    扱うことになる。
    """
    now = now or datetime.now(timezone.utc)
    period = timedelta(minutes=minutes)
    out = list(rows)
    dropped = 0
    while out and out[-1]["timestamp"] + period > now:
        out.pop()
        dropped += 1
    return out, dropped


def resample(rows: List[Dict], factor: int, src_minutes: int) -> List[Dict]:
    """下位足をまとめて上位足にする。

    境目は 00:00 UTC 起点でそろえる。**そろえないと、取り寄せた時刻によって
    足の切れ目が変わり、同じ期間なのに別の結果が出る。**
    足が欠けている区間は、揃わないので作らない。
    """
    step = src_minutes * factor
    buckets: Dict[datetime, List[Dict]] = {}
    for r in rows:
        t = r["timestamp"]
        minute_of_day = t.hour * 60 + t.minute
        start = t.replace(hour=0, minute=0, second=0, microsecond=0) + \
            timedelta(minutes=(minute_of_day // step) * step)
        buckets.setdefault(start, []).append(r)

    out: List[Dict] = []
    for start in sorted(buckets):
        g = sorted(buckets[start], key=lambda r: r["timestamp"])
        if len(g) < factor:
            continue                 # 揃っていない区間は作らない
        out.append({
            "timestamp": start,
            "open": g[0]["open"],
            "high": max(x["high"] for x in g),
            "low": min(x["low"] for x in g),
            "close": g[-1]["close"],
            "volume": sum(x["volume"] for x in g),
        })
    return out


def fill_gaps(coarse: List[Dict], fine: List[Dict], factor: int,
              fine_minutes: int) -> Tuple[List[Dict], int]:
    """細かい足からまとめ直して、粗い足の穴を埋める。

    **Yahoo の1時間足には穴が開く。** 実際に見た例（2026-09-23 23:43 UTC）::

        ... 17:00, 18:00, 19:00, 23:00, 23:43:40

    20:00〜22:00 が丸ごと無い。最後の2本は未確定なので落とすので、
    残る最新は 19:00。**取り込んだ直後なのに4.7時間前**になり、鮮度の
    条件で全銘柄が止まる。15分足のほうは 23:15 まで来ているのに。

    そこで、15分足が届いている範囲は15分足からまとめ直して使い、
    それより前は Yahoo の1時間足をそのまま使う。

    **作り話はしない。** まとめられるのは、その1時間ぶんの15分足が
    4本そろっている区間だけ（``resample`` が揃わない区間を作らない）。
    そろっていなければ、粗いほうの足が残るか、どちらも無いままになる。
    """
    if not fine:
        return coarse, 0
    rebuilt = resample(fine, factor, fine_minutes)
    if not rebuilt:
        return coarse, 0

    merged: Dict[datetime, Dict] = {r["timestamp"]: r for r in coarse}
    # 15分足が届いている範囲だけ差し替える。**その外側は触らない。**
    first_fine = fine[0]["timestamp"]
    added = 0
    for row in rebuilt:
        if row["timestamp"] < first_fine:
            continue
        if row["timestamp"] not in merged:
            added += 1
        merged[row["timestamp"]] = row
    return [merged[k] for k in sorted(merged)], added


def write_csv(path: Path, rows: List[Dict]) -> None:
    """一時ファイルに書いてから差し替える。

    **書いている途中のファイルを画面に読ませない。** 取引時間中は1時間ごとに
    取り込み直すので、画面が読む瞬間と重なりうる。Windows では読んでいる
    最中の差し替えが失敗することがあるので、少し待って何度か試す。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for r in rows:
            w.writerow([
                r["timestamp"].isoformat(),
                f"{r['open']:.6f}", f"{r['high']:.6f}",
                f"{r['low']:.6f}", f"{r['close']:.6f}", f"{r['volume']:.0f}",
            ])
    for attempt in range(10):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(0.3 * (attempt + 1))
    tmp.replace(path)                           # 最後は例外をそのまま出す


def fetch_pair(pair: str, out_dir: Path, pause: float,
               recent: bool = False) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    sym = urllib.parse.quote(yahoo_symbol(pair), safe="")
    by_tf: Dict[str, List[Dict]] = {}

    for tf, interval, rng in (RECENT_PLAN if recent else FETCH_PLAN):
        url = f"{BASE}{sym}?interval={interval}&range={rng}&includePrePost=false"
        payload = get_json(url)
        time.sleep(pause)                       # 相手先に負担をかけない
        if payload is None:
            print(f"  {pair} {tf}: 取得失敗")
            continue
        rows = parse_chart(payload)
        minutes = {"M5": 5, "M15": 15, "H1": 60}[tf]
        rows, misaligned = drop_misaligned(rows, minutes)
        rows, dropped = drop_incomplete(rows, minutes)
        dropped += misaligned
        if not rows:
            print(f"  {pair} {tf}: 足が0本")
            continue
        if recent:
            old = read_csv(out_dir / f"{pair}_{tf}.csv")
            if not old:
                print(f"  {pair} {tf}: 継ぎ足す元のファイルが無いので書きません（先に全体を取ってください）")
                continue
            rows = merge_rows(old, rows)
        by_tf[tf] = rows
        counts[tf] = len(rows)
        mark = f"（未確定の足を{dropped}本落とした）" if dropped else ""
        print(f"  {pair} {tf}: {len(rows)}本 "
              f"{rows[0]['timestamp'].date()}〜{rows[-1]['timestamp'].date()} {mark}")

    # **1時間足の穴を15分足で埋める。** Yahoo の1時間足は数時間ぶんまとめて
    # 欠けることがあり、そのままだと取り込んだ直後に「古すぎ」で止まる。
    if "H1" in by_tf and "M15" in by_tf:
        filled, added = fill_gaps(by_tf["H1"], by_tf["M15"], 4, 15)
        if added:
            print(f"  {pair} H1: 15分足から {added}本 埋めた "
                  f"（最後 {filled[-1]['timestamp'].isoformat()}）")
        by_tf["H1"] = filled
        counts["H1"] = len(filled)

    for tf in ("M5", "M15", "H1"):
        if tf in by_tf:
            write_csv(out_dir / f"{pair}_{tf}.csv", by_tf[tf])

    for tf, (src, factor) in RESAMPLE.items():
        if src not in by_tf:
            continue
        rows = resample(by_tf[src], factor, {"H1": 60}[src])
        if not rows:
            continue
        write_csv(out_dir / f"{pair}_{tf}.csv", rows)
        counts[tf] = len(rows)
        print(f"  {pair} {tf}: {len(rows)}本（{src} を{factor}本ずつまとめた）")
    return counts


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    from app.config import get_trading_config

    cfg = get_trading_config()
    ap = argparse.ArgumentParser(
        description="Yahoo Finance から実勢の為替データを取り寄せて CSV にする")
    ap.add_argument("--pairs", default="", help="カンマ区切り。省略すると全26銘柄")
    ap.add_argument("--out", default="data/real", help="書き出し先")
    ap.add_argument("--pause", type=float, default=1.2,
                    help="1回の取得ごとに空ける秒数")
    ap.add_argument("--recent", action="store_true",
                    help="直近だけ取って、いまのファイルに継ぎ足す（取引時間中の取り直し用）")
    a = ap.parse_args(argv)

    pairs = ([p.strip().upper() for p in a.pairs.split(",") if p.strip()]
             or [p.symbol for p in cfg.enabled_pairs()])
    out_dir = Path(a.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir

    print(f"取り寄せ先: Yahoo Finance / 書き出し: {out_dir}")
    print(f"銘柄 {len(pairs)} 件\n")

    ok, ng = [], []
    for i, pair in enumerate(pairs, 1):
        print(f"[{i}/{len(pairs)}] {pair}")
        counts = fetch_pair(pair, out_dir, a.pause, recent=a.recent)
        (ok if len(counts) == 4 else ng).append(pair)

    print(f"\n揃った銘柄 {len(ok)} 件")
    if ng:
        print(f"揃わなかった銘柄 {len(ng)} 件: {', '.join(ng)}")
        print("  → 足りない時間足がある銘柄は、検証で NO_TRADE になります")

    print("\n注意: intraday は Yahoo 側の制限で M5/M15 が約60日ぶんまでです。")
    print("      H4 は H1 から作った合成足です（Yahoo に4時間足が無いため）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
