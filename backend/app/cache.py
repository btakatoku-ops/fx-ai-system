# -*- coding: utf-8 -*-
"""分析結果の使い回し。

同じ銘柄を同じ瞬間に何度も計算し直さないための層。**安全の条件は
一切緩めない。** 緩めれば、速いだけで間違った画面になる。

守っていること。

1. **期限切れは絶対に返さない。** ``valid_until`` を過ぎた結果は捨てる。
   速さのために古い BUY を残すのは、この仕組みでいちばんやってはいけない。
2. **時計が巻き戻ったら使わない。** 経過が負の記録は捨てる。
   12:00 の BUY を作ったあと時計を 11:59 に戻すと、未来のデータで作った
   判断を「まだ新しい」として使い回せてしまう。``0 <= 経過 < TTL`` を条件にする。
3. **同じ銘柄の同時要求は1回にまとめる。** 26銘柄の一覧を開いている最中に
   詳細を開くと、同じ計算が何本も並走して全部が遅くなる。銘柄ごとの錠で、
   先に走っている計算の結果を待つ。
4. **結果はそのまま保つ。** 強制条件も理由も期限も、計算したときのまま。
   キャッシュは「いつ計算したか」を足すだけで、中身を書き換えない。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, Generic, Optional, Tuple, TypeVar

T = TypeVar("T")


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    expired: int = 0
    rollback: int = 0
    coalesced: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {"hits": self.hits, "misses": self.misses,
                "expired": self.expired, "rollback": self.rollback,
                "coalesced": self.coalesced}


class ResultCache(Generic[T]):
    """鍵ごとに1件だけ持つ、寿命つきの置き場。

    ``is_expired`` を持つ結果なら、それも見る。TTL を過ぎていなくても
    結果自身が期限切れなら使わない。
    """

    def __init__(self, ttl_seconds: float = 10.0) -> None:
        self.ttl = float(ttl_seconds)
        self._entries: Dict[str, Tuple[datetime, T]] = {}
        self._lock = threading.Lock()
        self._key_locks: Dict[str, threading.Lock] = {}
        self.stats = CacheStats()

    # ---------------------------------------------------------------- 内部

    def _key_lock(self, key: str) -> threading.Lock:
        with self._lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock

    def _usable(self, key: str, now: datetime) -> Optional[T]:
        """使ってよい記録だけを返す。判断はここに集める。"""
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return None
        computed_at, value = entry

        age = (now - computed_at).total_seconds()
        if age < 0:
            # 時計が巻き戻った。未来に作った判断を「新しい」として使わない。
            self.stats.rollback += 1
            self.invalidate(key)
            return None
        if age >= self.ttl:
            return None

        is_expired = getattr(value, "is_expired", None)
        if callable(is_expired) and is_expired(now):
            # TTL 内でも、結果そのものの期限が切れていれば使わない。
            self.stats.expired += 1
            self.invalidate(key)
            return None
        return value

    # ---------------------------------------------------------------- 公開

    def get(self, key: str, now: Optional[datetime] = None) -> Optional[T]:
        return self._usable(key, now or datetime.now(timezone.utc))

    def put(self, key: str, value: T,
            now: Optional[datetime] = None) -> None:
        with self._lock:
            self._entries[key] = (now or datetime.now(timezone.utc), value)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._key_locks.clear()
        self.stats = CacheStats()

    def get_or_compute(self, key: str, compute: Callable[[], T],
                       now: Optional[datetime] = None) -> T:
        """あれば使い、無ければ計算する。

        **同じ鍵の同時要求は1本にまとめる。** 26銘柄の一覧を開いている
        最中に詳細を開くと、同じ計算が何本も並走して全部が遅くなる。
        """
        now = now or datetime.now(timezone.utc)
        hit = self._usable(key, now)
        if hit is not None:
            self.stats.hits += 1
            return hit

        lock = self._key_lock(key)
        contended = not lock.acquire(blocking=False)
        if contended:
            lock.acquire()          # 先に走っている計算を待つ
        try:
            # 待っている間に他が入れているかもしれない
            again = self._usable(key, datetime.now(timezone.utc))
            if again is not None:
                if contended:
                    self.stats.coalesced += 1
                else:
                    self.stats.hits += 1
                return again

            self.stats.misses += 1
            value = compute()
            self.put(key, value, datetime.now(timezone.utc))
            return value
        finally:
            lock.release()
