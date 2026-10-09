# 週のまとめ（土曜 8:00。Windows のタスクスケジューラ「FX 週のまとめ」から）
#
# 金曜の刻限（土曜 01:00）を過ぎてから、その週のボードを採点してまとめる。
# 書き出し先は logs/weekly/<年>-W<週>.md。記録は logs/weekly.log。
# **予測はしない。** 30件に届かない区分は割合を名乗らない（scripts/weekly_report.py）。
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$log = Join-Path $root "logs\weekly.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:FX_MARKET_DATA_PROVIDER = "csv"
$env:FX_CSV_DATA_DIR = "./data/real"
Set-Location $root

$t = Get-Date
$out = & $py scripts/weekly_report.py 2>&1
$code = $LASTEXITCODE
$secs = [int]((Get-Date) - $t).TotalSeconds
$line = "{0:yyyy-MM-dd HH:mm} 週のまとめ 終了コード={1} {2}秒" -f $t, $code, $secs
$line += " | " + (($out | Select-Object -Last 1) -join "")
Add-Content -Path $log -Encoding utf8 -Value $line