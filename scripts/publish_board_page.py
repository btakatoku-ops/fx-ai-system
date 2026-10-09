# -*- coding: utf-8 -*-
"""ボードを静的ページにして GitHub Pages に出す（計画の計算つき）。

外（モバイル回線）からボードを見て、計画まで出すため。**ページは誰でも見られる。**

**PC のサーバーにつながらなくても使えるようにする**（2026-10-01）。携帯は PC ではなく
GitHub Pages を見る。PC は計算して送るだけ。朝 8:40 と、取引時間中は15分ごとに送る。
gh-pages は**1つのコミットを上書きし続ける**（15分ごとに履歴を積み上げない）。

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
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import dashboard_page                                 # noqa: E402
from app.board_page import JST                                 # noqa: E402
from app.config import get_settings, get_trading_config        # noqa: E402
from app.market_data import build_provider                     # noqa: E402

PAGES_DIR = ROOT / ".pages"
PUBLIC_DIR = ROOT / "frontend" / "public"

# ホーム画面に置いたときの入口。**開く先をこのページに固定する。**
# 以前は PC（192.168.0.5）を指すアイコンしか無く、家の外では開けなかった。
MANIFEST = {
    "name": "FX ボード", "short_name": "FX ボード", "lang": "ja",
    "description": "朝のボードと自分向けの計画。予測ではない。発注はしない。",
    "start_url": "./", "scope": "./", "display": "standalone",
    "background_color": "#0f1217", "theme_color": "#0f1217",
    "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"}],
}

# 間違ったパス（大文字・打ち間違い・古いブックマーク）でもボードへ戻す。
# GitHub Pages はパスの大文字小文字を区別し、違えば 404 になる（2026-10-01 に携帯で発生）。
NOT_FOUND = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="robots" content="noindex,nofollow">
<meta http-equiv="refresh" content="0; url=/fx-ai-system/">
<title>FX ボードへ移動します</title></head>
<body><p>FX ボードへ移動します。移動しない場合は
<a href="/fx-ai-system/">こちら</a>を開いてください。</p>
<script>location.replace('/fx-ai-system/');</script></body></html>
"""
BRANCH = "gh-pages"


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), check=check,
                          capture_output=True, text=True, encoding="utf-8")


def ensure_worktree() -> None:
    """``.pages/`` に gh-pages の作業場所を用意する（無ければ作る）。"""
    if (PAGES_DIR / ".git").exists():
        return                           # 上書きして強制送信するので、取り込み直さない
    git("worktree", "prune")
    remote = git("ls-remote", "--heads", "origin", BRANCH, check=False).stdout.strip()
    if remote:
        git("fetch", "origin", f"{BRANCH}:{BRANCH}", check=False)
        git("worktree", "add", str(PAGES_DIR), BRANCH)
    else:
        git("worktree", "add", "--orphan", "-b", BRANCH, str(PAGES_DIR))


def build_html(cfg) -> str:
    """作り方は PC の画面（GET /dashboard）と同じ（app/dashboard_page.py）。"""
    return dashboard_page.build(cfg, build_provider(get_settings()),
                                get_settings().resolved_database_url)


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
    (PAGES_DIR / "404.html").write_text(NOT_FOUND, encoding="utf-8")
    import json as _json
    (PAGES_DIR / "manifest.webmanifest").write_text(
        _json.dumps(MANIFEST, ensure_ascii=False, indent=2), encoding="utf-8")
    import shutil as _shutil
    for name in ("icon-192.png", "icon-512.png", "apple-touch-icon.png"):
        src = PUBLIC_DIR / name
        if src.exists():
            _shutil.copyfile(src, PAGES_DIR / name)
    git("add", "-A", cwd=PAGES_DIR)
    if not git("status", "--porcelain", cwd=PAGES_DIR).stdout.strip():
        print("変わったところがないので送りません。")
        return 0
    stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    msg = f"ボード {stamp} JST\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
    # **1つのコミットに畳んで上書きする。** 15分ごとに履歴を積むと、リポジトリが膨らむ
    has_head = git("rev-parse", "--verify", "HEAD", cwd=PAGES_DIR, check=False).returncode == 0
    if has_head:
        root = git("rev-list", "--max-parents=0", "HEAD", cwd=PAGES_DIR).stdout.split()[0]
        git("reset", "-q", "--soft", root, cwd=PAGES_DIR)
        git("commit", "-q", "--amend", "-m", msg, cwd=PAGES_DIR)
    else:
        git("commit", "-q", "-m", msg, cwd=PAGES_DIR)
    got = git("push", "-q", "--force", "-u", "origin", BRANCH, cwd=PAGES_DIR, check=False)
    if got.returncode != 0:
        print(f"送れませんでした: {got.stderr.strip()}")
        return 1
    print(f"送りました（{stamp} JST）: https://btakatoku-ops.github.io/fx-ai-system/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
