$ErrorActionPreference = 'Stop'
$PidFile = Join-Path $PSScriptRoot 'reports\dev-pids.json'
if (-not (Test-Path -LiteralPath $PidFile)) { Write-Host 'No TestPilot dev PID file found.'; exit 0 }
$Processes = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
foreach ($Id in @($Processes.backend, $Processes.frontend)) {
    if ($Id) {
        $Process = Get-Process -Id ([int]$Id) -ErrorAction SilentlyContinue
        if ($Process) { Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue }
    }
}
Remove-Item -LiteralPath $PidFile -Force
Write-Host 'Stopped TestPilot AI development services.'
