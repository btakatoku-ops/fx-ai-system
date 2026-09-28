# -*- coding: utf-8 -*-
"""朝のボードを静的ページにして GitHub Pages に出す。

外（モバイル回線）から朝のボードを見るため。**ページは誰でも見られる。**

1. 支援する銘柄のボードを、実勢データ（data/real）で作る
2. 1枚の HTML にする（backend/app/board_page.py）
3. ``gh-pages`` ブランチ（作業場所は .pages/）に置いて送る

使い方::

    set FX_MARKET_DATA_PROVIDER=csv
    set FX_CSV_DATA_DIR=./data/real
    .venv/Scripts/python.exe scripts/publish_board_page.py            # 作って送る
    .venv/Scripts/python.exe scripts/publish_board_page.py --out x.html --no-push   # 手元に書くだけ

ページの場所: https://btakatoku-ops.github.io/fx-ai-system/
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import focus as focus_mod                             # noqa: E402
from app.analysis import analyze_pair                          # noqa: E402
from app.board import build_board                              # noqa: E402
from app.board_page import JST, render                         # noqa: E402
from app.config import get_settings, get_trading_config        # noqa: E402
from app.market_data import build_provider                     # noqa: E402

PAGES_DIR = ROOT / ".pages"
BRANCH = "gh-pages"


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), check=check,
                          capture_output=True, text=True, encoding="utf-8")


def ensure_worktree() -> None:
    """``.pages/`` に gh-pages の作業場所を用意する（無ければ作る）。"""
    if (PAGES_DIR / ".git").exists():
        git("pull", "--ff-only", "origin", BRANCH, cwd=PAGES_DIR, check=False)
        return
    git("worktree", "prune")
    remote = git("ls-remote", "--heads", "origin", BRANCH, check=False).stdout.strip()
    if remote:
        git("fetch", "origin", f"{BRANCH}:{BRANCH}", check=False)
        git("worktree", "add", str(PAGES_DIR), BRANCH)
    else:
        git("worktree", "add", "--orphan", "-b", BRANCH, str(PAGES_DIR))


def build_html(cfg) -> str:
    provider = build_provider(get_settings())
    targets: List[str] = focus_mod.symbols(cfg, "focus") or [
        p.symbol for p in cfg.enabled_pairs()[:3]]
    boards = []
    for sym in targets:
        try:
            boards.append(build_board(cfg, provider, sym, analyze_pair(sym, provider, cfg)))
        except Exception as exc:                  # noqa: BLE001
            print(f"  {sym}: ボードを作れません: {exc}")
    note = "" if getattr(provider, "name", "") != "mock" else "（合成データです。実勢ではありません）"
    return render(boards, datetime.now(timezone.utc), note=note)


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", help="手元に書く場所（--no-push と一緒に使う）")
    ap.add_argument("--no-push", action="store_true", help="送らない")
    a = ap.parse_args(argv)

    cfg = get_trading_config()
    html = build_html(cfg)
    if a.no_push:
        out = Path(a.out or ROOT / "board_page.html")
        out.write_text(html, encoding="utf-8")
        print(f"書きました: {out}")
        return 0

    ensure_worktree()
    (PAGES_DIR / "index.html").write_text(html, encoding="utf-8")
    (PAGES_DIR / ".nojekyll").write_text("", encoding="utf-8")
    git("add", "-A", cwd=PAGES_DIR)
    if not git("status", "--porcelain", cwd=PAGES_DIR).stdout.strip():
        print("変わったところがないので送りません。")
        return 0
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    git("commit", "-q", "-m", f"朝のボード {stamp} JST\n\n"
        "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>", cwd=PAGES_DIR)
    got = git("push", "-u", "origin", BRANCH, cwd=PAGES_DIR, check=False)
    if got.returncode != 0:
        print(f"送れませんでした: {got.stderr.strip()}")
        return 1
    print(f"送りました（{stamp} JST）: https://btakatoku-ops.github.io/fx-ai-system/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
