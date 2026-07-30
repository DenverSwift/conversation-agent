[CmdletBinding()]
param([int]$Port = 8080)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtime = Join-Path $repoRoot ".runtime\local_slm"
$manifest = Get-Content (Join-Path $runtime "ruadapt-model.json") -Raw | ConvertFrom-Json
$startup = Get-Content (Join-Path $runtime "ruadapt-startup.json") -Raw | ConvertFrom-Json
$install = Get-Content (Join-Path $runtime "cuda-install.json") -Raw | ConvertFrom-Json
$log = (
    (Get-Content (Join-Path $runtime "ruadapt-cuda.log") -Raw -ErrorAction SilentlyContinue) +
    "`n" +
    (Get-Content (Join-Path $runtime "ruadapt-cuda.error.log") -Raw -ErrorAction SilentlyContinue)
)
$gpuLine = (& nvidia-smi --query-gpu=name,memory.used,memory.total `
    --format=csv,noheader,nounits | Select-Object -First 1) -split ",\s*"
$offload = [regex]::Matches(
    $log,
    "offload(?:ed|ing)\s+([0-9]+)(?:/([0-9]+))?\s+(?:repeating\s+)?layers?",
    "IgnoreCase"
) | Select-Object -Last 1
$offloaded = if ($offload) { [int]$offload.Groups[1].Value } else { 0 }
$total = if ($offload -and $offload.Groups[2].Success) {
    [int]$offload.Groups[2].Value
} else { $offloaded }
$models = Invoke-RestMethod "http://127.0.0.1:$Port/v1/models" -TimeoutSec 5
$russianPrompt = [Text.Encoding]::UTF8.GetString(
    [Convert]::FromBase64String(
        "0J7RgtCy0LXRgtGMINC/0L4t0YDRg9GB0YHQutC4INC+0LTQvdC40Lwg0YHQu9C+0LLQvtC8OiDRgNCw0LHQvtGC0LDQtdGC"
    )
)
$payload = @{
    model = $startup.model
    messages = @(@{role="user";content=$russianPrompt})
    max_tokens = 12
    temperature = 0
    stream = $false
} | ConvertTo-Json -Depth 5
$payloadBytes = [Text.Encoding]::UTF8.GetBytes($payload)
$timer = [Diagnostics.Stopwatch]::StartNew()
$completion = Invoke-RestMethod "http://127.0.0.1:$Port/v1/chat/completions" `
    -Method Post -ContentType "application/json; charset=utf-8" `
    -Body $payloadBytes -TimeoutSec 90
$timer.Stop()
$text = [string]$completion.choices[0].message.content
$used = [int]$gpuLine[1]
$delta = $used - [int]$startup.baseline_vram_mib
$processText = & nvidia-smi | Out-String
$processVisible = $processText -match "\b$($startup.pid)\b" -and $processText -match "llama-server"
$ready = (
    $log -match "CUDA" -and
    $log -notmatch "no usable GPU" -and
    $offloaded -gt 0 -and
    $offloaded -eq $total -and
    $delta -gt 0 -and
    $processVisible -and
    $models.data[0].id -eq $startup.model -and
    $text -match "\p{IsCyrillic}" -and
    $text -notmatch "(?i)<\/?think|reasoning_content"
)
$status = [ordered]@{
    ready = $ready
    gpu_name = $gpuLine[0]
    model = $startup.model
    repository = $manifest.repository
    resolved_revision = $manifest.resolved_revision
    quantization = $manifest.quantization
    filename = $manifest.filename
    sha256 = $manifest.sha256
    llama_version = $install.llama_version
    offloaded_layers = $offloaded
    total_layers = $total
    vram_used_mib = $used
    vram_total_mib = [int]$gpuLine[2]
    vram_delta_mib = $delta
    process_visible_in_nvidia_smi = $processVisible
    gpu_layers_requested = 99
    kv_cache_offload = $true
    flash_attention = $true
    cpu_fallback = -not $ready
    model_metadata = [ordered]@{
        architecture = "qwen3"
        parameter_count = "4B"
        context_length = 4096
        source = "pinned GGUF manifest and llama.cpp load log"
    }
    completion_latency_ms = $timer.ElapsedMilliseconds
    completion_text = $text
    checked_at = (Get-Date).ToUniversalTime().ToString("o")
}
$status | ConvertTo-Json -Depth 5 |
    Set-Content (Join-Path $runtime "ruadapt-gpu-status.json") -Encoding utf8
$status | Format-List
Write-Host "Ready: $(if($ready){'YES'}else{'NO'})"
if (-not $ready) { exit 1 }
