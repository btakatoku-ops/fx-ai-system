# 朝の処理（平日 8:40。Windows のタスクスケジューラ「FX 朝の処理」から）
#
# **許可待ちで止まらないように、Claude を通さずに動かす。** 以前は Claude の定期
# タスクがすべてを行っていたが、無人の実行で許可を求めて9秒で中断し、取り込み・
# 記録・公開ページがすべて抜けた（2026-09-29 に確認）。
#
# Claude の定期タスク（平日 8:15）はブリーフの取り込みだけを行う。それが失敗しても、
# ここはブリーフ無しで進む（ボードには「今日のブリーフではない」と出る）。
#
# 1. 全銘柄の取り直し・相関・経済指標
# 2. 朝の確認（その日のボードを1枚ずつ記録）
# 3. 公開ページ（GitHub Pages）の作り直し
# 4. 採点
# 結果は logs/morning.log に残す。
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$log = Join-Path $root "logs\morning.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
$env:PYTHONIOENCODING = "utf-8"
# Python の出力（UTF-8）を正しく読む。**無いと PowerShell 5.1 は Shift-JIS として読み、ログが文字化けする**
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:FX_MARKET_DATA_PROVIDER = "csv"
$env:FX_CSV_DATA_DIR = "./data/real"
Set-Location $root

function Step([string]$name, [string[]]$argv) {
    $t = Get-Date
    $out = & $py @argv 2>&1
    $code = $LASTEXITCODE
    $secs = [int]((Get-Date) - $t).TotalSeconds
    Add-Content -Path $log -Encoding utf8 -Value ("{0:HH:mm} {1} 終了コード={2} {3}秒" -f $t, $name, $code, $secs)
    return ,$out
}

Add-Content -Path $log -Encoding utf8 -Value ("=== {0:yyyy-MM-dd HH:mm} 朝の処理 ===" -f (Get-Date))
Step "相場の取り直し" @("scripts/fetch_yahoo.py", "--out", "data/real", "--pause", "0.6") | Out-Null
# **長い期間で取った足は、直近の数時間が欠ける。** 朝のボードが毎朝「H1 の最後の足が
# 3.7〜5.7 時間前で古すぎます」で除外になっていた（2026-09-30〜10-16 の朝の記録15件すべて）。
# 取引時間中の取り込みと同じく、直近だけ取って継ぎ足してからボードを作る（条件は緩めない）
$core = (& $py scripts/dashboard_pairs.py 2>$null | Select-Object -Last 1)
if (-not $core) { $core = "USDJPY,GBPJPY,EURJPY" }
Step "直近の足の継ぎ足し" @("scripts/fetch_yahoo.py", "--pairs", $core, "--out", "data/real", "--pause", "0.6", "--recent") | Out-Null
Step "相関の材料" @("scripts/fetch_drivers.py") | Out-Null
Step "経済指標" @("scripts/fetch_calendar.py") | Out-Null
$brief = Step "朝の確認" @("scripts/morning_brief.py")
$brief | Where-Object { $_ -notmatch '"logger"' } | ForEach-Object { "    $_" } |
    Add-Content -Path $log -Encoding utf8
Step "公開ページ" @("scripts/publish_board_page.py") | Out-Null
Step "判断の採点" @("scripts/score_journal.py") | Out-Null
Step "ボードの採点" @("scripts/score_boards.py") | Out-Null
