[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$pidPath = Join-Path $repoRoot ".runtime\local_slm\ruadapt-llama.pid"
if (-not (Test-Path $pidPath)) {
    Write-Host "Ruadapt server is not managed or already stopped."
    exit 0
}
$savedPid = [int](Get-Content $pidPath -Raw)
$process = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
if ($process) {
    Stop-Process -Id $savedPid -Force
    Wait-Process -Id $savedPid -Timeout 10 -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $pidPath -Force
Write-Host "Stopped Ruadapt llama-server PID $savedPid."
