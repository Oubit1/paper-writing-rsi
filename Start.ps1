param([int]$Port = 8766, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$paperRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$paperUrl = "http://127.0.0.1:$Port"
$paperReady = $false
try {
    $paperHealth = Invoke-RestMethod -Uri "$paperUrl/api/health" -TimeoutSec 2
    if ($paperHealth.ok -and $paperHealth.root -eq $paperRoot) { $paperReady = $true }
    elseif ($paperHealth.ok) { throw 'The selected port belongs to another Paper Agent workspace.' }
} catch [System.Net.WebException] { }
if (-not $paperReady) {
    $paperPython = Join-Path $paperRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $paperPython)) { throw 'Run Install.ps1 first to install the local Python environment.' }
    New-Item -ItemType Directory -Path (Join-Path $paperRoot 'runtime') -Force | Out-Null
    $paperProcess = Start-Process -FilePath $paperPython -ArgumentList @('-m', 'app.server', '--port', "$Port") -WorkingDirectory $paperRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $paperRoot 'runtime\server.out.log') -RedirectStandardError (Join-Path $paperRoot 'runtime\server.err.log')
    for ($paperAttempt = 0; $paperAttempt -lt 40; $paperAttempt++) {
        Start-Sleep -Milliseconds 250
        try {
            $paperHealth = Invoke-RestMethod -Uri "$paperUrl/api/health" -TimeoutSec 1
            if ($paperHealth.ok -and $paperHealth.root -eq $paperRoot) { $paperReady = $true; break }
        } catch { }
        if ($paperProcess.HasExited) { break }
    }
    if (-not $paperReady) { throw "Paper Agent failed to start. See $paperRoot\runtime\server.err.log" }
}
Write-Host "Paper Agent: $paperUrl"
if (-not $NoBrowser) { Start-Process $paperUrl }
