# 取引時間中の取り込み直し（平日 9:05〜翌 1:05、5分ごと。Windows のタスクスケジューラから）
#
# 朝 8:30 の1回だけだと、夕方には4時間足が17時間前のものになり、ボードが
# 「データが古すぎる」で除外になる（2026-09-28 に確認）。昼から夜の取引時間に
# 画面を使えるよう、ボードで使う銘柄だけ取り直す。
#
# - **5分ごと**：強制条件は「最後の足が3本ぶん以内」で、5分足なら15分。1時間ごとでは
#   1時間のほとんどが「古すぎ」で除外になった（2026-09-28 に確認）。条件は緩めず、取り直す間隔を詰める
# - 直近だけ取って継ぎ足す（--recent）。60日ぶんを毎回取り直さない
# - 取り直すのは支援・監視の銘柄（GBPJPY・USDJPY・EURJPY）だけ。相関の材料は毎時0〜4分の回だけ
# - **15分ごと（毎時0・15・30・45分台の回）に公開ページを作り直して送る**（2026-10-01）。
#   携帯は PC ではなく GitHub Pages を見るので、PC が止まっていても家の外でも見られる。
#   GitHub Pages は1時間に10回ほどまでなので、5分ごとには送らない
# - 朝のボードの記録は触らない（朝の処理だけが残す）
# - **ロンドン・ニューヨークの始まりのボードを残す**（2026-10-02）。毎回 record_session_board.py を
#   呼ぶが、始まりから1時間のあいだ以外は何もしないで終わる。残したときだけ記録に書く
# - 結果は logs/refresh.log に1行ずつ残す
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$log = Join-Path $root "logs\refresh.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:FX_MARKET_DATA_PROVIDER = "csv"
$env:FX_CSV_DATA_DIR = "./data/real"

Set-Location $root
$start = Get-Date
$pairs = & $py scripts/fetch_yahoo.py --pairs GBPJPY,USDJPY,EURJPY --out data/real --pause 0.6 --recent 2>&1
$pairsCode = $LASTEXITCODE
$driversCode = "-"
if ($start.Minute -lt 5) {
    $drivers = & $py scripts/fetch_drivers.py 2>&1
    $driversCode = $LASTEXITCODE
}
$session = & $py scripts/record_session_board.py 2>&1
$sessionCode = $LASTEXITCODE
$pageCode = "-"
if ($start.Minute % 15 -lt 5) {
    $page = & $py scripts/publish_board_page.py 2>&1
    $pageCode = $LASTEXITCODE
}
$secs = [int]((Get-Date) - $start).TotalSeconds

$line = "{0:yyyy-MM-dd HH:mm} 相場={1} 相関={2} ページ={3} {4}秒" -f $start, $pairsCode, $driversCode, $pageCode, $secs
if ($pairsCode -ne 0) { $line += " | " + (($pairs | Select-Object -Last 3) -join " / ") }
$sessionText = ($session | Where-Object { $_ -match '残しました|待ちます|作れません' }) -join " / "
if ($sessionCode -ne 0) { $line += " | 時間帯のボード: 終了コード=$sessionCode " + (($session | Select-Object -Last 2) -join " / ") }
elseif ($sessionText) { $line += " | 時間帯のボード: " + $sessionText }
if ($pageCode -ne "-" -and $pageCode -ne 0) { $line += " | ページ: " + (($page | Select-Object -Last 2) -join " / ") }
Add-Content -Path $log -Value $line -Encoding utf8
