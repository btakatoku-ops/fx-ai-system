# 市場データ

## 原則

**市場データを作らない。** ブローカーの API・エンドポイント・価格・
スプレッドを推測で書くことは、この段階でいちばんやってはいけない。
実データが無いなら、無いと分かる形にする。

そのために供給元を抽象にして、実装を差し替えられるようにしてある。
合成データを使っているときは、画面に**「合成データで、実勢の値ではない」**と
出す。数字が本物に見えてしまうのが最大の危険だから。

## 抽象

`backend/app/market_data.py` の `MarketDataProvider`（ABC）。
実装はこの4つを持つ。

| メソッド | 返すもの |
|---|---|
| `get_latest_price(pair)` | `Quote`（bid / ask / 時刻） |
| `get_candles(pair, timeframe, limit)` | `CandleSeries` |
| `get_spread(pair)` | `SpreadInfo`（pips と価格差） |
| `provider_status()` | 名前・状態・直近の失敗 |

上の層は `MarketDataProvider` としか話さない。どの供給元かを知らない。

## 実装

| 名前 | 環境変数の値 | 状態 |
|---|---|---|
| Mock | `mock` | 動く。合成データ |
| CSV | `csv` | 動く。同梱の CSV を読む |
| MT4 | `mt4` | **未実装。呼ぶと `NotImplementedError`** |
| 外部 API | `external_api` | **未実装** |
| ブローカー | `future_broker` | **未実装** |

未実装のものは `NotImplementedError` を投げる。黙って空を返したり、
それらしい値を作ったりはしない。**中途半端に動くより、無いと分かるほうが安全。**

切り替えは環境変数だけ。

```
FX_MARKET_DATA_PROVIDER=mock   # または csv
```

## Mock（合成データ）

試験と画面確認のためのもの。**実勢の値ではない。**

場面は8通り。

`strong_uptrend` / `strong_downtrend` / `range` / `breakout` /
`high_volatility` / `low_volatility` / `trend_pullback` / `downtrend_pullback`

ペアごとにどの場面を割り当てるかは固定してある。同じペア・同じ時間足なら
**いつ・どのプロセスで実行しても同じ足**が出る。

種の作り方には一度つまずいた。最初 Python の組み込み `hash()` を使ったが、
文字列のハッシュはプロセスごとに無作為化されるため、**同じ入力で毎回
違うデータ**が出ていた。試験が通ったり落ちたりする原因になる。
いまは `sha256` で作っている（`_stable_hash`）。

## CSV

置き場所と名前の規則:

```
<data_dir>/<PAIR>_<TIMEFRAME>.csv
例) data/USDJPY_M15.csv
```

見出し:

```
timestamp,open,high,low,close[,volume]
2026-09-11T00:30:00+00:00,150.0,151.0,149.0,150.5,1000
```

- `timestamp` は ISO 8601。タイムゾーンが無いものは UTC とみなす。
- `volume` は省略できる。

### 壊れた行の扱い

**黙って直さない。飛ばして記録する。**

`data/USDJPY_M30.csv` には `high < low` の行をわざと1行入れてある。
読むとこうなる。

```json
{"candles": 2, "rejected_count": 1,
 "rejected_rows": ["USDJPY_M30.csv 3行目: high が low を下回っています"]}
```

補間したり近い値に丸めたりすると、**壊れたデータで計算した結果が
正常な結果と見分けられなくなる**。それが判断に混ざるほうが危ない。

### 同梱の CSV について

`data/` の見本13本は**時刻が固定**してある。時間が経つと鮮度の条件に
引っかかり、CSV 供給元では最終的にすべて NO_TRADE になる。

```
EURUSD  NO_TRADE  STALE  「M5 の最後の足が 0.4 時間前で、古すぎます」
AUDUSD  NO_TRADE  PROVIDER_ERROR  「CSV がありません: AUDUSD_H4.csv」
```

これは不具合ではなく、**古いデータで判断を続けないための設計どおりの挙動**。
CSV 供給元で売買シグナルまで出したい場合は、時刻の新しい CSV を置き換える。
26ペアぶんの CSV は同梱していないので、無いペアは PROVIDER_ERROR になる。

### 経路の制限

`pair` は設定にある26ペアの名前しか受け付けない。時間足も同様。
そのうえで組み立てた絶対パスが置き場の中にあることを確かめる。
`../../etc/passwd` のような入力は `KeyError` で弾かれる（試験あり）。

## 実勢データの取り込み

`scripts/fetch_yahoo.py` が Yahoo Finance から実勢の為替を取り寄せ、
CSV に落とす。

```bash
python scripts/fetch_yahoo.py --out data/real
python scripts/fetch_yahoo.py --pairs USDJPY,EURUSD --out data/real
```

**これはアプリの一部ではない。** 取り込みだけを行う道具で、書き出した CSV を
`csv` 供給元が読む。アプリが実行中に外部へ繋ぎにいく作りにはしていない
（供給元の抽象を保つため。mt4 / external_api / future_broker は未実装のまま）。

守っていること。

1. **正体を名乗る。** ブラウザのふりをする User-Agent は使わない。
   `fx-ai-system/0.1 (personal research)` で普通に通る。
2. **進行中の足を落とす。** Yahoo は「まだ終わっていない最後の1本」を
   混ぜて返す。確定した足として扱うと指標が誤作動する
   （market-radar で的中率が29.6%まで落ちた原因）。
3. **欠けた足は埋めない。捨てる。**
4. **H4 は H1 を4本ずつまとめて作る。** Yahoo に4時間足が無いため。
   境目は 00:00 UTC 起点にそろえる。そろえないと取り寄せた時刻で
   切れ目が変わり、同じ期間でも別の結果が出る。

### Yahoo 側の制限

| 時間足 | 遡れる範囲 |
|---|---|
| M5 / M15 | 約60日 |
| H1 | 730日まで（この道具は180日を取る） |
| H4 | 提供なし → H1 から合成 |

つまり **M15 での検証は約60日ぶんが上限**。それ以上の期間を見たい場合は、
業者から履歴を落として CSV に直す必要がある。

### スプレッド

`config/spreads.json` に **外為オンライン 店頭FX の原則固定スプレッド**
（2026-08-25 取得）を置いてある。価格の単位で、`narrow`（平常時）と
`wide`（広がったとき）の2組。

**一律の値を置いてはいけない。** GBPNZD は10pips、USDJPY は0.9pips で、
10倍以上ちがう。一律に置くと、広い銘柄の成績を実際よりずっと良く見せる。

表に無い銘柄（TRYJPY / MXNJPY）は `default_pips`（3.0）を使う。推測値なので、
その2銘柄の結果は割り引いて読む。

「原則固定」は保証ではない。指標発表の前後・早朝・週明けの窓では広がる。
検証は `wide` でも試すこと。

## 検証（Pydantic）

`Candle` と `CandleSeries` は受け取った時点で確かめる。

**足1本**
- `high >= max(open, close, low)`
- `low <= min(open, close, high)`
- 価格が正

**系列**
- 時刻が昇順
- 時刻の重複が無い
- ペアと時間足が設定にあるもの

不整合なら `ValidationError`。壊れた足は上の層へ渡さない。

## データが足りない・古いとき

どちらも `filters.py` の強制条件で **NO_TRADE** になる。

- 足の本数が最低本数（既定60本）に満たない → NO_TRADE
- 最後の足が `max_bars_behind`（既定3本）分より古い → NO_TRADE
- 供給元が落ちている → 全ペア NO_TRADE

古いデータで分析を続けるのがいちばん危ない。止まるほうを選ぶ。

## 置き場のパス

`FX_CSV_DATA_DIR` の相対指定は**プロジェクト基点**に付く。
起動したディレクトリには依存しない。

`backend/` から uvicorn を起動したときに `backend/data` を探しに行き、
さらに `backend/database/` と `backend/logs/` が別にできた。
同じ設定なのに置き場が2つある状態を実際に作ってしまったので、
基点から解決するよう直してある（試験あり）。
