# -*- coding: utf-8 -*-
"""ホーム画面に置くアイコンを作る。

**取り込んだ画像を置かない。** どこから来たか分からない画像を同梱すると、
権利も中身も追えなくなる。ここで描いて書き出す。

絵は単純に、ローソク足2本（上げと下げ）。**銘柄名も数字も入れない。**
アイコンに数字を入れると、古い値がホーム画面に残り続ける。

Pillow などは使わず、標準ライブラリだけで PNG を組み立てている。
依存を1つ増やすより、80行書くほうが後々楽なため。

使い方::

    .venv/Scripts/python.exe scripts/make_icons.py
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path
from typing import List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend" / "public"

BG = (15, 18, 23)          # --bg
PANEL = (23, 27, 34)       # --panel
UP = (47, 179, 122)        # --buy
DOWN = (224, 87, 95)       # --sell
LINE = (42, 49, 61)        # --line

Color = Tuple[int, int, int]


def _png(width: int, height: int, rows: Sequence[Sequence[Color]]) -> bytes:
    """RGB の並びから PNG を組み立てる。"""
    raw = bytearray()
    for row in rows:
        raw.append(0)                       # フィルタなし
        for r, g, b in row:
            raw += bytes((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def draw(size: int) -> bytes:
    """1枚描く。座標はすべて size に対する割合で決める。"""
    px: List[List[Color]] = [[BG for _ in range(size)] for _ in range(size)]

    def rect(x0: float, y0: float, x1: float, y1: float, color: Color) -> None:
        for y in range(max(0, int(y0 * size)), min(size, int(y1 * size))):
            row = px[y]
            for x in range(max(0, int(x0 * size)), min(size, int(x1 * size))):
                row[x] = color

    # 下地。**角は丸めない。** iOS が自分で丸めるので、こちらで丸めると
    # 二重に欠ける。
    rect(0.0, 0.0, 1.0, 1.0, PANEL)
    rect(0.06, 0.06, 0.94, 0.94, BG)

    # 目盛りの線を2本だけ（チャートに見えれば十分）
    for y in (0.36, 0.64):
        rect(0.14, y, 0.86, y + 0.006, LINE)

    # 上げの足
    rect(0.315, 0.20, 0.335, 0.74, UP)          # ひげ
    rect(0.26, 0.30, 0.39, 0.64, UP)            # 実体
    # 下げの足
    rect(0.655, 0.26, 0.675, 0.80, DOWN)
    rect(0.60, 0.40, 0.73, 0.70, DOWN)

    return _png(size, size, px)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    OUT.mkdir(parents=True, exist_ok=True)
    # 180 は iOS のホーム画面、192/512 は manifest 用
    for name, size in (("apple-touch-icon.png", 180),
                       ("icon-192.png", 192),
                       ("icon-512.png", 512)):
        path = OUT / name
        path.write_bytes(draw(size))
        print(f"  {name}  {size}x{size}  {path.stat().st_size:,} bytes")
    print(f"書き出しました: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
