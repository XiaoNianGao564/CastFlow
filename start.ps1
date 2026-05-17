# CastFlow 启动脚本 (PowerShell)
# 右键 -> 使用 PowerShell 运行

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  CastFlow Start Helper (PowerShell)"       -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# 检查 bun
$bun = Get-Command "bun" -ErrorAction SilentlyContinue
if (-not $bun) {
    Write-Host "ERROR: 'bun' not found. Run: npm install -g bun" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "[OK] bun: $($bun.Source)" -ForegroundColor Green

Set-Location "C:\Users\17166\Desktop\CastFlow"

# 杀掉占端口的进程
Write-Host "[1/3] Releasing ports 3000-3001..."
@(3000, 3001) | ForEach-Object {
    $conn = Get-NetTCPConnection -LocalPort $_ -ErrorAction SilentlyContinue
    if ($conn) {
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}
Start-Sleep 2
Write-Host "  OK" -ForegroundColor Green
Write-Host ""

# 启动 API
Write-Host "[2/3] Starting API Server..."
$apiJob = Start-Process -FilePath "bun" -ArgumentList "run --cwd packages\api src\index.ts" -NoNewWindow -PassThru -Environment @{ PORT = "3001" }
Start-Sleep 5

# 检测 API 端口
Write-Host "  Detecting API port..." -NoNewline
$apiPort = $null
for ($p = 3001; $p -le 3020; $p++) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$p/api/health" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        if ($r.StatusCode -eq 200) {
            $apiPort = $p
            break
        }
    } catch {}
}
if ($apiPort) {
    Write-Host " $apiPort" -ForegroundColor Green
} else {
    Write-Host " not found (check API window)" -ForegroundColor Yellow
    $apiPort = "3001"
}
Write-Host ""

# 启动前端
Write-Host "[3/3] Starting Web App..."
$env:API_PORT = $apiPort
$uiJob = Start-Process -FilePath "bun" -ArgumentList "run --cwd packages\app dev" -NoNewWindow -PassThru

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  FRONTEND: http://localhost:3000"            -ForegroundColor Green
Write-Host "  API:       http://localhost:$apiPort"       -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press Ctrl+C to stop all services."
Read-Host "Press Enter to continue..."
