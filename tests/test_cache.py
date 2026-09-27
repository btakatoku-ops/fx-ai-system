# -*- coding: utf-8 -*-
"""分析の使い回しの試験。

**速さのために安全を緩めていないこと**を確かめる。
期限切れを返す・時計の巻き戻しを許す、のどちらも起きてはいけない。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone


from app.analysis_service import analyze, get_cache, reset_cache
from app.cache import ResultCache
from app.market_data import MockMarketDataProvider
from app.models import Signal

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


class _Value:
    """期限を持つ結果の代わり。"""

    def __init__(self, tag: str, valid_until=None):
        self.tag = tag
        self.valid_until = valid_until

    def is_expired(self, now):
        return self.valid_until is not None and now >= self.valid_until


# ============================================================ 置き場の性質


def test_a_fresh_entry_is_reused():
    c = ResultCache(ttl_seconds=10)
    c.put("k", _Value("a"), NOW)
    got = c.get("k", NOW + timedelta(seconds=5))
    assert got is not None and got.tag == "a"


def test_an_entry_past_the_ttl_is_not_reused():
    c = ResultCache(ttl_seconds=10)
    c.put("k", _Value("a"), NOW)
    assert c.get("k", NOW + timedelta(seconds=10)) is None
    assert c.get("k", NOW + timedelta(seconds=11)) is None


def test_an_expired_result_is_never_served(cfg):
    """**TTL 内でも、結果自身の期限が切れていれば返さない。**

    速さのために古い BUY を残すのは、この仕組みでいちばんやってはいけない。
    """
    c = ResultCache(ttl_seconds=60)
    c.put("k", _Value("a", valid_until=NOW + timedelta(seconds=5)), NOW)
    assert c.get("k", NOW + timedelta(seconds=3)) is not None
    assert c.get("k", NOW + timedelta(seconds=6)) is None
    assert c.stats.expired == 1


def test_a_clock_rollback_invalidates_the_entry():
    """12:00 の判断を作ったあと時計を 11:59 に戻しても使い回さない。

    戻せば、未来のデータで作った判断が「まだ新しい」として通ってしまう。
    """
    c = ResultCache(ttl_seconds=60)
    c.put("k", _Value("a"), NOW)
    assert c.get("k", NOW - timedelta(seconds=1)) is None
    assert c.stats.rollback == 1
    # 捨てたので、時計が戻る前の時刻でも残っていない
    assert c.get("k", NOW + timedelta(seconds=1)) is None


def test_zero_ttl_means_no_reuse():
    c = ResultCache(ttl_seconds=0)
    c.put("k", _Value("a"), NOW)
    assert c.get("k", NOW) is None


def test_concurrent_requests_for_one_key_compute_once():
    """**同じ銘柄の同時要求を1本にまとめる。**

    まとめないと、一覧の更新中に詳細を開いたときに同じ計算が何本も
    並走して全部が遅くなる。
    """
    c = ResultCache(ttl_seconds=60)
    calls = []
    started = threading.Event()

    def slow():
        calls.append(1)
        started.set()
        time.sleep(0.25)
        return _Value("x")

    results = []
    threads = [threading.Thread(
        target=lambda: results.append(c.get_or_compute("k", slow)))
        for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1, f"{len(calls)} 回計算しています"
    assert len(results) == 6
    assert all(r.tag == "x" for r in results)


def test_different_keys_do_not_block_each_other():
    c = ResultCache(ttl_seconds=60)
    order = []

    def make(tag, delay):
        def fn():
            time.sleep(delay)
            order.append(tag)
            return _Value(tag)
        return fn

    ts = [threading.Thread(target=c.get_or_compute, args=("slow", make("slow", 0.2))),
          threading.Thread(target=c.get_or_compute, args=("fast", make("fast", 0.0)))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert order[0] == "fast", "別の銘柄の計算を待たされています"


# ============================================================ 分析での使い回し


def test_analysis_is_reused_within_the_ttl(cfg):
    reset_cache()
    p = MockMarketDataProvider()
    first = analyze("AUDJPY", p, cfg)
    second = analyze("AUDJPY", p, cfg)
    assert second is first          # 同じ物が返る
    assert get_cache(cfg).stats.misses == 1


def test_an_explicit_now_bypasses_the_cache(cfg):
    """検証など、過去の時刻で呼んだ結果を「いま」のものとして残さない。"""
    reset_cache()
    p = MockMarketDataProvider()
    a = analyze("AUDJPY", p, cfg, now=NOW)
    b = analyze("AUDJPY", p, cfg, now=NOW)
    assert a is not b
    assert get_cache(cfg).stats.misses == 0


def test_hard_filters_survive_the_cache(cfg):
    """使い回しても、強制条件と理由はそのまま残ること。

    寿命を長めにした設定で確かめる。既定の10秒だと26銘柄を2巡する間に
    寿命が切れ、2巡目は計算し直しになる。そのときは足が進んでいるので
    結果が変わりうる——それは正常で、**確かめたいのは「使い回した結果が
    書き換わらないこと」**。
    """
    long_ttl = cfg.model_copy(update={
        "filters": {**cfg.filters,
                    "freshness": {**cfg.filters["freshness"],
                                  "cache_ttl_seconds": 600}}})
    reset_cache()
    p = MockMarketDataProvider()
    first = [analyze(s.symbol, p, long_ttl) for s in long_ttl.enabled_pairs()]
    again = [analyze(s.symbol, p, long_ttl) for s in long_ttl.enabled_pairs()]
    assert get_cache(long_ttl).stats.hits >= len(first)
    for a, b in zip(first, again):
        assert a is b                      # 同じ物が返る
        assert a.signal is b.signal
        assert a.hard_blocked == b.hard_blocked
        assert a.invalidation_reasons == b.invalidation_reasons
    reset_cache()


def test_ranking_and_detail_share_one_computation(cfg):
    """一覧を出した直後の詳細は、計算し直さないこと。

    寿命を長めにした設定で確かめる。既定の10秒のままだと、負荷の高い
    実行では一覧の途中で先頭銘柄の寿命が切れ、**試験が時間に左右される**。
    確かめたいのは「共有されること」であって、寿命の長さではない。
    """
    from app.pair_ranker import rank_pairs

    long_ttl = cfg.model_copy(update={
        "filters": {**cfg.filters,
                    "freshness": {**cfg.filters["freshness"],
                                  "cache_ttl_seconds": 600}}})
    reset_cache()
    p = MockMarketDataProvider()
    rank_pairs(p, long_ttl)
    misses_after_ranking = get_cache(long_ttl).stats.misses
    assert misses_after_ranking == len(long_ttl.enabled_pairs())

    analyze("AUDJPY", p, long_ttl)
    assert get_cache(long_ttl).stats.misses == misses_after_ranking
    assert get_cache(long_ttl).stats.hits >= 1
    reset_cache()


def test_cache_ttl_does_not_exceed_the_analysis_ttl(cfg):
    """使い回しの寿命が、判断そのものの寿命を超えない。"""
    fresh = cfg.filters["freshness"]
    assert fresh["cache_ttl_seconds"] <= fresh["analysis_ttl_seconds"]


def test_two_providers_with_the_same_name_do_not_share_results(cfg):
    """**名前が同じでも別の供給元なら結果を分ける。**

    名前だけで束ねると、壊れた供給元が正常な結果を受け取ってしまう。
    置き場の違う CSV 供給元どうしでも同じ問題が起きる。
    """
    reset_cache()

    class _Broken(MockMarketDataProvider):
        def get_candles(self, pair, timeframe, limit=300):
            if pair == "EURUSD":
                raise RuntimeError("この銘柄だけ壊れている")
            return super().get_candles(pair, timeframe, limit)

    healthy = MockMarketDataProvider()
    broken = _Broken()
    assert healthy.name == broken.name          # 名前は同じ

    ok = analyze("EURUSD", healthy, cfg)
    ng = analyze("EURUSD", broken, cfg)
    assert ng.signal is Signal.NO_TRADE
    assert ng.hard_blocked is True
    assert ok is not ng
