# -*- coding: utf-8 -*-
"""notification_engine — 後の段階で実装する。

Phase 1 では**枠だけ**を置く。動くふりをさせない。呼ばれたら
NotImplementedError を投げて、未実装だとはっきり分かるようにする。

中途半端に動くものを置くと、繋がっていないのに繋がったように見えて、
その状態で数字を信用してしまう。
"""
from __future__ import annotations

PHASE = 2
NOT_READY = "notification_engine は Phase 1 では未実装です"


def not_implemented(*args, **kwargs):
    raise NotImplementedError(NOT_READY)


def notify(event):
    """通知を送る。Phase 2。"""
    not_implemented()
