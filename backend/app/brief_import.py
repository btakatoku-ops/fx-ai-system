# -*- coding: utf-8 -*-
"""FXモーニングブリーフ（claude.ai のアーティファクト）を読み込む。

平日の朝8時にブリーフが更新される。その「方向性ボード」から、銘柄ごとに

- ブリーフの判定（▲▼■・対立・除外）
- 4つのシグナル（トレンド・モメンタム・ファンダ・レンジ位置）

を取り出す。**ファンダはこのアプリが作らない材料なので、ブリーフの見立てを
出典つきで使う。** トレンドとモメンタムはアプリが自分の足で出すので、
ブリーフの値は「突き合わせ用」に並べるだけで、判定には使わない。

## 決まり

1. **形が合わなければ何も取り込まない。** 方向性ボードが無い日（形式の違う
   版）や、印と色が食い違うシグナルは、推測で埋めない。
2. **日付はブリーフに書いてある日付。** 取り込んだ日ではない。古いブリーフを
   今日の見立てとして使わない。
3. **手で入れた見立てを上書きしない。** ブリーフより後に手で入れたものが
   あれば、そちらを残す。
4. ブリーフは「発注」と書くことがあるが、**このアプリの言葉にはしない。**
   判定は向き（上・下・中立・対立）と、除外・見送りかどうかに直して持つ。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional

JST = timezone(timedelta(hours=9))
SOURCE_PREFIX = "FXモーニングブリーフ"

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}

# シグナルの色（class）と印。**両方が合っているときだけ採る。**
_SIG = {"up": ("up", "▲"), "dn": ("down", "▼"), "fl": ("neutral", "■")}
_SIG_KEY = {"トレンド": "trend", "モメンタム": "momentum",
            "ファンダ": "fundamentals", "レンジ位置": "range"}
_VERDICT = {"v-up": "up", "v-dn": "down", "v-fl": "neutral", "v-cf": "conflict"}


class BriefFormatError(ValueError):
    """ブリーフの形が想定と違う。**取り込まない。**"""


# ------------------------------------------------------------ 小さな DOM


@dataclass
class _Node:
    tag: str
    classes: List[str]
    children: List["_Node"] = field(default_factory=list)
    parts: List[object] = field(default_factory=list)   # 文字と子の並び

    def text(self) -> str:
        out = []
        for p in self.parts:
            out.append(p.text() if isinstance(p, _Node) else p)
        return re.sub(r"\s+", " ", "".join(out)).strip()

    def find_all(self, cls: str) -> List["_Node"]:
        found = []
        for c in self.children:
            if cls in c.classes:
                found.append(c)
            found.extend(c.find_all(cls))
        return found

    def find(self, cls: str) -> Optional["_Node"]:
        got = self.find_all(cls)
        return got[0] if got else None


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", [])
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        cls = (dict(attrs).get("class") or "").split()
        node = _Node(tag, cls)
        parent = self.stack[-1]
        parent.children.append(node)
        parent.parts.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        # 閉じ忘れに強くする: 同じ名前の開きまで戻る
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if self.stack[-1].tag not in ("style", "script"):
            self.stack[-1].parts.append(data)


# ------------------------------------------------------------ 読み取り


def _pair_symbol(text: str) -> Optional[str]:
    s = re.sub(r"[^A-Z]", "", text.upper())
    return s if len(s) == 6 else None


def _brief_datetime(root: _Node) -> datetime:
    node = root.find("mast-date")
    if node is None:
        raise BriefFormatError("ブリーフの日付（mast-date）が見つかりません")
    t = node.text()
    m = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", t)
    if not m:
        raise BriefFormatError(f"ブリーフの日付を読めません: {t!r}")
    hm = re.search(r"(\d{1,2}):(\d{2})", t)
    hh, mm = (int(hm.group(1)), int(hm.group(2))) if hm else (8, 0)
    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                    hh, mm, tzinfo=JST)


def _signal(cell: _Node) -> Optional[Dict]:
    k, s, d = cell.find("k"), cell.find("s"), cell.find("d")
    if k is None or s is None:
        return None
    key = _SIG_KEY.get(k.text())
    if key is None:
        return None
    kind = next((c for c in s.classes if c in _SIG), None)
    if kind is None:
        return None
    view, mark = _SIG[kind]
    if s.text() != mark:
        return None                      # 色と印が食い違う。推測しない
    return {"key": key, "view": view, "mark": mark,
            "text": d.text() if d is not None else ""}


def parse(html: str) -> Dict:
    """ブリーフの HTML から方向性ボードを取り出す。

    方向性ボードが1行も読めなければ ``BriefFormatError``。
    """
    b = _Builder()
    b.feed(html)
    root = b.root
    at = _brief_datetime(root)

    pairs: Dict[str, Dict] = {}
    for row in root.find_all("db-row"):
        head = row.find("db-head")
        pn = row.find("db-pair")
        vd = row.find("verdict")
        if head is None or pn is None or vd is None:
            continue
        sym = _pair_symbol(pn.text())
        if sym is None:
            continue
        lean = next((_VERDICT[c] for c in vd.classes if c in _VERDICT), None)
        raw = vd.text()
        # 見出しの3つ目（要約）
        spans = [c for c in head.children if c.tag == "span"]
        summary = spans[2].text() if len(spans) >= 3 else ""
        signals: Dict[str, Dict] = {}
        sig = row.find("sig")
        if sig is not None:
            for cell in sig.children:
                got = _signal(cell)
                if got:
                    signals[got.pop("key")] = got
        pairs[sym] = {
            "lean": lean,
            "excluded": bool(re.search(r"除外|見送り", raw)),
            "verdict_raw": raw,
            "summary": summary,
            "signals": signals,
        }
    if not pairs:
        raise BriefFormatError(
            "方向性ボードが見つかりません（この版のブリーフには無いか、形式が変わりました）")
    return {"brief_at": at.isoformat(), "brief_date": at.date().isoformat(),
            "pairs": pairs}


def pick_newest(docs: List[Dict]) -> Optional[Dict]:
    return max(docs, key=lambda d: d["brief_at"]) if docs else None


# ------------------------------------------------------------ 反映


def _saved_at(row: Dict) -> Optional[datetime]:
    """見立てを入れた時刻。無ければ as_of の日の終わりとみなす（控えめに）。"""
    for key in ("saved_at",):
        try:
            v = datetime.fromisoformat(str(row[key]))
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError):
            pass
    try:
        d = date.fromisoformat(str(row.get("as_of")))
        return datetime(d.year, d.month, d.day, 23, 59, tzinfo=JST)
    except (TypeError, ValueError):
        return None


def merge_fundamentals(views: Dict[str, Dict], doc: Dict, url: str,
                       known: List[str]) -> Dict[str, str]:
    """ブリーフのファンダを見立てに反映する。銘柄ごとの結果を返す。

    **手で入れた見立てが、ブリーフより後なら残す。**
    """
    at = datetime.fromisoformat(doc["brief_at"])
    label = f"{SOURCE_PREFIX} {at:%m/%d %H:%M} JST"
    result: Dict[str, str] = {}
    for sym, p in doc["pairs"].items():
        if sym not in known:
            result[sym] = "このアプリの対象外"
            continue
        fund = p["signals"].get("fundamentals")
        if not fund:
            result[sym] = "ファンダの欄が読めないので取り込みません"
            continue
        cur = views.get(sym) or {}
        manual = cur and not str(cur.get("source", "")).startswith(SOURCE_PREFIX)
        cur_at = _saved_at(cur) if cur else None
        if manual and cur_at and cur_at > at:
            result[sym] = f"手で入れた見立て（{cur.get('as_of')}）の方が新しいので残します"
            continue
        views[sym] = {
            "view": fund["view"],
            "note": fund["text"],
            "source": label,
            "as_of": doc["brief_date"],
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "url": url,
        }
        result[sym] = f"取り込みました（{fund['mark']}）"
    return result


def write_json(path: Path, data: Dict) -> None:
    """途中で落ちても壊れたファイルを残さない。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------ ボードに添える


BRIEFS_DIR = Path(__file__).resolve().parents[2] / "data" / "briefs"
LATEST = "latest.json"


def load_latest(path: Optional[Path] = None) -> Optional[Dict]:
    p = Path(path) if path else BRIEFS_DIR / LATEST
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def for_pair(doc: Optional[Dict], symbol: str, now: datetime) -> Optional[Dict]:
    """ボードに添えるブリーフの判定。**何日前のものかを必ず付ける。**"""
    if not doc:
        return None
    p = (doc.get("pairs") or {}).get(symbol)
    if not p:
        return None
    try:
        d = date.fromisoformat(doc["brief_date"])
        age = (now.astimezone(JST).date() - d).days
    except (KeyError, TypeError, ValueError):
        age = None
    return {
        "brief_at": doc.get("brief_at"),
        "brief_date": doc.get("brief_date"),
        "age_days": age,
        "url": doc.get("url"),
        "lean": p.get("lean"),
        "excluded": p.get("excluded", False),
        "summary": p.get("summary", ""),
        "signals": p.get("signals", {}),
    }
