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

# **過去の足でボードを作り直して測り、積み重ねる**（2026-10-18 追加）。手元の足は直近約60日
# しか無いので、毎週測り直して logs/replay/archive.json に足していく。続けて指標の候補と
# 利確の倍率を、積み重ねた分で試す（logs/replay/<日付>.md・<日付>-signals.md）。
# 続けて、約2年の1時間足（data/long）を取り直し、前半で見つけ・後半で確かめる検証（2026-10-18 追加。
# 8週間の結果はその時期の相場に引きずられる。実際、ドル指数は8週では56%、2年では50%だった）。
$steps = @(
    @("ボードの再生", @("scripts/replay_boards.py")),
    @("指標の候補", @("scripts/signal_lab.py")),
    @("長い足の取り直し", @("scripts/fetch_long.py")),
    @("長い相関の材料", @("scripts/fetch_drivers.py", "--out", "data/long", "--range", "730d")),
    @("約2年の検証", @("scripts/long_lab.py"))
)
foreach ($step in $steps) {
    $t = Get-Date
    $argv = $step[1]
    $out = & $py @argv 2>&1
    $code = $LASTEXITCODE
    $secs = [int]((Get-Date) - $t).TotalSeconds
    $line = "{0:yyyy-MM-dd HH:mm} {1} 終了コード={2} {3}秒 | {4}" -f $t, $step[0], $code, $secs, (($out | Select-Object -Last 1) -join "")
    Add-Content -Path $log -Encoding utf8 -Value $line
}