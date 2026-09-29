# FX 判断支援エンジンを起動する。
#
# **毎回2つの窓でコマンドを打たなくて済むようにする。** 実際、起動を
# 忘れたまま iPhone から開いて「見られない」になった。
#
# 使い方（PowerShell）:
#     .\scripts\start.ps1              # 本番ビルドで起動（速い。既定）
#     .\scripts\start.ps1 -Dev         # 開発モードで起動（コードを触る日）
#     .\scripts\start.ps1 -Stop        # 止める
#
# 止めるまで動き続ける。窓を閉じても裏で動いているので、止めるときは
# -Stop を使うこと。

param(
    [switch]$Dev,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv\Scripts\python.exe"
$frontend = Join-Path $root "frontend"

function Stop-Port([int]$port) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        try { Stop-Process -Id $c.OwningProcess -Force -ErrorAction Stop }
        catch { }
    }
}

if ($Stop) {
    Stop-Port 8000
    Stop-Port 3000
    Write-Host "止めました（3000 / 8000）" -ForegroundColor Yellow
    exit 0
}

if (-not (Test-Path $venv)) {
    Write-Host "Python の置き場が見つかりません: $venv" -ForegroundColor Red
    exit 1
}

# 二重起動を避ける。**同じ番号で2つ動くと、どちらを見ているか分からない。**
Stop-Port 8000
Stop-Port 3000
Start-Sleep -Seconds 1

# --- バックエンド ---
# **実勢データで動かす。** 既定のままだと合成データになり、画面にも
# 「合成データ」と出る。
$env:FX_ENV = "development"
$env:FX_MARKET_DATA_PROVIDER = "csv"
$env:FX_CSV_DATA_DIR = "./data/real"
Start-Process -FilePath $venv `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--log-level", "warning" `
    -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden
Write-Host "バックエンド: http://127.0.0.1:8000" -ForegroundColor Green

# --- 画面 ---
# **npm.cmd と書く。** Windows PowerShell 5.1（タスクスケジューラ）では "npm" が npm.ps1 に
# 解決され、Start-Process ではスクリプトが「開かれる」だけで画面が起動しなかった（2026-09-29）。
if ($Dev) {
    Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev" `
        -WorkingDirectory $frontend -WindowStyle Hidden
    Write-Host "画面（開発モード）" -ForegroundColor Green
} else {
    Write-Host "画面を組み立てています（初回は1分ほど）..." -ForegroundColor Gray
    Push-Location $frontend
    & npm run build | Out-Null
    Pop-Location
    Start-Process -FilePath "npm.cmd" -ArgumentList "run", "start" `
        -WorkingDirectory $frontend -WindowStyle Hidden
    Write-Host "画面（本番ビルド）" -ForegroundColor Green
}

# --- どこから見られるか ---
Start-Sleep -Seconds 6
$ips = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.*' -and $_.InterfaceAlias -notlike 'vEthernet*' }
Write-Host ""
Write-Host "このPC:      http://127.0.0.1:3000"
foreach ($ip in $ips) {
    Write-Host "iPhone から: http://$($ip.IPAddress):3000   ($($ip.InterfaceAlias))"
}

# **繋がらない原因のほとんどはここ。** 受信を許していないと、PC からは
# 見えるのに iPhone からだけ見えない、という分かりにくい形になる。
$rule = Get-NetFirewallRule -DisplayName "FX 判断支援エンジン (3000)" -ErrorAction SilentlyContinue
if (-not $rule) {
    Write-Host ""
    Write-Host "※ iPhone から見るには、3000番の受信を1度だけ許可してください。" -ForegroundColor Yellow
    Write-Host "   管理者の PowerShell で:" -ForegroundColor Yellow
    Write-Host '   New-NetFirewallRule -DisplayName "FX 判断支援エンジン (3000)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 3000 -Profile Any -RemoteAddress LocalSubnet' -ForegroundColor Cyan
}
