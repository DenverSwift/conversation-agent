[CmdletBinding()]
param(
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = Join-Path $repoRoot ".runtime\local_slm"
$pidPath = Join-Path $runtimeDir "llama.pid"
$logPath = Join-Path $runtimeDir "llama-cuda.log"
$errorLogPath = Join-Path $runtimeDir "llama-cuda.error.log"
$startupFile = Join-Path $runtimeDir "cuda-startup.json"
$installFile = Join-Path $runtimeDir "cuda-install.json"
$statusFile = Join-Path $runtimeDir "gpu-status.json"
$baseUrl = "http://127.0.0.1:$Port"
$reasons = [System.Collections.Generic.List[string]]::new()

function Add-Failure([string]$Reason) {
    $reasons.Add($Reason)
}

function Read-Json([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
}

function Get-GpuInfo {
    $line = (& nvidia-smi --query-gpu=name,driver_version,memory.used,memory.total `
        --format=csv,noheader,nounits | Select-Object -First 1)
    if (-not $line) {
        return $null
    }
    $parts = $line -split ",\s*"
    return [ordered]@{
        name = $parts[0].Trim()
        driver = $parts[1].Trim()
        memory_used_mib = [int]$parts[2]
        memory_total_mib = [int]$parts[3]
    }
}

$startup = Read-Json $startupFile
$install = Read-Json $installFile
$gpu = Get-GpuInfo
if (-not $gpu) {
    Add-Failure "nvidia-smi GPU query failed"
}

$process = $null
$pidValue = $null
if (Test-Path -LiteralPath $pidPath) {
    $pidValue = [int](Get-Content -LiteralPath $pidPath -Raw)
    $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
}
if (-not $process) {
    Add-Failure "saved llama-server process is not running"
}

$models = $null
try {
    $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 5
    $models = Invoke-RestMethod -Uri "$baseUrl/v1/models" -TimeoutSec 5
    if ($health.status -notin @("ok", "no slot available")) {
        Add-Failure "health endpoint returned $($health.status)"
    }
}
catch {
    Add-Failure "llama-server health/model endpoint failed: $($_.Exception.Message)"
}

$combined = (
    (Get-Content -LiteralPath $logPath -Raw -ErrorAction SilentlyContinue) +
    "`n" +
    (Get-Content -LiteralPath $errorLogPath -Raw -ErrorAction SilentlyContinue)
)
$backendConfirmed = (
    $combined -match "ggml_cuda_init.*found\s+[1-9][0-9]*\s+CUDA devices?" -or
    $combined -match "CUDA[0-9]*.*NVIDIA"
)
if (-not $backendConfirmed) {
    Add-Failure "CUDA backend initialization is not present in llama.cpp logs"
}
if ($combined -match "no usable GPU found") {
    Add-Failure "llama.cpp explicitly reported no usable GPU"
}

$offloadMatch = [regex]::Matches(
    $combined,
    "offload(?:ed|ing)\s+([0-9]+)(?:/([0-9]+))?\s+(?:repeating\s+)?layers?",
    [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
) | Select-Object -Last 1
$offloadedLayers = 0
$totalLayers = $null
if ($offloadMatch) {
    $offloadedLayers = [int]$offloadMatch.Groups[1].Value
    if ($offloadMatch.Groups[2].Success) {
        $totalLayers = [int]$offloadMatch.Groups[2].Value
    }
}
if ($offloadedLayers -le 0) {
    Add-Failure "no offloaded model layers were confirmed in llama.cpp logs"
}

$processListing = (& nvidia-smi 2>&1 | Out-String)
$processVisible = (
    $pidValue -and
    $processListing -match "\b$pidValue\b" -and
    $processListing -match "llama-server"
)
if (-not $processVisible) {
    Add-Failure "llama-server process is not visible in nvidia-smi"
}

$baselineVram = if ($startup) { $startup.baseline_vram_mib } else { $null }
$vramDelta = if ($gpu -and $null -ne $baselineVram) {
    [int]$gpu.memory_used_mib - [int]$baselineVram
}
else {
    $null
}
if ($null -ne $vramDelta -and $vramDelta -le 0) {
    Add-Failure "GPU memory usage did not increase after model startup"
}

$modelId = if ($models -and $models.data) {
    [string]$models.data[0].id
}
elseif ($startup) {
    [string]$startup.model
}
else {
    ""
}
$latencyMs = $null
$tokensPerSecond = $null
$completionTokens = $null
if ($models -and $modelId) {
    try {
        $payload = @{
            model = $modelId
            messages = @(@{ role = "user"; content = "Reply with: OK" })
            max_tokens = 12
            temperature = 0
            stream = $false
        } | ConvertTo-Json -Depth 6
        $timer = [System.Diagnostics.Stopwatch]::StartNew()
        $completion = Invoke-RestMethod -Uri "$baseUrl/v1/chat/completions" `
            -Method Post -ContentType "application/json" -Body $payload `
            -TimeoutSec 60
        $timer.Stop()
        $latencyMs = [int]$timer.ElapsedMilliseconds
        $completionTokens = [int]$completion.usage.completion_tokens
        if ($completion.timings.predicted_per_second) {
            $tokensPerSecond = [double]$completion.timings.predicted_per_second
        }
        elseif ($latencyMs -gt 0) {
            $tokensPerSecond = [math]::Round(
                $completionTokens / ($latencyMs / 1000.0),
                3
            )
        }
    }
    catch {
        Add-Failure "short completion failed: $($_.Exception.Message)"
    }
}

$ready = $reasons.Count -eq 0
$status = [ordered]@{
    ready = $ready
    reason = if ($ready) { $null } else { $reasons -join "; " }
    gpu_name = if ($gpu) { $gpu.name } else { $null }
    driver_version = if ($gpu) { $gpu.driver } else { $null }
    cuda_backend = $backendConfirmed
    llama_version = if ($install) { $install.llama_version } else { $null }
    cuda_runtime_package = if ($install) {
        $install.cuda_runtime_package
    } else {
        $null
    }
    model = $modelId
    pid = $pidValue
    offloaded_layers = $offloadedLayers
    total_layers = $totalLayers
    process_visible_in_nvidia_smi = $processVisible
    vram_used_mib = if ($gpu) { $gpu.memory_used_mib } else { $null }
    vram_total_mib = if ($gpu) { $gpu.memory_total_mib } else { $null }
    vram_baseline_mib = $baselineVram
    vram_delta_mib = $vramDelta
    completion_latency_ms = $latencyMs
    completion_tokens = $completionTokens
    tokens_per_second = $tokensPerSecond
    cpu_fallback = -not $ready
    checked_at = (Get-Date).ToUniversalTime().ToString("o")
}
$status | ConvertTo-Json -Depth 6 |
    Set-Content -LiteralPath $statusFile -Encoding utf8

Write-Host "GPU: $($status.gpu_name)"
Write-Host "CUDA backend: $(if ($backendConfirmed) { 'OK' } else { 'NO' })"
Write-Host "Model: $modelId"
Write-Host "GPU offload: $(if ($offloadedLayers -gt 0) { "confirmed ($offloadedLayers layers)" } else { 'not confirmed' })"
Write-Host "VRAM usage: $($status.vram_used_mib) / $($status.vram_total_mib) MiB (delta $vramDelta MiB)"
Write-Host "Completion latency: $latencyMs ms"
Write-Host "Tokens/sec: $tokensPerSecond"
Write-Host "CPU fallback: $($status.cpu_fallback.ToString().ToLowerInvariant())"
if ($ready) {
    Write-Host "Ready: YES"
    exit 0
}
Write-Host "Reason: $($status.reason)"
Write-Host "Ready: NO"
exit 1
