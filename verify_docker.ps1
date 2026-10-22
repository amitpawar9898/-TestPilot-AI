$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
Push-Location $Root
try {
    if (-not (Test-Path -LiteralPath (Join-Path $Root '.env'))) {
        Copy-Item -LiteralPath (Join-Path $Root '.env.example') -Destination (Join-Path $Root '.env')
    }
    docker compose up --build -d
    $Ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $Api = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 3
            $Web = Invoke-WebRequest -Uri 'http://127.0.0.1:5173/' -UseBasicParsing -TimeoutSec 3
            $Samples = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/samples' -TimeoutSec 3
            if ($Api.status -eq 'ok' -and $Web.StatusCode -eq 200 -and $Samples.items.Count -gt 0) {
                $Ready = $true
                break
            }
        } catch { Start-Sleep -Seconds 2 }
    }
    docker compose ps
    if (-not $Ready) {
        docker compose logs --tail 80 api frontend
        throw 'Docker services did not pass the API, frontend, and sample checks.'
    }
    $Settings = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/settings' -TimeoutSec 5
    Write-Host 'Docker verification passed:'
    Write-Host '  API: http://127.0.0.1:8000/api/health (ok)'
    Write-Host '  Frontend: http://127.0.0.1:5173/ (HTTP 200)'
    Write-Host "  Bundled sample files: $($Samples.items.Count)"
    Write-Host "  LLM: $($Settings.settings.provider) / $($Settings.settings.model) (ready=$($Settings.llm_ready))"
    Write-Host '  API docs: http://127.0.0.1:8000/docs'
    Write-Host 'Stop containers with: docker compose down'
} finally {
    Pop-Location
}
