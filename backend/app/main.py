# -*- coding: utf-8 -*-
"""FastAPI の入口。

この段階では**発注も自動売買も行わない**。相場を分析して、判断の材料を
返すところまで。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import __version__
from .analysis import enrich_indicators
from .analysis_service import analyze
from .indicator_engine import compute_all
from .config import get_settings, get_trading_config
from .db import check_health
from .logging_setup import ReasonCode, setup_logging
from .market_data import (
    CSVMarketDataProvider,
    MarketDataError,
    MarketDataProvider,
    build_provider,
)
from .models import (
    AnalysisResult,
    HealthResponse,
    ProviderStatus,
    RankingResponse,
)
from .entry_engine import collect_rates
from .pair_ranker import rank_pairs
from .board import build_board
from . import board as board_mod
from . import brief_import as brief_mod
from .data_status import collect as collect_data_status
from . import focus as focus_mod
from .morning_brief import as_text as brief_as_text
from .morning_brief import build as build_brief
from .strategies import REGISTRY
from . import trade_logger
from .trade_plan import build_plan

settings = get_settings()
setup_logging(settings.log_level, settings.log_dir)
log = logging.getLogger(__name__)

# 実データでの検証状況。**「実装した」と「効くと分かった」は別のこと。**
# 画面にこの文言を書かず、ここから返す（docs/backtesting.md と同じ結論）。
# **画面にそのまま出す文なので、書式の記号を混ぜない。** 以前 ** で
# 強調したまま返してしまい、画面に星印が並んで出た。
MEASURED_NOTE = (
    "2026-09-15、実データ26銘柄・30,914回の判断で測りました。"
    "5つとも、コイン投げに勝つ材料は出ていません。"
    "向きが出た場面の 77〜100% はスプレッドの強制条件で消えており、"
    "押し目継続（momentum_flag）は一度も測れていません。"
    "実装したことと、効くと分かったことは別です。"
    "詳細は docs/backtesting.md にあります。")

app = FastAPI(
    title="FX AI Day Trading Decision Engine",
    version=__version__,
    description=("相場を分析して BUY / SELL / WAIT / NO_TRADE を返します。"
                 "自動売買と発注は含みません。点数は勝率ではありません。"),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_provider: Optional[MarketDataProvider] = None


def get_provider() -> MarketDataProvider:
    global _provider
    if _provider is None:
        _provider = build_provider(settings)
    return _provider


class PairInfo(BaseModel):
    symbol: str
    base: str
    quote: str
    pip: float
    digits: int
    enabled: bool


class PairsResponse(BaseModel):
    version: str
    count: int
    pairs: List[PairInfo]


class CSVLoadRequest(BaseModel):
    """開発用。置き場にある CSV を読めるか確かめるだけ。"""

    pair: str
    timeframe: str
    limit: int = 300


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    provider = get_provider()
    status = provider.provider_status()
    return HealthResponse(
        status="ok",
        version=__version__,
        database=check_health(settings.resolved_database_url),
        market_data=status.name,
        provider_state=status.state,
        timestamp=datetime.now(timezone.utc),
    )


@app.get("/api/pairs", response_model=PairsResponse)
def list_pairs() -> PairsResponse:
    cfg = get_trading_config()
    return PairsResponse(
        version=__version__,
        count=len(cfg.pairs),
        pairs=[PairInfo(**p.model_dump()) for p in cfg.pairs],
    )


@app.get("/api/pairs/{pair}", response_model=PairInfo)
def get_pair(pair: str) -> PairInfo:
    cfg = get_trading_config()
    try:
        return PairInfo(**cfg.pair(pair).model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _forecast_band(cfg, timeframe: str, last_close, last_atr):
    """この先どれだけ動きうるかの幅。**向きは予想しない。**

    倍率も覆い率も ``config/forecast.json`` から引く。これは
    ``scripts/measure_forecast.py`` が実データから測って書き出したもので、
    **決めた期間とは別の期間で覆い率を確かめてある**。

    測っていなければ ``None`` を返す。**推測で幅を描かない。**
    測った足と違う足でも返さない。M15 で測った倍率を H4 に当てると、
    測っていないものを測ったふりで出すことになる。
    """
    fc = getattr(cfg, "forecast", None) or {}
    horizons = fc.get("horizons") or {}
    if not horizons or fc.get("timeframe") != timeframe:
        return None
    if last_close is None or not last_atr or last_atr <= 0:
        return None
    levels = []
    for key in sorted(horizons, key=lambda k: int(k)):
        h = horizons[key]
        mult = h.get("atr_multiple")
        if mult is None:
            continue
        width = float(mult) * float(last_atr)
        levels.append({
            "bars": int(key),
            "low": round(float(last_close) - width, 6),
            "high": round(float(last_close) + width, 6),
            "atr_multiple": mult,
            "measured_coverage": h.get("measured_coverage"),
            "checked_on": h.get("checked_on"),
        })
    if not levels:
        return None
    rates = [l["measured_coverage"] for l in levels
             if l["measured_coverage"] is not None]
    return {
        "basis": f"ATR{fc.get('atr_period', 14)}（{timeframe}）",
        "quantile": fc.get("quantile"),
        "measured_at": fc.get("measured_at"),
        "pairs": fc.get("pairs"),
        "coverage_low": min(rates) if rates else None,
        "coverage_high": max(rates) if rates else None,
        "levels": levels,
        "note": ("向きの予想ではありません。上下どちらに動くかは何も"
                 "言っていません。過去の値動きから、この先どれだけ"
                 "動きうるかの幅だけを出しています。"),
    }


@app.get("/api/strategies")
def list_strategies():
    """持っている戦略と、相場つきごとの割り当て。

    **画面が名前と説明を持たないようにする。** 画面側に日本語名を書くと、
    engine 側と食い違ったときに気づけない。名前・説明・割り当ては
    すべてここから返す。

    ``measured`` は実データでの検証状況。**「実装した」と「効くと
    分かった」は別のこと**なので、画面でも分けて出せるようにしておく。
    """
    cfg = get_trading_config()
    table = (cfg.filters.get("regime_strategy", {}) or {}).get("strategies", {})
    used: Dict[str, List[str]] = {}
    for regime, value in (table or {}).items():
        names = [value] if isinstance(value, str) else list(value or ())
        for i, n in enumerate(names):
            used.setdefault(str(n), []).append(f"{regime}({i + 1})")
    return {
        "version": __version__,
        "strategies": [
            {
                "name": st.name, "label": st.label, "why": st.why,
                "scoring": st.scoring,
                "regimes": used.get(st.name, []),
                "enabled": bool(used.get(st.name)),
            }
            for st in REGISTRY.values()
        ],
        "measured": MEASURED_NOTE,
    }


def _journal(results) -> None:
    """判断を残す。**残せなくても分析は止めない。**

    記録のために判断が出せなくなるのは本末転倒なので、失敗は警告に
    とどめる（``trade_logger.record`` の中で握っている）。

    BUY / SELL のときだけ計画も一緒に残す。**あとで結果と突き合わせる
    には、そのとき出していた損切りと利確が要る**（後から作り直すと、
    設定を変えた瞬間に「当時の判断」が変わってしまう）。
    """
    settings = get_settings()
    if not settings.journal_enabled:
        return
    cfg = get_trading_config()
    url = settings.resolved_database_url
    name = getattr(get_provider(), "name", "unknown")
    provider = get_provider()
    for res in results:
        plan = trade_logger.plan_for(res, cfg, provider)
        trade_logger.record(res, plan, database_url=url, provider=name)


@app.get("/api/journal")
def journal(limit: int = Query(50, ge=1, le=500),
            pair: Optional[str] = None):
    """画面に出た判断の記録。

    **残っているのは「こう判断した」という記録だけで、実際に建てたか
    どうかは含まない。** 同じ判断が続いているあいだは1行で、``seen_count``
    が増える。結果（outcome）は ``scripts/score_journal.py`` が後から埋める。
    """
    settings = get_settings()
    url = settings.resolved_database_url
    if pair:
        try:
            pair = get_trading_config().pair(pair).symbol
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "version": __version__,
        "enabled": settings.journal_enabled,
        "summary": trade_logger.summary(url),
        "entries": trade_logger.recent(url, limit=limit, pair=pair),
    }


@app.get("/api/board")
def board(pair: Optional[str] = None):
    """朝のボード。**予測はしない。事実を揃え、やめるべき時をはっきり言う。**

    既定は支援する銘柄だけ。``pair`` を渡すとその銘柄だけ出す（対象外の
    銘柄でも出せる。ただし費用の面で土俵に乗らないことは、除外の理由に出る）。
    """
    cfg = get_trading_config()
    provider = get_provider()
    if pair:
        try:
            targets = [cfg.pair(pair).symbol]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    else:
        targets = focus_mod.symbols(cfg, "focus") or [
            p.symbol for p in cfg.enabled_pairs()[:3]]
    boards = []
    for sym in targets:
        # 期限切れに落とさない。**ボードは信号ではなく場面を見せる。**
        b = build_board(cfg, provider, sym, analyze(sym, provider, cfg))
        boards.append(b)
        # **記録は朝の確認（scripts/morning_brief.py）だけで残す。** ブリーフを
        # 取り込んだ後の1枚を「その日のボード」にしたいので、画面を開いた
        # 時刻しだいで、取り込み前のボードが残ってしまわないようにする。
    return {"version": __version__, "boards": boards}


class FundamentalsIn(BaseModel):
    view: str
    note: str = ""
    source: str = ""


@app.post("/api/fundamentals/{pair}")
def set_fundamentals(pair: str, body: FundamentalsIn = Body(...)):
    """ファンダの見立てを書き換える。**機械は作らない。使う人が入れる。**

    日付はこちらで今日にする（手で書くと、古い見立てが新しい顔をする）。
    出典が空なら受け付けない。**誰の見立てか分からないものは使わない。**
    """
    cfg = get_trading_config()
    try:
        symbol = cfg.pair(pair).symbol
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if body.view not in ("up", "down", "neutral"):
        raise HTTPException(status_code=422,
                            detail="view は up / down / neutral のどれか")
    if not body.source.strip():
        raise HTTPException(status_code=422,
                            detail="出典（source）を書いてください")

    import json as _json

    path = board_mod.FUNDAMENTALS_PATH
    try:
        payload = _json.loads(path.read_text(encoding="utf-8"))             if path.exists() else {}
    except (OSError, ValueError):
        payload = {}
    views = payload.setdefault("views", {})
    now = datetime.now(timezone.utc)
    views[symbol] = {
        "view": body.view, "note": body.note.strip(),
        "source": body.source.strip(),
        # 日付は日本時間で。**朝8時に入れた見立てが「昨日」にならないように**
        "as_of": now.astimezone(brief_mod.JST).date().isoformat(),
        # ブリーフの取り込みが、これより古いブリーフで上書きしないための時刻
        "saved_at": now.isoformat(),
    }
    brief_mod.write_json(path, payload)
    return {"version": __version__, "pair": symbol, "saved": views[symbol]}


@app.get("/api/focus")
def focus():
    """どの銘柄を支援するか。

    **費用（スプレッド ÷ M15 の ATR）だけで分けている。** 勝てるかどうかで
    は分けていない。それは測れていない（docs/backtesting.md）。
    測っていなければ全銘柄が「未測定」で返る。**既定で支援対象にしない。**
    """
    return {"version": __version__, **focus_mod.as_dict(get_trading_config())}


@app.get("/api/morning-brief")
def morning_brief(format: str = Query("json", pattern="^(json|text)$")):
    """朝の確認。**今日この道具が使えるのか、から書く。**

    毎朝いちばん知りたいのは「今日、判断支援を受けられるのか」で、次が
    「受けられないなら何をすればいいか」。26銘柄の点数はその後。
    """
    cfg = get_trading_config()
    settings = get_settings()
    provider = get_provider()
    seen: List = []
    # 支援する銘柄だけ分析する。**対象外まで毎朝26銘柄計算しない。**
    targets = focus_mod.symbols(cfg, "focus") or None
    rank_pairs(provider, cfg, symbols=targets, on_result=seen.append)
    _journal(seen)
    boards = [build_board(cfg, provider, r.pair, r) for r in seen]
    brief = build_brief(cfg, getattr(provider, "name", "unknown"),
                        settings.resolved_database_url, results=seen,
                        boards=boards)
    if format == "text":
        return PlainTextResponse(brief_as_text(brief))
    return {"version": __version__, **brief}


@app.get("/api/data-status")
def data_status():
    """取り込んだ材料の鮮度。

    **取り込みは手動なのに、古くなったことが画面から分からなかった。**
    とくに経済指標の予定表は36時間で古くなり、そこから先は全銘柄が
    いきなり見送りになる。理由は各銘柄の詳細の奥にしか出ないので、
    「昨日まで動いていたのに今日は全部 NO_TRADE」という壊れ方に見える。
    """
    cfg = get_trading_config()
    provider = get_provider()
    return collect_data_status(cfg, getattr(provider, "name", "unknown"))


@app.get("/api/analysis/{pair}", response_model=AnalysisResult)
def analysis(pair: str) -> AnalysisResult:
    cfg = get_trading_config()
    try:
        cfg.pair(pair)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # analyze_pair は例外を外へ出さず NO_TRADE で返す。500 にしない。
    res = analyze(pair, get_provider(), cfg)
    # **返す直前にもう一度期限を見る。**
    # 作った時点では有効でも、返す時点で切れていることがある。
    # 切れた判断を BUY/SELL のまま外へ出さない。
    if res.is_expired():
        log.info("期限切れの判断を NO_TRADE に落としました", extra={
            "pair": res.pair, "reason_code": ReasonCode.ANALYSIS_EXPIRED})
        res = res.expired_view()
    # **画面に出したものを残す。** 期限切れに落とした後に残すのは、
    # 実際に利用者が見たのがこちらだから。
    _journal([res])
    return res


@app.get("/api/ranking", response_model=RankingResponse)
def ranking(limit: Optional[int] = Query(None, ge=1, le=100),
            tier: Optional[str] = Query(
                None, pattern="^(focus|watch|off|unknown)$")
            ) -> RankingResponse:
    """全銘柄を並べる。

    ``tier`` を渡すと、その区分だけ分析する。**26銘柄ぶん計算しない。**
    実勢データでは1銘柄あたりの読み込みが重く、全銘柄だと一覧の表示に
    数秒かかる。支援する2銘柄だけなら一瞬で済む。
    """
    cfg = get_trading_config()
    # **一覧に出したものも残す。** 詳細を開いた銘柄だけ記録すると、
    # 記録が「見た銘柄」に偏る。
    seen: List = []
    targets = focus_mod.symbols(cfg, tier) if tier else None
    if tier and not targets:
        # **区分を頼まれたのに該当が無いとき、勝手に全銘柄へ広げない。**
        return RankingResponse(
            version=__version__, generated_at=datetime.now(timezone.utc),
            provider=provider_status(), entries=[], analyzed=0, failed=0)
    res = rank_pairs(get_provider(), cfg, symbols=targets,
                     on_result=seen.append)
    _journal(seen)
    if limit:
        res.entries = res.entries[:limit]
    res.version = __version__
    return res


@app.get("/api/candles/{pair}")
def candles(
    pair: str,
    timeframe: str = Query("M15"),
    limit: int = Query(200, ge=20, le=1000),
):
    """チャート用の足。**表示のためだけの口で、判断には使わない。**

    指標も一緒に返す。画面側で計算し直すと、**同じ指標の実装が2つ**に
    なって静かに食い違う。計算はここ（engine）に1つだけ置く。
    """
    cfg = get_trading_config()
    try:
        spec = cfg.pair(pair)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if timeframe not in cfg.supported_timeframes():
        raise HTTPException(status_code=422,
                            detail=f"未対応の時間足です: {timeframe}")

    provider = get_provider()
    try:
        series = provider.get_candles(spec.symbol, timeframe, limit=limit)
    except MarketDataError as exc:
        log.warning("チャート用の足を取れません", extra={
            "pair": spec.symbol, "timeframe": timeframe,
            "reason": str(exc), "reason_code": ReasonCode.PROVIDER_ERROR})
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    ind = compute_all(series, cfg.indicators)
    # 支持帯・抵抗帯も返す。持ち合いの戦略が見ている「帯」を、画面でも
    # 同じものが見えるようにするため。**計算は analysis 側の1つを使う。**
    # ここで作り直すと、engine と画面で違う帯が出る。
    enrich_indicators(series, ind, cfg.indicators)

    def tail(key: str):
        arr = ind.get(key) or []
        return [None if v is None else round(float(v), 6) for v in arr]

    return {
        "version": __version__,
        "pair": spec.symbol,
        "timeframe": timeframe,
        "digits": spec.digits,
        "candles": [
            {"t": c.timestamp.isoformat(), "o": c.open, "h": c.high,
             "l": c.low, "c": c.close}
            for c in series.candles
        ],
        "overlays": {
            "ema20": tail("ema20"), "ema50": tail("ema50"),
            "ema200": tail("ema200"),
            "bb_upper": tail("bb_upper"), "bb_lower": tail("bb_lower"),
            "support": tail("support"), "resistance": tail("resistance"),
        },
        "sub": {
            "rsi14": tail("rsi14"),
            "macd": tail("macd"), "macd_signal": tail("macd_signal"),
            "macd_hist": tail("macd_hist"),
        },
        # 十字線に出す値。**画面側で計算し直さない。**
        # 同じ指標の実装が2つになると、静かに食い違う。
        "readout": {"atr14": tail("atr14"), "adx14": tail("adx14")},
        # この先の値動きの幅。**向きは予想しない。** 測っていなければ None。
        "forecast": _forecast_band(
            cfg, timeframe,
            series.candles[-1].close if series.candles else None,
            (ind.get("atr14") or [None])[-1]),
    }


class PlanCalcIn(BaseModel):
    """自分向けの計画の入力。**向きは使う人が選ぶ。基準価格は MT4 の bid・ask。**"""

    direction: str
    bid: str
    ask: str
    balance: Optional[float] = None
    risk_pct: Optional[float] = None


@app.post("/api/plan-calc/{pair}")
def plan_calc_endpoint(pair: str, body: PlanCalcIn = Body(...)):
    """建玉計画（Phase 2・新しい計算）。**発注はしない。**

    ボードが除外の日、手入力の値が古い・おかしい、費用込みの比が足りない、
    などの場合は ``NO_TRADE`` と理由コードを返す。PLAN_OK でも儲かる根拠ではない。
    """
    from . import plan_calc as pc
    from . import plan_inputs as pi

    cfg = get_trading_config()
    try:
        symbol = cfg.pair(pair).symbol
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if body.direction not in (pc.LONG, pc.SHORT):
        raise HTTPException(status_code=422, detail="direction は LONG か SHORT")
    try:
        bid, ask = pc.dec(body.bid.strip()), pc.dec(body.ask.strip())
    except Exception:                           # noqa: BLE001
        raise HTTPException(status_code=422, detail="bid・ask を数字で入れてください")
    if body.risk_pct is not None and not (0 < body.risk_pct <= 10):
        raise HTTPException(status_code=422, detail="risk_pct は 0〜10")
    if body.balance is not None and body.balance <= 0:
        raise HTTPException(status_code=422, detail="balance は正の数")

    provider = get_provider()
    now = datetime.now(timezone.utc)
    analysis = analyze(symbol, provider, cfg)
    try:
        m15 = provider.get_candles(symbol, "M15", limit=2)
    except Exception:                           # noqa: BLE001
        m15 = None
    board = build_board(cfg, provider, symbol, analysis, now)
    inputs = pi.build(cfg, symbol, body.direction, bid, ask, analysis, m15, board,
                      now=now, balance=body.balance, risk_pct=body.risk_pct)
    if inputs is None:
        decision = pc.PlanDecision(
            "NO_TRADE", [pc.Stop("CANDIDATE_UNAVAILABLE",
                                 "足や ATR が取れず、損切りの幅を決められません")],
            None, [pc.NO_EDGE_NOTE])
    else:
        decision = pc.decide(inputs, now)
    return {"version": __version__, "pair": symbol, "direction": body.direction,
            "board_state": (board.get("verdict") or {}).get("state"),
            **pi.decision_dict(decision, inputs)}


@app.get("/api/plan/{pair}")
def plan(
    pair: str,
    balance: Optional[float] = Query(None, gt=0),
    risk_pct: Optional[float] = Query(None, gt=0, le=10),
):
    """建玉計画（Phase 2）。Entry / SL / TP / Lot / 証拠金。

    **発注はしない。** 数字を出すだけで、注文は利用者が業者の画面で行う。
    分析が BUY/SELL でなければ計画は作らず、理由を返す。
    """
    cfg = get_trading_config()
    try:
        spec = cfg.pair(pair)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    provider = get_provider()
    # 一覧と同じ入口を通す。**詳細を開くたびに再計算しない。**
    res = analyze(spec.symbol, provider, cfg)
    if res.is_expired():
        res = res.expired_view()

    rates, missing = collect_rates(spec, provider, cfg,
                                   cfg.account.get("quote_currency", "JPY"))
    built = build_plan(res, spec, cfg, rates,
                       balance=balance, risk_pct=risk_pct)
    if missing:
        built.ok = False
        built.blocked_reasons.append(
            f"円換算に必要なレートが揃っていません（{', '.join(missing)}）。"
            f"いくらの損益になるか分からないため計画を出しません")
        log.warning("換算レート不足で計画を出しません", extra={
            "pair": spec.symbol, "reason": ",".join(missing),
            "reason_code": ReasonCode.INSUFFICIENT_DATA})

    return {
        "version": __version__,
        "analysis": {
            "signal": res.signal.value, "direction": res.direction.value,
            "score": res.score, "regime": res.regime.value,
            "valid_until": res.valid_until.isoformat() if res.valid_until else None,
        },
        "account": {
            "balance": balance or cfg.account["defaults"]["balance"],
            "risk_per_trade_pct": risk_pct
            or cfg.account["defaults"]["risk_per_trade_pct"],
            "leverage": cfg.account["leverage"],
            "margin_rate": cfg.account["margin_rate"],
            "broker": cfg.account["broker"],
            "unverified": cfg.account.get("unverified_note"),
        },
        "plan": built.as_dict(),
    }


@app.get("/api/provider/status", response_model=ProviderStatus)
def provider_status() -> ProviderStatus:
    return get_provider().provider_status()


@app.post("/api/dev/load-csv")
def dev_load_csv(req: CSVLoadRequest = Body(...)):
    """開発用。CSV が読めるかを確かめる。本番では使わない。"""
    if settings.env != "development":
        raise HTTPException(status_code=403, detail="開発環境でのみ使えます")
    provider = CSVMarketDataProvider(settings.csv_dir)
    try:
        series = provider.get_candles(req.pair, req.timeframe, req.limit)
    except KeyError as exc:
        # 銘柄・時間足が設定に無い。入力が不正なので 422。
        log.warning("CSV 取り込みの入力が不正です", extra={
            "pair": req.pair, "timeframe": req.timeframe,
            "reason_code": ReasonCode.CSV_INVALID_INPUT})
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MarketDataError as exc:
        # 置き場に無い・中身が壊れている。どちらも与えられた入力の問題。
        log.warning("CSV を取り込めません", extra={
            "pair": req.pair, "timeframe": req.timeframe,
            "reason_code": ReasonCode.CSV_UNREADABLE})
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "version": __version__,
        "pair": series.pair,
        "timeframe": series.timeframe,
        "candles": len(series),
        "first": series.candles[0].timestamp if series.candles else None,
        "last": series.last.timestamp if series.last else None,
        "rejected_rows": provider.last_errors[:20],
        "rejected_count": len(provider.last_errors),
    }
