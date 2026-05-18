param([int]$Port = 8780)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
Set-Location $PSScriptRoot

Write-Host "FindCheapFlights - arrancando..." -ForegroundColor Cyan

# 1) Kill any previous uvicorn for THIS project so a restart actually takes
#    effect instead of leaving a stale server running.
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*uvicorn*app.main*" } |
    ForEach-Object {
        Write-Host "  matando server previo PID $($_.ProcessId)" -ForegroundColor DarkGray
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

# 2) If the port is busy (e.g. zombie socket on 8765), step up until free.
function Test-PortFree([int]$p) {
    -not (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)
}
$tries = 0
while (-not (Test-PortFree $Port) -and $tries -lt 20) {
    Write-Host "  puerto $Port ocupado, probando $($Port + 1)" -ForegroundColor DarkYellow
    $Port++; $tries++
}

Write-Host ""
Write-Host "  >> Abre el navegador en:  http://127.0.0.1:$Port/" -ForegroundColor Green
Write-Host "  >> Para detener: Ctrl+C aqui" -ForegroundColor DarkGray
Write-Host ""

& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port $Port --reload
