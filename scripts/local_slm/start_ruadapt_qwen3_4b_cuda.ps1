[CmdletBinding()]
param([int]$Port = 8080)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = Join-Path $repoRoot ".runtime\local_slm"
$manifestPath = Join-Path $runtimeDir "ruadapt-model.json"
$serverPathFile = Join-Path $runtimeDir "llama-server-cuda.path"
$pidPath = Join-Path $runtimeDir "ruadapt-llama.pid"
$stdout = Join-Path $runtimeDir "ruadapt-cuda.log"
$stderr = Join-Path $runtimeDir "ruadapt-cuda.error.log"
$startupPath = Join-Path $runtimeDir "ruadapt-startup.json"
if (-not (Test-Path $manifestPath) -or -not (Test-Path $serverPathFile)) {
    throw "Download the model and install CUDA llama.cpp first."
}
$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
$server = (Get-Content $serverPathFile -Raw).Trim()
$help = & $env:ComSpec /d /s /c "`"$server`" --help 2>&1" | Out-String
foreach ($required in @("--n-gpu-layers", "--parallel", "--flash-attn")) {
    if ($help -notmatch [regex]::Escape($required)) {
        throw "llama-server does not support required flag $required."
    }
}
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    throw "Port $Port is already used by PID $($listener.OwningProcess)."
}
$baseline = [int]((& nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits |
    Select-Object -First 1).Trim())
$alias = if ($manifest.quantization -eq "Q6_K") {
    "ruadapt-qwen3-4b-q6"
} else {
    "ruadapt-qwen3-4b-q5"
}
$arguments = @(
    "--model", $manifest.model_path,
    "--alias", $alias,
    "--host", "127.0.0.1",
    "--port", "$Port",
    "--ctx-size", "4096",
    "--parallel", "1",
    "--n-gpu-layers", "all",
    "--flash-attn", "on",
    "--jinja",
    "--verbosity", "4"
)
$process = Start-Process -FilePath $server -ArgumentList $arguments `
    -WorkingDirectory $repoRoot -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
Set-Content $pidPath $process.Id -Encoding ascii
@{
    pid = $process.Id
    baseline_vram_mib = $baseline
    model = $alias
    quantization = $manifest.quantization
    started_at = (Get-Date).ToUniversalTime().ToString("o")
} | ConvertTo-Json | Set-Content $startupPath -Encoding utf8
$deadline = (Get-Date).AddMinutes(8)
while ((Get-Date) -lt $deadline) {
    $process.Refresh()
    if ($process.HasExited) {
        throw "Ruadapt llama-server exited.`n$((Get-Content $stderr -Tail 60) -join "`n")"
    }
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($health.status -in @("ok", "no slot available")) {
            powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
                (Join-Path $PSScriptRoot "check_ruadapt_qwen3_4b.ps1") -Port $Port
            exit $LASTEXITCODE
        }
    } catch {}
    Start-Sleep -Seconds 2
}
throw "Ruadapt server did not become ready."
