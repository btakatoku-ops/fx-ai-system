# FX AI Day Trading Decision Engine — Phase 1

相場を分析して **BUY / SELL / WAIT / NO_TRADE** を返す判断支援エンジン。

> **発注しません。** 自動売買もブローカー接続もこの段階には入っていません。
> 点数は**場面の整い方**であって、**勝率でも上昇確率でもありません**。
> 売買の判断と結果はご自身の責任になります。

---

## 必要なもの

| | 版 | 確認済み |
|---|---|---|
| Python | 3.11 以上 | 3.14.6 |
| Node.js | 20 以上 | 24.15.0 |
| OS | Windows / macOS / Linux | Windows 10 Pro |

---

## 導入

プロジェクト直下で実行します。

### 1. Python

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**macOS / Linux**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

### 2. 環境変数

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env.local
```

既定のままで動きます。`.env` は版管理しません。

### 3. フロントエンド

```bash
cd frontend
npm install
```

---

## 起動

### バックエンド

**Windows**

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

**macOS / Linux**

```bash
cd backend && ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

- API: http://127.0.0.1:8000
- 自動生成のドキュメント: http://127.0.0.1:8000/docs

確認:

```bash
curl http://127.0.0.1:8000/health
```

```json
{"status":"ok","version":"0.1.0","database":"ok",
 "market_data":"mock","provider_state":"OK"}
```

### フロントエンド

バックエンドを起動したまま、別の端末で。

```bash
cd frontend
npm run dev
```

画面: http://localhost:3000

---

## 試験

```bash
.venv/Scripts/python.exe -m pytest tests -q      # Windows
.venv/bin/python -m pytest tests -q              # macOS / Linux
```

```
438 passed
```

画面側:

```bash
cd frontend && npm test        # 19件（期限・金額表示・建玉の線・ボードの帯と水準）
cd frontend && npx tsc --noEmit && npm audit
```

---

## 検証

```bash
cd backend && ../.venv/Scripts/python.exe -m app.backtester --bars 600 --step 2
```

過去の足を1本ずつ進めて、判断と決着を突き合わせます。未来を見ないよう、
判断時点より後ろの足は供給元の側で見えなくしてあります。建玉は判断した足の
**次の**足の始値で、スプレッドを引きます。

詳しくは [docs/backtesting.md](docs/backtesting.md)。

> 合成データでの検証結果は、**相場について何も述べていません**。
> 合成データを作る式の性質を測っただけです。取引の根拠にはできません。

**実勢データ（26銘柄・約2か月半）で分かったこと**

| 組 | 件数 | 勝率 | 平均R |
|---|---:|---:|---:|
| 建てたもの | 203 | 31.6% | −0.117 |
| 基準（強制条件を通った場面） | 2,714 | 31.4% | −0.116 |
| 対照群（コイン投げ） | 2,714 | 31.3% | −0.115 |
| 強制条件で弾いた場面 | 19,648 | 16.7% | −0.438 |

- **強制条件は効いている**（+14.8ポイント、p<0.001）。裏づけの取れた唯一の部品。
- **向きの判断はコイン投げと差が無い**（p=0.918）。
- **点数の選別も効果が見えない**（p=0.899）。
- 3つの組が平均R −0.115〜−0.117 で揃う。測れているのは**取引費用そのもの**。
- 損切り幅・保有時間・損益比をどう変えても、**平均Rが正になる設定は無かった**。

**このシグナルエンジンには、実データ上で測定可能な優位性がありません。**

詳しくは [docs/backtesting.md](docs/backtesting.md)。

---

## 構成

```
fx-ai-system/
├── config/                売買ロジックの値（版管理する）
│   ├── pairs.json         26通貨ペア
│   ├── timeframes.json    D1 / H4 / H1 / M30 / M15 / M5 / M1
│   ├── indicators.json    EMA・RSI・MACD・ATR・BB・ADX・ストキャス
│   ├── regime.json        相場つきの閾値
│   ├── signal_weights.json 9項目の配点と区分（材料の有無つき）
│   ├── filters.json       強制の見送り条件
│   └── backtest.json      検証の条件
├── backend/app/
│   ├── config.py          設定の唯一の読み込み口
│   ├── models.py          Pydantic の型と検証
│   ├── market_data.py     供給元の抽象（mock / csv / 未実装3種）
│   ├── synthetic.py       合成データ（種は固定）
│   ├── indicator_engine.py 指標の計算
│   ├── market_structure.py HH/HL/LH/LL・BOS・CHOCH
│   ├── regime_engine.py   相場つきの判定
│   ├── filters.py         強制条件 ★点数とは別系統
│   ├── freshness.py       足の素性・欠落・有効期限
│   ├── signal_engine.py   向き・点数・最終シグナル
│   ├── analysis.py        1銘柄の組み立て
│   ├── pair_ranker.py     並べ替え
│   ├── backtester.py      過去の足での検証（未来を見ない）
│   ├── performance.py     成績の集計と比較
│   ├── main.py            FastAPI
│   ├── db.py / logging_setup.py
│   ├── risk_engine.py     Lot・証拠金・損益（Phase 2）
│   ├── trade_plan.py      Entry/SL/TP の組み立て（Phase 2）
│   ├── entry_engine.py    換算レートの収集・建値の帯（Phase 2）
│   ├── correlation.py     相関の測定（Phase 2）
│   ├── news.py            指標の停止と余裕（Phase 2）
│   ├── cache.py           分析の使い回し（期限は緩めない）
│   ├── analysis_service.py 分析の唯一の入口
│   └── （未実装）notification_engine / trade_logger
├── frontend/              Next.js 16（App Router）
├── data/                  CSV の見本13本（real/ は取り込んだ実勢データ）
├── scripts/               実勢データ・スワップ表の取り込み
├── tests/                 281件
└── docs/                  書類7本
```

---

## 書類

| | |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 層・依存の向き・失敗時の既定値 |
| [docs/signal-engine.md](docs/signal-engine.md) | 向き・点数・強制条件の決め方 |
| [docs/market-data.md](docs/market-data.md) | 供給元・CSV の形式・検証 |
| [docs/testing.md](docs/testing.md) | 試験の内容と、見つけた不具合 |
| [docs/backtesting.md](docs/backtesting.md) | 検証の仕組みと、結果の読み方 |
| [docs/trade-plan.md](docs/trade-plan.md) | Entry/SL/TP/Lot/証拠金（Phase 2） |

---

## API

| 口 | 内容 |
|---|---|
| `GET /health` | 稼働・DB・供給元 |
| `GET /api/pairs` | 26通貨ペア |
| `GET /api/pairs/{pair}` | 1ペアの定義 |
| `GET /api/analysis/{pair}` | 1ペアの分析結果 |
| `GET /api/ranking?limit=N` | 全ペアの並べ替え |
| `GET /api/provider/status` | 供給元の状態 |
| `GET /api/plan/{pair}` | 建玉計画（Entry/SL/TP/Lot/証拠金）。**発注はしない** |
| `GET /api/candles/{pair}?timeframe=H1&limit=400` | ローソク足と指標（EMA20/50/200・BB・支持抵抗・RSI14・MACD・ATR・ADX）と、この先の値動きの幅。チャート表示用 |
| `GET /api/strategies` | 持っている戦略と、相場つきごとの割り当て・検証状況 |
| `GET /api/data-status` | 取り込んだ材料（予定表・相関・スワップ）の鮮度と、取り込み直す方法 |
| `GET /api/journal?limit=N` | 画面に出した判断の記録。**建てたかどうかは含まない** |
| `GET /api/focus` | どの銘柄を支援するか。**費用だけで分けている** |
| `GET /api/morning-brief?format=text` | 朝の確認。**今日この道具が使えるのか、から書く** |
| `GET /api/ranking?tier=focus` | 支援する銘柄だけ分析する（26銘柄ぶん計算しない）|
| `GET /api/board?pair=` | 朝のボード。事実（値幅の消化・20日レンジの位置・近い水準）と、トレンド・モメンタム・ファンダの ▲▼■、除外の理由。**予測はしない。「発注」とは書かない** |
| `POST /api/fundamentals/{pair}` | ファンダの見立てを入れる（`view`=up/down/neutral・`note`・`source`）。**出典が空なら受け付けない。日付は保存した日** |
| `POST /api/dev/load-csv` | 開発用。`FX_ENV=development` のときだけ |

---

## iPhone のホーム画面に置く

**App Store のアプリにはしていません。** そのためには Mac と Xcode、
Apple の開発者登録（年額）が要り、いまの構成では **PC が計算しているので
アプリだけ配っても動きません**。代わりに、**ホーム画面から全画面で開ける**
ようにしてあります（PWA）。

### 置き方

1. **PC 側を起動する。** PowerShell で1回叩くだけ。
   ```powershell
   .\scripts\start.ps1        # 本番ビルドで起動（速い）
   .\scripts\start.ps1 -Dev   # コードを触る日はこちら
   .\scripts\start.ps1 -Stop  # 止める
   ```
   起動すると、iPhone から開くアドレスをそのまま表示します。
2. **3000番の受信を1度だけ許可する。**（初回のみ。管理者の PowerShell）
   ```powershell
   New-NetFirewallRule -DisplayName "FX 判断支援エンジン (3000)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 3000 -Profile Any -RemoteAddress LocalSubnet
   ```
   **これが無いと、PC からは見えるのに iPhone からだけ見えません。**
   `-RemoteAddress LocalSubnet` で、同じ LAN の中からだけに限っています。
3. **iPhone を同じ Wi-Fi に繋ぐ。**
3. Safari で **`http://<PCのIP>:3000`** を開く（例 `http://192.168.0.5:3000`）。
   IP は PowerShell の `ipconfig` か、`npm run dev` が出す `Network:` の行で分かる。
4. 共有ボタン → **「ホーム画面に追加」**。

これでアイコンから全画面で開きます。Safari の枠は出ません。

### 繋がらないときに見るところ

| 症状 | 見るところ |
|---|---|
| 画面が出ない | **まず PC 側が起動しているか**（`.\scripts\start.ps1`）。次にファイアウォール（上の手順2）。最後に同じ Wi-Fi か |
| PC からは見えるが iPhone から見えない | ほぼファイアウォール。上の手順2をやったか |
| 画面は出るが押しても動かない | `next.config.mjs` の `allowedDevOrigins` に PC の IP があるか。**無いと HMR が拒否され、hydration ごと止まる** |
| 数字が古いまま | 取り込みは PC 側で走る。`scripts/morning_brief.py` を確認 |

### できないこと（PWA の限界）

- **通知は来ません。** iOS の Web Push は HTTPS でないと使えません。
  いまは PC を平文 HTTP で見る構成なので対象外です。
- **オフラインでは動きません。** Service Worker も HTTPS が要ります。
- **PC が落ちていると開けません。** 計算しているのは PC です。
- 外出先からは見られません（同じ Wi-Fi のときだけ）。

**アイコンは同梱の画像ではなく、`scripts/make_icons.py` が描いています。**
どこから来たか分からない画像を置かないためです。

---

## 実勢データで検証する

```bash
python scripts/fetch_yahoo.py --out data/real
```

Yahoo Finance から26銘柄ぶんの M5 / M15 / H1 を取り寄せ、H4 を H1 から
合成して CSV に落とします（約47MB、intraday は Yahoo 側の制限で約60日ぶん）。
そのうえで、

```bash
cd backend
FX_MARKET_DATA_PROVIDER=csv FX_CSV_DATA_DIR=./data/real   ../.venv/Scripts/python.exe -m app.backtester --bars 5000 --step 8
```

スプレッドは `config/spreads.json` の **外為オンライン実勢値**を使います。

## 経済指標カレンダーを取り込む

```bash
python scripts/fetch_calendar.py --out data/calendar/events.json
```

**発表前後の停止は点数ではなく強制条件**です。予定表が36時間より古いと
`UNKNOWN` になり建てません。**毎日取り込んでください。**

## 相関の材料を取り込む

```bash
python scripts/fetch_drivers.py --out data/drivers
```

ドル指数・米10年債・金・VIX・S&P500 を Yahoo から取り寄せます。
**どの銘柄が何と相関するかは決め打ちせず、期間ごとに測ります**（弱ければ
使いません）。詳しくは [docs/signal-engine.md](docs/signal-engine.md)。

## スワップ表の取り込み

```bash
python scripts/fetch_swap_gaitame.py --out swap.csv
python scripts/import_swap.py --csv swap.csv     --source "外為オンライン スワップポイント一覧表 2026年9月（PDF）" --as-of 2026-09-11
```

外為オンラインが公開している月次PDFから自動で取り込めます（26銘柄・取得済み）。
他社の表を手で入れる場合は `data/swap_template.csv` を雛形にしてください。

出所と日付は必須です（スワップは毎日変わるため）。取り込むまで計画の
スワップ欄は「不明」で、**0円とは表示しません**。7日を過ぎた表は
古いものとして警告します。詳しくは [docs/trade-plan.md](docs/trade-plan.md)。

## 市場データの切り替え

`.env` の1行だけ。

```
FX_MARKET_DATA_PROVIDER=mock   # 合成データ（既定）
FX_MARKET_DATA_PROVIDER=csv    # data/ の CSV を読む
```

CSV の形式:

```
data/USDJPY_M15.csv
timestamp,open,high,low,close[,volume]
```

同梱の見本13本は**時刻が固定**なので、`csv` に切り替えると鮮度の条件で
すべて NO_TRADE になります。古いデータで判断を続けないための設計どおりの
挙動です。売買シグナルまで見たい場合は、時刻の新しい CSV に置き換えてください。

MT4・外部 API・ブローカーの3つは**未実装**で、選ぶと
`NotImplementedError` になります。それらしい値を作らないための意図的な挙動です。

---

## Phase 1 の範囲と、仕様との差

### この段階で提供するもの

相場を分析して **BUY / SELL / WAIT / NO_TRADE** を返すまで。
発注・自動売買・ブローカー接続・機械学習・通知は**含みません**。

### Master Prompt との差（意図した差分）

| 項目 | 仕様 | この実装 | 理由 |
|---|---|---|---|
| 配点の満点 | 100点 | **100点**（材料を繋いだ） | ただし測れない項目はその回だけ満点から外れる |
| フロントエンド | Next.js / React / TypeScript | Next.js 16.3.4（App Router） | 仕様どおり。Vite は使っていない |
| 気配値の許容経過 | — | 600秒 | 足から気配値を作る供給元は原理的に「入口の足」より新しくなれない。ティック配信を繋いだら90秒などへ下げる |
| Entry / Risk / Plan | Phase 2 | **実装済み**（発注は含まない） | 利用者の指示により Phase 2 に着手 |
| 支援する銘柄の絞り込み | — | **実装済み**（`config/focus.json`）| 費用（スプレッド ÷ M15 の ATR）で測って決める。いまは **GBPJPY と USDJPY の2銘柄**。**勝てるかどうかでは分けていない** |
| iPhone | — | **ホーム画面から全画面で開ける**（PWA）| App Store のアプリではない。PC が計算しているので、同じ Wi-Fi で PC が動いている必要がある。通知とオフラインは HTTPS が要るため対象外 |
| 朝の確認 | — | **実装済み**（`scripts/morning_brief.py`）| 今日この道具が使えるか、から書く。使えないなら理由とコマンドを先に出す |
| 手仕舞いの刻限 | — | **日本時間 01:00**（`config/filters.json` の `day_trade`）| FXモーニングブリーフに合わせた（2026-09-27）。01:00 を過ぎたら日替わり（夏 06:00／冬 07:00）まで新しく建てない。**どう設定しても日替わりは跨がない**。それまでの検証結果（`docs/backtesting.md`）は刻限 06:00／07:00 で測ったもの |
| 朝のボード | — | **実装済み**（画面のいちばん上・朝の確認の文面）| 予測をやめ、材料を並べる方式。トレンド（H4とH1のEMA20/50）・モメンタム（M15のRSIとMACD）・ファンダ（**使う人が入れる**）の向きを ▲▼■ で出し、そろい・まちまち・除外を示す。除外は強制条件・テクニカルとファンダの対立・介入警戒帯・値幅の使い切り（普段の1.2倍）・刻限だけ。設定は `config/board.json` |
| ファンダの見立て | — | **FXモーニングブリーフから自動取り込み**＋手入力（`data/fundamentals.json`、画面の各ボードから書き換え）| 機械は経済や政策を判断しない。出典必須。**7日を過ぎた見立ては判定に数えない**（画面には残す）。ブリーフより後に手で入れた見立ては上書きしない |
| FXモーニングブリーフ連携 | — | **平日8:30の定期タスク**（`scripts/import_brief.py`）| ブリーフ（claude.ai のアーティファクト）を定期タスクが保存し、スクリプトが方向性ボードを**機械的に**読む。ブリーフの日付が今日でなければ取り込まない。方向性ボードの無い版は何も取り込まない。ブリーフの判定はボードに並べ、後で同じ足で採点する。**アプリの除外の理由にはしない**（まだ測れていない）|
| ボードの採点 | — | **実装済み**（`scripts/score_boards.py`）| 1銘柄1日1枚、朝に最初に出たボードを残す（閉場中・合成データは残さない）。刻限を過ぎてから、刻限までの足だけで採点。**30日に届くまで割合は名乗らない** |
| 判断の記録 | Phase 2 以降 | **実装済み**（`/api/journal`）| 画面に出した判断をそのまま残す。同じ判断が続くあいだは1行で、回数だけ増やす。**建てたかどうかは含まない** |
| 結果の突き合わせ | — | **実装済み**（`scripts/score_journal.py`）| 残した判断を後から採点する。**判断した時刻より後の足しか使わない**。合成データの判断は採点しない |
| この先の値動き | — | **幅だけ出す。向きは出さない** | 実データ26銘柄で測った ATR 倍率。決めた期間とは別の期間で確かめて 80〜81% を覆った。**上下どちらに動くかは何も言っていない** |
| 画面の更新 | — | **30秒ごとに読み直す**（止められる）| 有効期限60秒に対して手動だけでは足りない。**期限の判定は変えていない** |
| 取り込みの鮮度 | — | **画面に出す**（`/api/data-status`）| 予定表が古くなると全銘柄が見送りになるので、そうなる前に知らせる |
| チャート | MT4 相当の表示 | **実装済み**（銘柄ごと・H4/H1/M15/M5） | MT4 の指標（EMA20/50/200・ボリンジャー・支持抵抗・RSI14・MACD）を、TradingView の操作感（十字線・その場読み・拡大縮小・掴んで移動・表示切替）で見る。BUY/SELL のときは Entry/SL/TP を線と帯で重ねる。描画ライブラリは使わず SVG を直接書いている |
| 戦略の数 | — | **5つ**（`trend` / `breakout` / `momentum_flag` / `range` / `failed_breakout`）| 相場つきごとに順番付きで割り当て、最初に向きが出たものを採る。**どれで入ったかを記録して1つずつ測る** |
| 持ち合いの戦略 | — | **実装済み。ただし実データでは建玉0件** | 変動の小さい相場で、帯の端でだけ入る逆張り。真ん中では入らない。**効くという根拠は出ていない**（`docs/backtesting.md` 2026-09-15）|
| 取引単位 | — | **数量（整数）** | 取引要綱どおり。ZARJPY・MXNJPY は10万通貨（数量1）で他の10倍 |
| スワップ | 表示する | **取り込み済み（26銘柄）** | 業者の公開PDFから取得。推測値は入れない |
| 期待値・信頼度% | 表示する | **出さない** | 優位性が測れていないため |
| PWA | 将来 | 未対応（方針のみ） | Phase 1 の範囲外 |

### できないこと

**できないことを、できるように見せないことがこの節の目的です。**

1. **勝てる根拠がありません。** 実データ26銘柄・30,914回の判断で測った
   結果、**5つの戦略はどれも、同時刻のコイン投げを上回りませんでした**。
   いったん「トレンド追随は有効（p<0.001）」と読めた数字も、向きを揃えて
   比べ直すと差が消えました（`docs/backtesting.md` 2026-09-15）。
   **この道具は「場面を整理する」ところまでで、優位性は示せていません。**
2. **費用の壁を越えていません。** 26銘柄中22銘柄で、スプレッドが M15 の
   ATR の25%を超えます。向きが出た場面の 77〜100% は、この条件で消えます。
3. **勝手に判断を延命しません。** 分析の有効期限は60秒です。画面は
   30秒ごとに読み直しますが（止められます）、**期限そのものは延ばしません**。
   読み直しが止まっていれば「期限切れ」と出ます。
4. **データの取り込みが手動です。** 実勢の足（`scripts/fetch_yahoo.py`）、
   経済指標（`fetch_calendar.py`）、相関の材料（`fetch_drivers.py`）は
   自分で走らせます。**経済指標の予定表は36時間で古くなり、そこから先は
   全銘柄が見送りになります。**
5. **既定は合成データです。** 実際の値動きではありません。画面にもその旨を
   出しています。実勢で見るには取り込みと `FX_MARKET_DATA_PROVIDER=csv`
   が要ります。取り込みから時間が経つと、鮮度の条件で全ペアが NO_TRADE に
   なります（設計どおりの挙動）。
6. **発注しません。** 設計として入れていません。`trade_plan` は数字を出す
   だけで、注文は利用者が業者の画面で行います。
7. **結果の突き合わせは手動です。** 判断は残るようになりました（下記）が、
   結果を埋めるのは `scripts/score_journal.py` を走らせたときだけです。
   **走らせていないあいだ、結果の欄は空のままです。**
8. **通知しません。** `notification_engine` は未実装です。
9. **利用者の区別がありません。** 認証はありません。ローカルで使う前提です。

### 点数について

点数は9項目の重み付き合計で、**場面がどれだけ整っているか**を表します。
勝率でも上昇確率でもありません。80点は「80%当たる」ではありません。

高い点数が強制の見送り条件を上書きすることはありません。
95点でもスプレッドが上限を超えていれば `NO_TRADE` です。

---

## 安全のための決め事

- 秘密はコードに書かない。環境変数から読む。ログに出す前に伏せる。
- `eval()` を使わない。web からの入力で shell を動かさない。
- CSV の経路は設定にあるペア名と時間足からのみ組み立て、置き場の外は読まない。
- CORS は許可する出所を明示する。
- 例外を黙って握りつぶさない。記録したうえで `NO_TRADE` に倒す。
- 壊れたデータを補間しない。飛ばして記録する。
