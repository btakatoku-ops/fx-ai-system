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
# - 朝のボードの記録と公開ページは触らない（あれは朝の写し）
# - 結果は logs/refresh.log に1行ずつ残す
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$log = Join-Path $root "logs\refresh.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
$env:PYTHONIOENCODING = "utf-8"

Set-Location $root
$start = Get-Date
$pairs = & $py scripts/fetch_yahoo.py --pairs GBPJPY,USDJPY,EURJPY --out data/real --pause 0.6 --recent 2>&1
$pairsCode = $LASTEXITCODE
$driversCode = "-"
if ($start.Minute -lt 5) {
    $drivers = & $py scripts/fetch_drivers.py 2>&1
    $driversCode = $LASTEXITCODE
}
$secs = [int]((Get-Date) - $start).TotalSeconds

$line = "{0:yyyy-MM-dd HH:mm} 相場={1} 相関={2} {3}秒" -f $start, $pairsCode, $driversCode, $secs
if ($pairsCode -ne 0) { $line += " | " + (($pairs | Select-Object -Last 3) -join " / ") }
Add-Content -Path $log -Value $line -Encoding utf8
