$ErrorActionPreference = 'Stop'
$paperRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$paperInfoPath = Join-Path $paperRoot 'runtime\server.json'
if (-not (Test-Path -LiteralPath $paperInfoPath)) { Write-Host 'Paper Agent is not running.'; exit 0 }
$paperInfo = Get-Content -LiteralPath $paperInfoPath -Raw | ConvertFrom-Json
if ($paperInfo.root -ne $paperRoot) { throw 'The server record belongs to another workspace.' }
$paperHealth = Invoke-RestMethod -Uri "$($paperInfo.url)/api/health" -TimeoutSec 3
if ($paperHealth.root -ne $paperRoot -or $paperHealth.pid -ne $paperInfo.pid) { throw 'The live server does not match this workspace record.' }
$paperProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($paperInfo.pid)"
if (-not $paperProcess -or $paperProcess.CommandLine -notmatch '-m\s+app\.server(?:\s|$)') { throw 'The process is not this Paper Agent server.' }
$paperState = Invoke-RestMethod -Uri "$($paperInfo.url)/api/state" -TimeoutSec 3
foreach ($paperRun in $paperState.runs) {
    if ($paperRun.status -in @('queued', 'running', 'cancelling')) {
        Invoke-RestMethod -Uri "$($paperInfo.url)/api/runs/$($paperRun.id)/cancel" -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 3 | Out-Null
    }
}
Stop-Process -Id $paperInfo.pid -ErrorAction Stop
Write-Host 'Paper Agent stopped. Saved manuscript versions are retained.'
