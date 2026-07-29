[CmdletBinding()]
param(
    [string]$Model = "Qwen/Qwen3-0.6B-GGUF:Q8_0",
    [int]$Port = 8080,
    [int]$ContextTokens = 4096
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = Join-Path $repoRoot ".runtime\local_slm"
$pidPath = Join-Path $runtimeDir "llama.pid"
$serverPathFile = Join-Path $runtimeDir "llama-server.path"
$logPath = Join-Path $runtimeDir "llama-server.log"
$errorLogPath = Join-Path $runtimeDir "llama-server.error.log"
$baseUrl = "http://127.0.0.1:$Port"

function Find-LlamaServer {
    if (Test-Path -LiteralPath $serverPathFile) {
        $savedPath = (Get-Content -LiteralPath $serverPathFile -Raw).Trim()
        if (Test-Path -LiteralPath $savedPath) {
            return $savedPath
        }
    }
    $command = Get-Command "llama-server" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $packagesRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path -LiteralPath $packagesRoot) {
        $candidate = Get-ChildItem -LiteralPath $packagesRoot -Filter "llama-server.exe" -Recurse -File -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($candidate) {
            return $candidate.FullName
        }
    }
    return $null
}

function Test-ModelsEndpoint {
    try {
        $health = Invoke-RestMethod -Uri "$baseUrl/health" -Method Get -TimeoutSec 2
        if ($health.status -notin @("ok", "no slot available")) {
            return $false
        }
        $response = Invoke-RestMethod -Uri "$baseUrl/v1/models" -Method Get -TimeoutSec 2
        return $null -ne $response.data
    }
    catch {
        return $false
    }
}

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

if (Test-Path -LiteralPath $pidPath) {
    $savedPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    $savedProcess = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
    if ($savedProcess -and (Test-ModelsEndpoint)) {
        Write-Host "Qwen server is already running (PID $savedPid)."
        exit 0
    }
    if (-not $savedProcess) {
        Remove-Item -LiteralPath $pidPath -Force
    }
}

$listener = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    throw "Port $Port is already used by PID $($listener.OwningProcess); refusing to start a second server."
}

$server = Find-LlamaServer
if (-not $server) {
    throw "llama-server is not installed. Run scripts\local_slm\install_llama_cpp.ps1 first."
}

$arguments = @(
    "--hf-repo", $Model,
    "--host", "127.0.0.1",
    "--port", "$Port",
    "--ctx-size", "$ContextTokens",
    "--n-predict", "256",
    "--jinja"
)

Write-Host "Starting $Model on $baseUrl ..."
$process = Start-Process -FilePath $server -ArgumentList $arguments -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $logPath -RedirectStandardError $errorLogPath -WindowStyle Hidden -PassThru
Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ascii

$deadline = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $deadline) {
    if ($process.HasExited) {
        Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
        $tail = if (Test-Path -LiteralPath $errorLogPath) {
            Get-Content -LiteralPath $errorLogPath -Tail 30
        }
        throw "llama-server exited during startup.`n$($tail -join "`n")"
    }
    if (Test-ModelsEndpoint) {
        Write-Host "Ready: YES"
        Write-Host "PID: $($process.Id)"
        Write-Host "Log: $logPath"
        exit 0
    }
    Start-Sleep -Seconds 2
    $process.Refresh()
}

Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
throw "llama-server did not become ready within 5 minutes. See $errorLogPath"
