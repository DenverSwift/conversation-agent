[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = Join-Path $repoRoot ".runtime\local_slm"
$pidPath = Join-Path $runtimeDir "llama.pid"

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host "Qwen server is not managed by this experiment."
    exit 0
}

$savedPid = [int](Get-Content -LiteralPath $pidPath -Raw)
$process = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $pidPath -Force
    Write-Host "Removed stale PID file."
    exit 0
}

if ($process.ProcessName -notlike "llama-server*") {
    throw "PID $savedPid belongs to '$($process.ProcessName)', not llama-server. Refusing to stop it."
}

Stop-Process -Id $savedPid
try {
    Wait-Process -Id $savedPid -Timeout 15 -ErrorAction Stop
}
catch {
    Stop-Process -Id $savedPid -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
Write-Host "Stopped llama-server PID $savedPid."
