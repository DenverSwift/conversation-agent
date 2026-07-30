[CmdletBinding()]
param(
    [string]$Model = "Qwen/Qwen3-0.6B-GGUF:Q8_0",
    [int]$Port = 8080,
    [int]$ContextTokens = 4096
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = Join-Path $repoRoot ".runtime\local_slm"
$pathFile = Join-Path $runtimeDir "llama-server-cuda.path"
$pidPath = Join-Path $runtimeDir "llama.pid"
$logPath = Join-Path $runtimeDir "llama-cuda.log"
$errorLogPath = Join-Path $runtimeDir "llama-cuda.error.log"
$startupFile = Join-Path $runtimeDir "cuda-startup.json"
$baseUrl = "http://127.0.0.1:$Port"

function Get-UsedVram {
    $value = (& nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits |
        Select-Object -First 1)
    if (-not $value) {
        return $null
    }
    return [int]$value.Trim()
}

function Test-Endpoint {
    try {
        $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 3
        $models = Invoke-RestMethod -Uri "$baseUrl/v1/models" -TimeoutSec 3
        return (
            $health.status -in @("ok", "no slot available") -and
            $null -ne $models.data
        )
    }
    catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $pathFile)) {
    throw (
        "CUDA llama-server is not installed. Run " +
        "scripts\local_slm\install_llama_cpp_cuda.ps1 first."
    )
}
$server = (Get-Content -LiteralPath $pathFile -Raw).Trim()
if (-not (Test-Path -LiteralPath $server)) {
    throw "Saved CUDA llama-server path does not exist: $server"
}

$helpCommand = "`"$server`" --help 2>&1"
$helpLines = & $env:ComSpec /d /s /c $helpCommand
$helpExitCode = $LASTEXITCODE
$help = ($helpLines | Out-String)
if ($helpExitCode -ne 0) {
    throw "llama-server --help failed."
}
$gpuFlag = if ($help -match "--n-gpu-layers") {
    "--n-gpu-layers"
}
elseif ($help -match "--gpu-layers") {
    "--gpu-layers"
}
elseif ($help -match "(?m)\s-ngl[\s,]") {
    "-ngl"
}
else {
    throw "This llama-server does not advertise a GPU offload argument."
}
$gpuValue = if ($help -match "exact number,\s*'auto',\s*or\s*'all'") {
    "all"
}
else {
    "999"
}

if (Test-Path -LiteralPath $pidPath) {
    $savedPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    $savedProcess = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
    if ($savedProcess -and (Test-Endpoint)) {
        throw (
            "A model server is already running as PID $savedPid. Stop it before " +
            "starting the verified CUDA server."
        )
    }
    if (-not $savedProcess) {
        Remove-Item -LiteralPath $pidPath -Force
    }
}

$listener = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port `
    -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    throw "Port $Port is already used by PID $($listener.OwningProcess)."
}

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$baselineVram = Get-UsedVram
$arguments = @(
    "--hf-repo", $Model,
    "--host", "127.0.0.1",
    "--port", "$Port",
    "--ctx-size", "$ContextTokens",
    "--n-predict", "256",
    "--jinja",
    "--verbosity", "4",
    $gpuFlag, $gpuValue
)
Write-Host "Starting $Model with $gpuFlag $gpuValue on $baseUrl ..."
$process = Start-Process -FilePath $server -ArgumentList $arguments `
    -WorkingDirectory $repoRoot -RedirectStandardOutput $logPath `
    -RedirectStandardError $errorLogPath -WindowStyle Hidden -PassThru
Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ascii

$startup = [ordered]@{
    pid = $process.Id
    model = $Model
    port = $Port
    server = $server
    gpu_flag = $gpuFlag
    gpu_value = $gpuValue
    baseline_vram_mib = $baselineVram
    started_at = (Get-Date).ToUniversalTime().ToString("o")
}
$startup | ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath $startupFile -Encoding utf8

$deadline = (Get-Date).AddMinutes(8)
while ((Get-Date) -lt $deadline) {
    $process.Refresh()
    if ($process.HasExited) {
        Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
        $tail = Get-Content -LiteralPath $errorLogPath -Tail 50 `
            -ErrorAction SilentlyContinue
        throw "llama-server exited during startup.`n$($tail -join "`n")"
    }
    if (Test-Endpoint) {
        $combined = (
            (Get-Content -LiteralPath $logPath -Raw -ErrorAction SilentlyContinue) +
            "`n" +
            (Get-Content -LiteralPath $errorLogPath -Raw -ErrorAction SilentlyContinue)
        )
        if ($combined -match "no usable GPU found") {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "llama.cpp reported no usable GPU; CPU fallback is forbidden."
        }
        if (
            $combined -notmatch "CUDA" -or
            $combined -notmatch "offload(ed|ing).*(layer|layers)"
        ) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw (
                "Server is healthy, but CUDA layer offload was not confirmed in logs. " +
                "See $errorLogPath"
            )
        }
        Write-Host "Server health: OK"
        & (Join-Path $PSScriptRoot "check_gpu_offload.ps1") -Port $Port
        exit $LASTEXITCODE
    }
    Start-Sleep -Seconds 2
}

Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
throw "llama-server did not become ready within 8 minutes. See $errorLogPath"
