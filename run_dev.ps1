$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Frontend = Join-Path $Root 'frontend'
$Reports = Join-Path $Root 'reports'
$PidFile = Join-Path $Reports 'dev-pids.json'

if (-not (Test-Path -LiteralPath $Python)) { throw 'Backend virtual environment missing. Follow the README installation steps first.' }
if (-not (Test-Path -LiteralPath (Join-Path $Frontend 'node_modules'))) { throw 'Frontend dependencies missing. Run npm ci in frontend/ first.' }
if (Test-Path -LiteralPath $PidFile) { throw 'A dev-pids.json already exists. Run .\stop_dev.ps1 before starting another copy.' }
New-Item -ItemType Directory -Force -Path $Reports | Out-Null
$Node = (Get-Command node.exe -ErrorAction Stop).Source
$BackendOut = Join-Path $Reports 'backend-dev.log'
$BackendErr = Join-Path $Reports 'backend-dev-error.log'
$FrontendOut = Join-Path $Reports 'frontend-dev.log'
$FrontendErr = Join-Path $Reports 'frontend-dev-error.log'

$Backend = Start-Process -FilePath $Python -ArgumentList @('-m','uvicorn','api.main:app','--host','127.0.0.1','--port','8000') -WorkingDirectory $Root -WindowStyle Hidden -PassThru -RedirectStandardOutput $BackendOut -RedirectStandardError $BackendErr
$FrontendProc = Start-Process -FilePath $Node -ArgumentList @('node_modules/vite/bin/vite.js','--host','127.0.0.1','--port','5173','--strictPort') -WorkingDirectory $Frontend -WindowStyle Hidden -PassThru -RedirectStandardOutput $FrontendOut -RedirectStandardError $FrontendErr
@{ backend = $Backend.Id; frontend = $FrontendProc.Id } | ConvertTo-Json | Set-Content -LiteralPath $PidFile -Encoding utf8

$Ready = $false
for ($i = 0; $i -lt 30; $i++) {
    if (-not (Get-Process -Id $Backend.Id -ErrorAction SilentlyContinue) -or
        -not (Get-Process -Id $FrontendProc.Id -ErrorAction SilentlyContinue)) { break }
    try {
        $Health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2
        $Page = Invoke-WebRequest -Uri 'http://127.0.0.1:5173/' -TimeoutSec 2 -UseBasicParsing
        if ($Health.status -eq 'ok' -and $Page.StatusCode -eq 200) { $Ready = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $Ready) {
    & (Join-Path $Root 'stop_dev.ps1')
    throw "Development services did not become ready. Inspect reports\backend-dev*.log and reports\frontend-dev*.log."
}
Write-Host 'TestPilot AI is ready:'
Write-Host '  Web: http://127.0.0.1:5173'
Write-Host '  API: http://127.0.0.1:8000/docs'
Write-Host "  Stop: .\stop_dev.ps1 (PIDs $($Backend.Id), $($FrontendProc.Id))"
