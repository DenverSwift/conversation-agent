[CmdletBinding()]
param(
    [string]$ReleaseTag = "latest",
    [ValidateSet("auto", "12.4", "13.3")]
    [string]$CudaRuntime = "auto"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = Join-Path $repoRoot ".runtime\local_slm"
$downloadDir = Join-Path $runtimeDir "downloads"
$installRoot = Join-Path $runtimeDir "cuda"
$pathFile = Join-Path $runtimeDir "llama-server-cuda.path"
$metadataFile = Join-Path $runtimeDir "cuda-install.json"

function Get-NvidiaInfo {
    if (-not (Get-Command "nvidia-smi" -ErrorAction SilentlyContinue)) {
        throw "nvidia-smi is unavailable. Install a compatible NVIDIA driver first."
    }
    $line = (& nvidia-smi --query-gpu=name,driver_version,memory.total `
        --format=csv,noheader,nounits | Select-Object -First 1)
    if (-not $line) {
        throw "nvidia-smi could not query the NVIDIA GPU."
    }
    $parts = $line -split ",\s*"
    return @{
        name = $parts[0].Trim()
        driver = $parts[1].Trim()
        memory_total_mib = [int]$parts[2]
    }
}

function Get-CudaVersion {
    $text = (& nvidia-smi 2>&1 | Out-String)
    $match = [regex]::Match($text, "CUDA Version:\s*([0-9.]+)")
    if (-not $match.Success) {
        throw "nvidia-smi did not report a CUDA compatibility version."
    }
    return $match.Groups[1].Value
}

function Get-Release {
    $headers = @{ "User-Agent" = "conversation-agent-stage25" }
    $uri = if ($ReleaseTag -eq "latest") {
        "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
    }
    else {
        "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/$ReleaseTag"
    }
    return Invoke-RestMethod -Uri $uri -Headers $headers
}

function Test-CudaServer([string]$ServerPath) {
    if (-not (Test-Path -LiteralPath $ServerPath)) {
        return $false
    }
    $deviceLines = & $ServerPath --list-devices 2>&1
    $exitCode = $LASTEXITCODE
    $devices = ($deviceLines | Out-String)
    $script:deviceOutput = $devices.Trim()
    return (
        $exitCode -eq 0 -and
        $devices -match "CUDA" -and
        $devices -match "NVIDIA"
    )
}

New-Item -ItemType Directory -Path $runtimeDir, $downloadDir, $installRoot -Force |
    Out-Null

$gpu = Get-NvidiaInfo
$driverCuda = Get-CudaVersion
Write-Host "GPU: $($gpu.name)"
Write-Host "Driver: $($gpu.driver)"
Write-Host "Driver CUDA compatibility: $driverCuda"

if (
    (Test-Path -LiteralPath $pathFile) -and
    (Test-CudaServer ((Get-Content -LiteralPath $pathFile -Raw).Trim()))
) {
    $existing = (Get-Content -LiteralPath $pathFile -Raw).Trim()
    Write-Host "CUDA-capable llama-server already installed: $existing"
    Write-Host $deviceOutput
    Write-Host "Ready: YES"
    exit 0
}

$selectedRuntime = $CudaRuntime
if ($selectedRuntime -eq "auto") {
    $driverMajorMinor = [version]$driverCuda
    $selectedRuntime = if ($driverMajorMinor -ge [version]"13.3") {
        "13.3"
    }
    else {
        "12.4"
    }
}

$release = Get-Release
$tag = [string]$release.tag_name
$binaryName = "llama-$tag-bin-win-cuda-$selectedRuntime-x64.zip"
$runtimeName = "cudart-llama-bin-win-cuda-$selectedRuntime-x64.zip"
$binaryAsset = $release.assets | Where-Object { $_.name -eq $binaryName } |
    Select-Object -First 1
$runtimeAsset = $release.assets | Where-Object { $_.name -eq $runtimeName } |
    Select-Object -First 1
if (-not $binaryAsset -or -not $runtimeAsset) {
    throw (
        "Official release $tag does not contain both required assets: " +
        "$binaryName and $runtimeName"
    )
}

$installDir = Join-Path $installRoot "$tag-cuda-$selectedRuntime"
$binaryZip = Join-Path $downloadDir $binaryName
$runtimeZip = Join-Path $downloadDir $runtimeName
New-Item -ItemType Directory -Path $installDir -Force | Out-Null

foreach ($asset in @($binaryAsset, $runtimeAsset)) {
    $destination = Join-Path $downloadDir $asset.name
    if (-not (Test-Path -LiteralPath $destination)) {
        Write-Host "Downloading $($asset.name)..."
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $destination
    }
    else {
        Write-Host "Using cached $($asset.name)"
    }
}

Write-Host "Extracting official llama.cpp CUDA archives..."
Expand-Archive -LiteralPath $binaryZip -DestinationPath $installDir -Force
Expand-Archive -LiteralPath $runtimeZip -DestinationPath $installDir -Force
$server = Get-ChildItem -LiteralPath $installDir -Filter "llama-server.exe" `
    -Recurse -File | Select-Object -First 1
if (-not $server) {
    throw "The official CUDA archive did not contain llama-server.exe."
}

if (-not (Test-CudaServer $server.FullName)) {
    throw (
        "llama-server starts but no NVIDIA CUDA device is available. " +
        "Selected CUDA runtime: $selectedRuntime; driver compatibility: $driverCuda. " +
        "Device output: $deviceOutput"
    )
}

$versionCommand = "`"$($server.FullName)`" --version 2>&1"
$versionLines = & $env:ComSpec /d /s /c $versionCommand
$versionExitCode = $LASTEXITCODE
if ($versionExitCode -ne 0) {
    throw "llama-server --version failed with exit code $versionExitCode."
}
$version = ($versionLines | Out-String).Trim()
$metadata = [ordered]@{
    ready = $true
    release_tag = $tag
    llama_server = $server.FullName
    llama_version = $version
    cuda_runtime_package = $selectedRuntime
    driver_cuda_compatibility = $driverCuda
    gpu_name = $gpu.name
    driver_version = $gpu.driver
    gpu_memory_total_mib = $gpu.memory_total_mib
    device_output = $deviceOutput
    installed_at = (Get-Date).ToUniversalTime().ToString("o")
}
Set-Content -LiteralPath $pathFile -Value $server.FullName -Encoding utf8
$metadata | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath $metadataFile -Encoding utf8

Write-Host $version
Write-Host $deviceOutput
Write-Host "CUDA backend: OK"
Write-Host "llama-server: $($server.FullName)"
Write-Host "Ready: YES"
