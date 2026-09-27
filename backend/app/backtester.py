# -*- coding: utf-8 -*-
"""過去の足を1本ずつ進めて、判断と結果を突き合わせる。

ここができるまで、勝率や期待値を名乗ってはいけなかった。逆に言えば、
**ここが正しくないと、間違った自信をつけるだけの道具になる。**
だから未来を覗かないための決まりを、実装の側で守らせている。

1. **判断に使う足は、判断時点までのもの だけ。**
   供給元を包み、指定した時刻より後ろの足を物理的に見えなくする。
   「使わないように気をつける」ではなく、渡さない。
2. **建玉は判断した足の次の足の始値。**
   判断した足の終値で建てると、その終値を知ったうえで建てたことになる。
   market-radar で的中率が29.6%まで落ちた原因がこれだった。
3. **スプレッドを建玉時に全額払う。** 決済は仲値で測る。
   費用を引かない成績は、取引できない成績。
4. **1本の足が損切りと利確の両方に触れたら、損切り側を採る。**
   足の中の順序は分からない。分からないときは悪いほうに倒す。

そして集計側（performance.py）で、件数が足りない区分では勝率を名乗らない。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

from .analysis import ANALYSIS_TIMEFRAMES, analyze_pair
from .day_trade import exit_deadline as day_trade_deadline
from .day_trade import max_hold_bars as day_trade_hold
from .config import TradingConfig
from .market_data import MarketDataError, MarketDataProvider
from .models import (
    Candle,
    CandleSeries,
    Direction,
    ProviderState,
    ProviderStatus,
    Quote,
    SpreadInfo,
)
from .performance import (
    Stats,
    Stratified,
    by_band,
    compare,
    component_effect,
    effect_direction,
    monotonicity,
    stratified_compare,
    summarize,
)

log = logging.getLogger(__name__)

PHASE = 2

# 配点の満点。項目ごとの効き方を見るときの区切りに使う。
_DIAG_WEIGHTS = {
    "trend": 20.0, "momentum": 15.0, "support_resistance": 15.0,
    "volatility": 10.0, "price_action": 10.0, "session": 5.0,
    "spread_risk": 5.0,
}


# ------------------------------------------------------------ 未来を隠す供給元


class HistoryProvider(MarketDataProvider):
    """全履歴を持ち、指定時刻までしか見せない供給元。

    **これがこの道具の要。** 判断側のコードを一切変えずに、見える範囲だけを
    絞る。判断側が「うっかり未来を見る」経路を残さないための作りにしてある。
    """

    name = "history"

    def __init__(self, pair: str, series_by_tf: Dict[str, CandleSeries],
                 spread: Optional[SpreadInfo],
                 minutes_by_tf: Optional[Dict[str, int]] = None) -> None:
        self.pair = pair.upper()
        self._full = series_by_tf
        self._spread = spread
        # 時間足ごとの長さ。**確定した足だけを見せる**ために要る。
        self._minutes = dict(minutes_by_tf or {})
        self.cutoff: Optional[datetime] = None
        # 時刻の切り出しを毎回やると遅いので、時刻の並びだけ先に持っておく
        self._times = {tf: [c.timestamp for c in s.candles]
                       for tf, s in series_by_tf.items()}

    def set_cutoff(self, when: datetime) -> None:
        self.cutoff = when

    def _visible(self, tf: str) -> List[Candle]:
        series = self._full.get(tf)
        if series is None:
            raise MarketDataError(f"{tf} の履歴を持っていません")
        if self.cutoff is None:
            return list(series.candles)

        # **その瞬間に配信されていたはずの足だけを見せる。**
        #
        # 時刻 T の足が確定するのは T + 期間。打ち切り時点で H4 はたいてい
        # 途中の足を持っているので、「timestamp <= 打ち切り」で切ると
        # 未確定の足が混じる。実際の配信では未完成足は除かれるので、
        # そのままでは検証と実運用がずれる。実際、判断の約1/3が
        # 「最後の足が未確定」で止まっていた。
        minutes = self._minutes.get(tf, 0)
        limit_ts = (self.cutoff - timedelta(minutes=minutes) if minutes
                    else self.cutoff)
        times = self._times[tf]
        lo, hi = 0, len(times)
        while lo < hi:
            mid = (lo + hi) // 2
            if times[mid] <= limit_ts:
                lo = mid + 1
            else:
                hi = mid
        return list(series.candles[:lo])

    def get_candles(self, pair: str, timeframe: str, limit: int = 300) -> CandleSeries:
        if pair.upper() != self.pair:
            raise MarketDataError(f"この検証は {self.pair} 専用です: {pair}")
        visible = self._visible(timeframe)
        if not visible:
            raise MarketDataError(f"{timeframe} に見える足がありません")
        return CandleSeries(pair=self.pair, timeframe=timeframe,
                            candles=visible[-limit:])

    def get_latest_price(self, pair: str) -> Quote:
        visible = self._visible(ANALYSIS_TIMEFRAMES[-1])
        if not visible:
            raise MarketDataError("見える足がありません")
        last = visible[-1]
        half = (self._spread.spread_price / 2.0) if self._spread else 0.0
        return Quote(pair=self.pair, bid=last.close - half,
                     ask=last.close + half, timestamp=last.timestamp)

    def get_spread(self, pair: str) -> SpreadInfo:
        """検証の間はスプレッドを一定とみなす。

        実際には時間帯や指標発表で広がる。**そこを一定にしている以上、
        この検証は実際よりわずかに甘い側に出る。** 承知のうえで使う。
        """
        if self._spread is None:
            raise MarketDataError("スプレッドの情報がありません")
        return self._spread

    def provider_status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name, state=ProviderState.OK,
            detail="過去の足を再生しています。実時間の値ではありません。",
            checked_at=self.cutoff or datetime.now().astimezone(),
        )


# ------------------------------------------------------------------ 建玉


@dataclass
class Trade:
    """1件ぶんの建玉と結果。"""

    pair: str
    timeframe: str
    decided_at: datetime
    entered_at: datetime
    direction: str          # LONG / SHORT
    signal: str             # BUY / SELL / WAIT / NO_TRADE
    score: float
    quality: str
    regime: str
    # どの戦略で向きを決めたか。**戦略ごとに分けて測るために要る。**
    # 混ぜると、効かない戦略が効く戦略の成績に紛れて見えなくなる。
    entry: float
    stop: float
    target: float
    exited_at: Optional[datetime] = None
    exit_price: Optional[float] = None
    outcome: str = "TIMEOUT"     # WIN / LOSS / TIMEOUT
    r_multiple: float = 0.0
    bars_held: int = 0
    # どの戦略で向きを決めたか。**戦略ごとに分けて測るために要る。**
    # 混ぜると、効かない戦略が効く戦略の成績に紛れて見えなくなる。
    strategy: str = ""
    taken: bool = True           # False は比較用に記録しただけのもの
    hard_blocked: bool = False   # 強制条件で弾かれた場面か
    breakdown: Dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> Dict:
        return {
            "pair": self.pair, "decided_at": self.decided_at.isoformat(),
            "entered_at": self.entered_at.isoformat(),
            "direction": self.direction, "signal": self.signal,
            "score": self.score, "quality": self.quality, "regime": self.regime,
            "strategy": self.strategy,
            "entry": self.entry, "stop": self.stop, "target": self.target,
            "exited_at": self.exited_at.isoformat() if self.exited_at else None,
            "exit_price": self.exit_price, "outcome": self.outcome,
            "r_multiple": round(self.r_multiple, 4), "bars_held": self.bars_held,
            "taken": self.taken,
        }


def simulate_bracket(bars: Sequence[Candle], start: int, is_long: bool,
                     entry: float, stop: float, target: float,
                     max_hold: int, ambiguous: str = "stop_first",
                     deadline: Optional[datetime] = None) -> Dict:
    """建玉してから決着までを1本ずつ進める。

    足の中の値動きの順序は分からない。高値と安値のどちらが先かは、
    足のデータからは決められない。だから両方に触れた足は
    **損切り側**を採る（``ambiguous='stop_first'``）。
    都合よく利確側を採ると、成績は実際より良く出る。

    ``deadline`` を渡すと、その時刻までに必ず閉じる（日計り）。

    **本数で区切るだけでは足りない。** 足が欠けていると、32本ぶんが
    32×15分より長い時間に広がる。週末や、配信元の穴（実際に
    19:25〜23:00 が丸ごと無い日があった）を跨ぐと、刻限を越えた玉が
    できる。実測で48件中3件が越えていた。**時刻で区切る。**
    """
    risk = abs(entry - stop)
    if risk <= 0:
        return {"outcome": "TIMEOUT", "exit_price": entry, "index": start,
                "bars": 0, "r": 0.0}

    last = min(len(bars) - 1, start + max_hold - 1)
    if deadline is not None:
        for i in range(start, last + 1):
            if bars[i].timestamp >= deadline:
                last = max(start, i - 1)
                break
    for i in range(start, last + 1):
        bar = bars[i]
        hit_stop = bar.low <= stop if is_long else bar.high >= stop
        hit_target = bar.high >= target if is_long else bar.low <= target

        if hit_stop and hit_target:
            if ambiguous == "stop_first":
                hit_target = False
            else:
                hit_stop = False

        if hit_stop:
            r = (stop - entry) / risk if is_long else (entry - stop) / risk
            return {"outcome": "LOSS", "exit_price": stop, "index": i,
                    "bars": i - start + 1, "r": r}
        if hit_target:
            r = (target - entry) / risk if is_long else (entry - target) / risk
            return {"outcome": "WIN", "exit_price": target, "index": i,
                    "bars": i - start + 1, "r": r}

    close = bars[last].close
    r = (close - entry) / risk if is_long else (entry - close) / risk
    return {"outcome": "TIMEOUT", "exit_price": close, "index": last,
            "bars": last - start + 1, "r": r}


# ------------------------------------------------------------------ 走らせる


@dataclass
class PairReport:
    """1銘柄ぶんの検証結果。"""

    pair: str
    timeframe: str
    bars_tested: int = 0
    decisions: int = 0
    errors: int = 0
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    trades: List[Trade] = field(default_factory=list)
    observations: List[Trade] = field(default_factory=list)
    controls: List[Trade] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    signal_counts: Dict[str, int] = field(default_factory=dict)


def _stable_coin(key: str) -> bool:
    """対照群の向きを決めるコイン。起動しても同じ結果になるようにする。

    組み込みの ``hash()`` は起動ごとに変わるので使わない。対照群が毎回
    変わると、差が出たのか揺れただけなのか分からなくなる。
    """
    import hashlib
    return hashlib.sha256(key.encode("utf-8")).digest()[0] % 2 == 0


def _history_sizes(cfg: TradingConfig, entry_tf: str, bars: int) -> Dict[str, int]:
    """時間足ごとに何本取り寄せるかを決める。

    **同じ本数では駄目。** M15 を500本取ると約125時間ぶんだが、M5 を500本
    取っても約41時間ぶんにしかならない。同じ本数で揃えると、検証の前半で
    下位足の履歴が尽きて「M5 に見える足がありません」になる。実際そうなった。

    だから「入口の足で ``bars`` 本ぶんの期間」を基準にし、各時間足が
    その期間を覆うだけの本数に、指標の立ち上がりぶんを足して求める。
    """
    span = bars * cfg.timeframe_minutes(entry_tf)      # 検証する期間（分）
    sizes: Dict[str, int] = {}
    for tf in ANALYSIS_TIMEFRAMES:
        minutes = cfg.timeframe_minutes(tf)
        need = cfg.timeframes["min_candles"].get(tf, 250)
        sizes[tf] = int(-(-span // minutes)) + need    # 切り上げ + 立ち上がり
    return sizes


def _fetch_history(pair: str, provider: MarketDataProvider,
                   cfg: TradingConfig, bars: int,
                   entry_tf: str) -> Dict[str, CandleSeries]:
    sizes = _history_sizes(cfg, entry_tf, bars)
    out: Dict[str, CandleSeries] = {}
    for tf in ANALYSIS_TIMEFRAMES:
        out[tf] = provider.get_candles(pair, tf, limit=sizes[tf])
    return out


def run_pair(pair: str, provider: MarketDataProvider, cfg: TradingConfig,
             bt: Dict) -> PairReport:
    """1銘柄を過去にさかのぼって検証する。"""
    spec = cfg.pair(pair)
    entry_tf = bt["entry_timeframe"]
    rep = PairReport(pair=spec.symbol, timeframe=entry_tf)

    history = _fetch_history(spec.symbol, provider, cfg, bt["history_bars"],
                             entry_tf)
    try:
        spread: Optional[SpreadInfo] = provider.get_spread(spec.symbol)
    except MarketDataError:
        spread = None
        rep.warnings.append(
            "スプレッドを取得できませんでした。費用を引かない成績は実際より良く出ます")

    tf_minutes = {tf: cfg.timeframe_minutes(tf) for tf in ANALYSIS_TIMEFRAMES}
    hp = HistoryProvider(spec.symbol, history, spread, tf_minutes)
    entry_bars = history[entry_tf].candles
    warm = bt["warmup_bars"]
    step = max(1, int(bt["step_bars"]))
    stop_atr = bt["exit"]["stop_atr"]
    target_atr = bt["exit"]["target_atr"]
    max_hold = bt["exit"]["max_hold_bars"]
    ambiguous = bt["exit"]["ambiguous_bar"]
    trade_on = set(bt["signals"]["trade_on"])
    observe_on = set(bt["signals"]["observe_on"])
    pay_spread = bt["entry"]["pay_spread_on_entry"]
    spread_price = spread.spread_price if (spread and pay_spread) else 0.0

    # 最後の足で判断しても建玉できない（次の足が無い）。決着の余地も要る。
    last_decision = len(entry_bars) - 2
    if last_decision <= warm:
        rep.warnings.append(
            f"足が {len(entry_bars)} 本しかなく、立ち上がり {warm} 本を引くと"
            f"検証できる範囲が残りません")
        return rep

    rep.period_start = entry_bars[warm].timestamp
    rep.period_end = entry_bars[last_decision].timestamp

    # 足の時刻は「始まり」を指す規約なので、足 i が確定するのは i の時刻 + 期間。
    # 判断できるのはその瞬間であって、足の開始時刻ではない。
    # ここを取り違えると、未確定の足を確定として扱う検査に自分で引っかかる。
    entry_period = timedelta(minutes=cfg.timeframe_minutes(entry_tf))

    for i in range(warm, last_decision + 1, step):
        bar = entry_bars[i]
        decision_now = bar.timestamp + entry_period   # 足 i が閉じた瞬間
        hp.set_cutoff(decision_now)      # その瞬間に確定している足だけ
        rep.bars_tested += 1
        try:
            res = analyze_pair(spec.symbol, hp, cfg, now=decision_now)
        except Exception:                       # analyze_pair は倒すが念のため
            rep.errors += 1
            log.exception("検証中に例外 %s %s", spec.symbol, decision_now)
            continue

        rep.decisions += 1
        sig = res.signal.value
        rep.signal_counts[sig] = rep.signal_counts.get(sig, 0) + 1

        taken = sig in trade_on
        if not taken and sig not in observe_on:
            continue

        # 比較用の記録でも向きが要る。定まっていないものは扱えない。
        if res.direction is Direction.NEUTRAL:
            continue
        is_long = res.direction is Direction.LONG

        atr = None
        snap = res.indicators.get(entry_tf) if res.indicators else None
        if snap is not None:
            atr = snap.atr14
        if not atr or atr <= 0:
            continue

        fill = entry_bars[i + 1].open
        entry = fill + spread_price if is_long else fill - spread_price
        stop = entry - stop_atr * atr if is_long else entry + stop_atr * atr
        target = entry + target_atr * atr if is_long else entry - target_atr * atr

        # **検証でも刻限を守る。** 守らないと、実際には持てない時間まで
        # 持った成績を測ることになり、画面の動きと数字が食い違う。
        hold = day_trade_hold(cfg.filters, decision_now,
                              cfg.timeframe_minutes(entry_tf), max_hold)
        limit = day_trade_deadline(cfg.filters, decision_now)
        sim = simulate_bracket(entry_bars, i + 1, is_long, entry, stop, target,
                               hold, ambiguous, deadline=limit)

        # 対照群: 同じ時刻・同じ幅で、向きだけコイン投げにしたもの。
        #
        # **これが無いと、成績が「判断のおかげ」なのか「その期間そう動いた
        # だけ」なのか区別できない。** 上昇が続く期間なら、何も考えず買う
        # だけで勝率は上がる。それと差が無いなら、判断は何もしていない。
        coin = _stable_coin(f"{spec.symbol}|{bar.timestamp.isoformat()}")
        c_long = coin
        c_entry = fill + spread_price if c_long else fill - spread_price
        c_stop = c_entry - stop_atr * atr if c_long else c_entry + stop_atr * atr
        c_target = (c_entry + target_atr * atr if c_long
                    else c_entry - target_atr * atr)
        c_sim = simulate_bracket(entry_bars, i + 1, c_long, c_entry, c_stop,
                                 c_target, hold, ambiguous,
                                 deadline=limit)
        rep.controls.append(Trade(
            pair=spec.symbol, timeframe=entry_tf,
            decided_at=bar.timestamp, entered_at=entry_bars[i + 1].timestamp,
            direction="LONG" if c_long else "SHORT",
            signal="CONTROL", score=res.score, quality=res.quality.value,
            regime=res.regime.value, strategy=res.strategy or "",
            entry=round(c_entry, 6), stop=round(c_stop, 6),
            target=round(c_target, 6),
            exited_at=entry_bars[c_sim["index"]].timestamp,
            exit_price=round(c_sim["exit_price"], 6),
            outcome=c_sim["outcome"], r_multiple=c_sim["r"],
            bars_held=c_sim["bars"], taken=False,
            hard_blocked=res.hard_blocked))

        t = Trade(
            pair=spec.symbol, timeframe=entry_tf,
            decided_at=bar.timestamp, entered_at=entry_bars[i + 1].timestamp,
            direction="LONG" if is_long else "SHORT",
            signal=sig, score=res.score, quality=res.quality.value,
            regime=res.regime.value, strategy=res.strategy or "",
            entry=round(entry, 6), stop=round(stop, 6), target=round(target, 6),
            exited_at=entry_bars[sim["index"]].timestamp,
            exit_price=round(sim["exit_price"], 6),
            outcome=sim["outcome"], r_multiple=sim["r"], bars_held=sim["bars"],
            taken=taken, hard_blocked=res.hard_blocked,
            breakdown={k: float(v) for k, v in
                       res.score_breakdown.model_dump().items()
                       if isinstance(v, (int, float))},
        )
        (rep.trades if taken else rep.observations).append(t)

    return rep


@dataclass
class BacktestReport:
    """全体の結果。**警告を外して読めないように、同じ入れ物に入れてある。**"""

    pairs: List[str] = field(default_factory=list)
    provider: str = ""
    bars_tested: int = 0
    decisions: int = 0
    errors: int = 0
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    signal_counts: Dict[str, int] = field(default_factory=dict)
    taken: Optional[Stats] = None
    baseline: Optional[Stats] = None
    blocked: Optional[Stats] = None
    all_bars: Optional[Stats] = None
    control: Optional[Stats] = None
    bands: List[Stats] = field(default_factory=list)
    comparison: Optional[object] = None
    vs_control: Optional[object] = None
    vs_blocked: Optional[object] = None
    stratified: Optional[Stratified] = None
    # **向きを揃えた比較。** 下げ相場では判断の9割が SHORT になり、
    # 対照群（50/50）の「良い半分」をそのまま受け取るだけで勝率が上がる。
    # 揃えずに比べると、期間の偏りを実力と読み違える。
    stratified_direction: Optional[Stratified] = None
    band_trend: str = ""
    component_effects: Dict[str, Dict] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    per_pair: Dict[str, Dict] = field(default_factory=dict)
    settings: Dict = field(default_factory=dict)

    def as_dict(self) -> Dict:
        return {
            "pairs": self.pairs, "provider": self.provider,
            "bars_tested": self.bars_tested, "decisions": self.decisions,
            "errors": self.errors,
            "period": [self.period_start.isoformat() if self.period_start else None,
                       self.period_end.isoformat() if self.period_end else None],
            "signal_counts": self.signal_counts,
            "taken": self.taken.as_dict() if self.taken else None,
            "baseline": self.baseline.as_dict() if self.baseline else None,
            "blocked": self.blocked.as_dict() if self.blocked else None,
            "all_bars": self.all_bars.as_dict() if self.all_bars else None,
            "control": self.control.as_dict() if self.control else None,
            "vs_control": self.vs_control.as_dict() if self.vs_control else None,
            "vs_blocked": self.vs_blocked.as_dict() if self.vs_blocked else None,
            "stratified": self.stratified.as_dict() if self.stratified else None,
            "stratified_direction": (self.stratified_direction.as_dict()
                                     if self.stratified_direction else None),
            "bands": [b.as_dict() for b in self.bands],
            "comparison": self.comparison.as_dict() if self.comparison else None,
            "band_trend": self.band_trend,
            "component_effects": self.component_effects,
            "warnings": self.warnings,
            "per_pair": self.per_pair,
            "settings": self.settings,
        }


def _provider_warnings(provider: MarketDataProvider) -> List[str]:
    """供給元に由来する、結果の読み方の制限。"""
    out: List[str] = []
    if getattr(provider, "name", "") == "mock":
        out.append(
            "【最重要】合成データでの検証です。ここに出る数字は、合成データを作る"
            "式の性質を測ったものであって、相場について何も述べていません。"
            "売買の判断に使えません。")
        out.append(
            "合成データは時間足ごとに別々に作っており、H4 と M15 が同じ値動きに"
            "なっていません。上位足との関係を見る部分は、この検証では意味を持ちません。")
    out.append(
        "最新の足を「完成した足」として扱っています。実際の配信では最後の足が"
        "未完成のことがあり、そこを取り違えると成績は大きく崩れます"
        "（market-radar で実際に起きました）。実データを繋ぐ前に必ず確かめること。")
    out.append(
        "スプレッドを一定として計算しています。実際は時間帯や指標発表で広がるので、"
        "この結果は実際よりわずかに甘い側に出ます。")
    out.append(
        "滑り（約定のずれ）・スワップ・取引の上限を考慮していません。")
    return out


def run(pairs: Sequence[str], provider: MarketDataProvider, cfg: TradingConfig,
        bt: Optional[Dict] = None) -> BacktestReport:
    """指定した銘柄をまとめて検証する。"""
    bt = bt or cfg.backtest
    rep = BacktestReport(provider=getattr(provider, "name", "unknown"))
    rep.warnings = _provider_warnings(provider)
    rep.settings = {
        "entry_timeframe": bt["entry_timeframe"],
        "history_bars": bt["history_bars"], "warmup_bars": bt["warmup_bars"],
        "step_bars": bt["step_bars"],
        "stop_atr": bt["exit"]["stop_atr"], "target_atr": bt["exit"]["target_atr"],
        "max_hold_bars": bt["exit"]["max_hold_bars"],
        "ambiguous_bar": bt["exit"]["ambiguous_bar"],
        "pay_spread_on_entry": bt["entry"]["pay_spread_on_entry"],
    }

    all_taken: List[Trade] = []
    all_observed: List[Trade] = []
    all_control: List[Trade] = []

    for p in pairs:
        try:
            pr = run_pair(p, provider, cfg, bt)
        except (MarketDataError, KeyError) as exc:
            rep.warnings.append(f"{p}: 検証できませんでした（{exc}）")
            continue

        rep.pairs.append(pr.pair)
        rep.bars_tested += pr.bars_tested
        rep.decisions += pr.decisions
        rep.errors += pr.errors
        for k, v in pr.signal_counts.items():
            rep.signal_counts[k] = rep.signal_counts.get(k, 0) + v
        if pr.period_start and (rep.period_start is None
                                or pr.period_start < rep.period_start):
            rep.period_start = pr.period_start
        if pr.period_end and (rep.period_end is None
                              or pr.period_end > rep.period_end):
            rep.period_end = pr.period_end
        rep.warnings.extend(f"{pr.pair}: {w}" for w in pr.warnings)

        all_taken.extend(pr.trades)
        all_observed.extend(pr.observations)
        all_control.extend(pr.controls)
        rep.per_pair[pr.pair] = {
            "bars_tested": pr.bars_tested, "decisions": pr.decisions,
            "trades": len(pr.trades), "observations": len(pr.observations),
            "signal_counts": pr.signal_counts,
            "stats": summarize(pr.trades, label=pr.pair,
                               min_samples=bt["reporting"]["min_samples"],
                               z=bt["reporting"]["confidence_z"]).as_dict(),
        }

    ms = bt["reporting"]["min_samples"]
    z = bt["reporting"]["confidence_z"]
    everything = all_taken + all_observed

    # **基準は「強制条件を通った場面」に限る。**
    #
    # 全部の足を基準にすると、費用が見合わない場面（スプレッドが ATR を
    # 上回る銘柄など）まで混ざる。それは必ず負けるので、基準は自動的に
    # ひどい数字になり、何と比べても「選別は有効」に見えてしまう。
    # 実データで実際にそうなった。16,224回の判断のうち 15,778 回が強制の
    # 見送りで、基準の勝率が 14% という現実には有り得ない値になった。
    #
    # 測りたいのは**点数の良し悪し**なので、強制条件で弾かれた場面は
    # 基準から外し、別に集計する。
    viable = [t for t in everything if not t.hard_blocked]
    blocked = [t for t in everything if t.hard_blocked]

    rep.taken = summarize(all_taken, label="建てたもの", min_samples=ms, z=z)
    rep.baseline = summarize(viable, label="基準（強制条件を通った場面）",
                             min_samples=ms, z=z)
    rep.blocked = summarize(blocked, label="強制条件で弾いた場面",
                            min_samples=ms, z=z)
    rep.all_bars = summarize(everything, label="参考・全部の足",
                             min_samples=ms, z=z)
    rep.bands = by_band(viable, bt["score_bands"], min_samples=ms, z=z)
    rep.control = summarize([c for c in all_control if not c.hard_blocked],
                            label="対照群（向きはコイン投げ・同じ場面）",
                            min_samples=ms, z=z)
    rep.comparison = compare(rep.taken, rep.baseline,
                             label="建てたもの vs 強制条件を通った場面")
    rep.vs_control = compare(rep.baseline, rep.control,
                             label="向きの判断 vs コイン投げ")
    rep.vs_blocked = compare(rep.baseline, rep.blocked,
                             label="強制条件を通った場面 vs 弾いた場面")
    # 銘柄ごとに揃えた比較。まとめて比べると composition で必ず誤る。
    rep.stratified = stratified_compare(all_taken, viable,
                                        key="pair", min_samples=ms)
    # **向きを揃えて、コイン投げと比べる。**
    #
    # 揃えないと、期間の偏りをそのまま実力として読んでしまう。実際、
    # 2026-09-15 の実データでは判断の93%が SHORT で、揃えない比較では
    # +7.1pt（p<0.001）に見えたが、向きを揃えると -0.5pt（p=0.802）で
    # 差が消えた。銘柄で一度踏んだ罠を、向きでもう一度踏みかけた。
    rep.stratified_direction = stratified_compare(
        viable, [c for c in all_control if not c.hard_blocked],
        key="direction", min_samples=ms)
    rep.band_trend = monotonicity(rep.bands)

    # 配点のどの項目が効いていて、どれが逆に効いているか。
    # 合計点だけ見ていると「80点台が弱い」までしか言えない。
    #
    # **ここも強制条件を通った場面だけで見る。** 弾いた場面を混ぜると、
    # 「費用が見合わないから負けた」が「その項目のせいで負けた」に化ける。
    universe = viable
    for comp, full in bt.get("weights_for_diagnosis", _DIAG_WEIGHTS).items():
        edges = [0.0, full * 0.25, full * 0.5, full * 0.75, full + 1e-9]
        buckets = component_effect(universe, comp, edges, min_samples=ms)
        rep.component_effects[comp] = {
            "buckets": [b.as_dict() for b in buckets],
            "direction": effect_direction(buckets),
        }
    return rep


# ------------------------------------------------------------------ 表示


def format_report(rep: BacktestReport) -> str:
    """人が読む形にする。**警告を先に出す。** 数字だけ抜き出させないため。"""
    L: List[str] = []
    L.append("=" * 72)
    L.append("検証結果 — これは取引の推奨ではありません")
    L.append("=" * 72)
    for w in rep.warnings:
        L.append(f"  ! {w}")
    L.append("")
    L.append(f"供給元       : {rep.provider}")
    L.append(f"銘柄         : {len(rep.pairs)} 件  {', '.join(rep.pairs[:8])}"
             + (" ..." if len(rep.pairs) > 8 else ""))
    L.append(f"期間         : {rep.period_start} 〜 {rep.period_end}")
    L.append(f"判断した回数 : {rep.decisions}（例外 {rep.errors}）")
    L.append(f"内訳         : {rep.signal_counts}")
    L.append(f"条件         : {rep.settings}")
    L.append("")

    def block(s: Stats) -> None:
        L.append(f"[{s.label}]")
        L.append(f"  件数 {s.n}（勝 {s.wins} / 負 {s.losses} / 時間切れ {s.timeouts}）")
        if s.win_rate is None:
            L.append(f"  勝率 — {s.note}")
        else:
            L.append(f"  勝率 {s.win_rate:.1%}  95%区間 "
                     f"[{s.win_rate_low:.1%}, {s.win_rate_high:.1%}]")
        L.append(f"  平均R {s.avg_r}  合計R {s.total_r:.2f}  "
                 f"最大の落ち込み {s.max_drawdown_r}R")
        L.append("")

    block(rep.taken)
    block(rep.baseline)
    if rep.control:
        block(rep.control)
    if rep.blocked:
        block(rep.blocked)
    if rep.all_bars:
        block(rep.all_bars)

    if rep.vs_blocked is not None:
        L.append(f"強制条件の働き: {rep.vs_blocked.verdict}")
        L.append("")
    L.append("点数の区分ごと（**強制条件を通った場面だけ**、同じ規則で測った結果）")
    L.append(f"{'区分':<8}{'件数':>6}{'勝':>5}{'負':>5}{'時間切れ':>8}"
             f"{'勝率':>8}{'平均R':>9}")
    for b in rep.bands:
        wr = "—" if b.win_rate is None else f"{b.win_rate:.1%}"
        ar = "—" if b.avg_r is None else f"{b.avg_r:+.4f}"
        L.append(f"{b.label:<8}{b.n:>6}{b.wins:>5}{b.losses:>5}{b.timeouts:>8}"
                 f"{wr:>8}{ar:>9}")
    L.append("")
    L.append(f"傾向: {rep.band_trend}")
    L.append("")
    L.append("配点の項目ごとの効き方（得点が上がると成績はどうなるか）")
    for comp, info in rep.component_effects.items():
        ns = [b["n"] for b in info["buckets"]]
        rs = ["—" if b["avg_r"] is None else f"{b['avg_r']:+.3f}"
              for b in info["buckets"]]
        L.append(f"  {comp:<20} 低→高 {' '.join(f'{r:>7}' for r in rs)}"
                 f"   (n={'/'.join(str(x) for x in ns)})")
        L.append(f"  {'':<20} {info['direction']}")
    if rep.stratified is not None:
        L.append(f"点数の選別（銘柄を揃えて）: {rep.stratified.verdict}")
    if rep.stratified_direction is not None:
        L.append(f"向きの判断（向きを揃えて）: "
                 f"{rep.stratified_direction.verdict}")
    if rep.comparison is not None:
        L.append(f"（参考・銘柄を揃えない比較）: {rep.comparison.verdict}")
    if rep.vs_control is not None:
        L.append(f"向きの判断: {rep.vs_control.verdict}")
    L.append("")
    L.append("注意: 点数は場面の整い方であって、勝率でも上昇確率でもありません。")
    return "\n".join(L)


# ------------------------------------------------------------------ CLI


def main(argv: Optional[Sequence[str]] = None) -> int:
    """コマンドから走らせる。``python -m app.backtester --help``"""
    import argparse
    import json
    import sys

    from .config import get_settings, get_trading_config
    from .market_data import build_provider

    # Windows の既定の文字コードでは出力に含まれる記号が落ちる
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    cfg = get_trading_config()
    bt = dict(cfg.backtest)

    ap = argparse.ArgumentParser(
        description="過去の足で判断と結果を突き合わせる。取引の推奨ではありません。")
    ap.add_argument("--pairs", default="",
                    help="カンマ区切り。省略すると有効な全銘柄")
    ap.add_argument("--provider", default=None, help="mock / csv")
    ap.add_argument("--bars", type=int, default=bt["history_bars"],
                    help="入口の足で何本ぶん検証するか")
    ap.add_argument("--step", type=int, default=bt["step_bars"],
                    help="何本ごとに判断するか")
    ap.add_argument("--stop-atr", type=float, default=bt["exit"]["stop_atr"])
    ap.add_argument("--target-atr", type=float, default=bt["exit"]["target_atr"])
    ap.add_argument("--anchor", default=None,
                    help="合成データの終端を固定する（例 2026-09-01T00:00:00Z）。"
                         "省略すると当日0時に固定する。走らせるたびに揺れないようにするため")
    ap.add_argument("--json", dest="json_path", default=None,
                    help="結果を JSON で書き出す先")
    a = ap.parse_args(argv)

    bt["history_bars"] = a.bars
    bt["step_bars"] = a.step
    bt["exit"] = dict(bt["exit"])
    bt["exit"]["stop_atr"] = a.stop_atr
    bt["exit"]["target_atr"] = a.target_atr

    settings = get_settings()
    if a.provider:
        settings = settings.model_copy(update={"market_data_provider": a.provider})
    provider = build_provider(settings)

    # 合成データの終端を固定する。既定のままだと走らせるたびに終端が動き、
    # 同じ条件でも結果が少しずつ変わってしまう。
    if getattr(provider, "name", "") == "mock":
        from datetime import datetime as _dt, timezone as _tz

        from .market_data import MockMarketDataProvider as _Mock
        if a.anchor:
            anchor = _dt.fromisoformat(a.anchor.replace("Z", "+00:00"))
        else:
            anchor = _dt.now(_tz.utc).replace(
                hour=0, minute=0, second=0, microsecond=0)
        provider = _Mock(end_time=anchor)
        print(f"合成データの終端を {anchor.isoformat()} に固定しました")

    pairs = ([p.strip().upper() for p in a.pairs.split(",") if p.strip()]
             or [p.symbol for p in cfg.enabled_pairs()])

    rep = run(pairs, provider, cfg, bt)
    print(format_report(rep))

    if a.json_path:
        with open(a.json_path, "w", encoding="utf-8") as f:
            json.dump(rep.as_dict(), f, ensure_ascii=False, indent=2)
        print(f"\n書き出し: {a.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
