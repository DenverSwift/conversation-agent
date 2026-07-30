[CmdletBinding()]
param(
    [ValidateSet("Q6_K", "Q5_K_M")]
    [string]$Quantization = "Q6_K"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = Join-Path $repoRoot ".runtime\local_slm"
$manifestPath = Join-Path $runtimeDir "ruadapt-model.json"
$repository = "RefalMachine/RuadaptQwen3-4B-Instruct-GGUF"
$revision = "da30124570330edcb7fe487c5b1f1ba0b0c09721"
$profile = if ($Quantization -eq "Q6_K") {
    "ruadapt_qwen3_4b_q6"
} else {
    "ruadapt_qwen3_4b_q5"
}
$filename = "$Quantization.gguf"
$expected = @{
    Q6_K = @{
        size = 3295488128
        sha256 = "a206b1994822653e1da29ce76e96dc57f0f2a899f09a44466b94d3c043b82d29"
    }
    Q5_K_M = @{
        size = 2878740608
        sha256 = "a2fe9351a264ee29234e5f09e949e57a3e7648984c3d503d83ee1c096c57c1b9"
    }
}

$api = Invoke-RestMethod -Uri (
    "https://huggingface.co/api/models/$repository/tree/$revision" +
    "?recursive=true&expand=true"
)
$remote = $api | Where-Object { $_.path -eq $filename } | Select-Object -First 1
if (-not $remote) {
    throw "Pinned revision $revision does not contain $filename."
}
if (
    [int64]$remote.size -ne [int64]$expected[$Quantization].size -or
    [string]$remote.lfs.oid -ne [string]$expected[$Quantization].sha256
) {
    throw "Pinned Hugging Face metadata differs from the recorded file manifest."
}

$hfHome = if ($env:HF_HOME) {
    $env:HF_HOME
} else {
    Join-Path $env:USERPROFILE ".cache\huggingface"
}
$snapshot = Join-Path $hfHome (
    "hub\models--RefalMachine--RuadaptQwen3-4B-Instruct-GGUF" +
    "\snapshots\$revision"
)
$modelPath = Join-Path $snapshot $filename
New-Item -ItemType Directory -Path $snapshot, $runtimeDir -Force | Out-Null

$valid = $false
if (Test-Path -LiteralPath $modelPath) {
    $file = Get-Item -LiteralPath $modelPath
    if ($file.Length -eq [int64]$expected[$Quantization].size) {
        $hash = (Get-FileHash -LiteralPath $modelPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $valid = $hash -eq $expected[$Quantization].sha256
    }
}
if (-not $valid) {
    if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) {
        throw "curl.exe is required for resumable model download."
    }
    $url = "https://huggingface.co/$repository/resolve/$revision/$filename"
    Write-Host "Downloading pinned $repository/$filename ..."
    & curl.exe -L --fail --retry 5 --continue-at - --output $modelPath $url
    if ($LASTEXITCODE -ne 0) {
        throw "Model download failed with exit code $LASTEXITCODE."
    }
}

$file = Get-Item -LiteralPath $modelPath
if ($file.Length -ne [int64]$expected[$Quantization].size) {
    throw "Downloaded model size mismatch: $($file.Length)."
}
$hash = (Get-FileHash -LiteralPath $modelPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($hash -ne $expected[$Quantization].sha256) {
    throw "Downloaded model SHA256 mismatch."
}
$manifest = [ordered]@{
    profile = $profile
    repository = $repository
    resolved_revision = $revision
    filename = $filename
    quantization = $Quantization
    size_bytes = $file.Length
    sha256 = $hash
    model_path = $modelPath
    source_repository = "RefalMachine/RuadaptQwen3-4B-Instruct"
    source_revision = "03bcd55e56b02175bcc863c4761613b1bda8302b"
    downloaded_at = (Get-Date).ToUniversalTime().ToString("o")
}
$manifest | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath $manifestPath -Encoding utf8
Write-Host "Model: $modelPath"
Write-Host "SHA256: $hash"
Write-Host "Size: $($file.Length) bytes"
Write-Host "Ready: YES"
