[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = Join-Path $repoRoot ".runtime\local_slm"
$serverPathFile = Join-Path $runtimeDir "llama-server.path"

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

function Test-LlamaServer([string]$Path) {
    & $Path --version | Out-Host
    return $LASTEXITCODE -eq 0
}

function Build-LlamaServerFromSource {
    if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {
        throw "git is required for the local source-build fallback."
    }
    if (-not (Get-Command "cmake" -ErrorAction SilentlyContinue)) {
        throw "cmake is required for the local source-build fallback."
    }

    $sourceDir = Join-Path $runtimeDir "src\llama.cpp"
    $buildDir = Join-Path $runtimeDir "build\llama.cpp"
    if (-not (Test-Path -LiteralPath (Join-Path $sourceDir ".git"))) {
        New-Item -ItemType Directory -Path (Split-Path $sourceDir) -Force | Out-Null
        & git clone --depth 1 --branch b10173 https://github.com/ggml-org/llama.cpp.git $sourceDir | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to clone official llama.cpp source."
        }
    }

    & cmake -S $sourceDir -B $buildDir -G Ninja `
        -DCMAKE_BUILD_TYPE=Release `
        -DCMAKE_C_FLAGS=-D_WIN32_WINNT=0x0601 `
        -DCMAKE_CXX_FLAGS=-D_WIN32_WINNT=0x0A00 `
        -DGGML_VULKAN=OFF `
        -DGGML_NATIVE=OFF `
        -DBUILD_SHARED_LIBS=OFF `
        -DLLAMA_BUILD_SERVER=ON `
        -DLLAMA_BUILD_TESTS=OFF `
        -DLLAMA_BUILD_EXAMPLES=OFF | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to configure the local llama.cpp build."
    }
    & cmake --build $buildDir --target llama-server --config Release --parallel | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to build llama-server locally."
    }

    $builtServer = Join-Path $buildDir "bin\llama-server.exe"
    if (-not (Test-Path -LiteralPath $builtServer)) {
        throw "Local build completed without llama-server.exe."
    }
    return $builtServer
}

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$server = Find-LlamaServer
if (-not $server) {
    if (-not (Get-Command "winget" -ErrorAction SilentlyContinue)) {
        throw "winget is not available. Install App Installer, then rerun this script."
    }

    Write-Host "Installing llama.cpp with winget..."
    & winget install --id ggml.llamacpp --exact --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget failed to install llama.cpp (exit code $LASTEXITCODE)."
    }
    $server = Find-LlamaServer
}

if (-not $server -or -not (Test-LlamaServer $server)) {
    Write-Warning "The packaged llama-server cannot run on this Windows installation; building official b10173 CPU sources locally."
    $server = Build-LlamaServerFromSource
}

Set-Content -LiteralPath $serverPathFile -Value $server -Encoding utf8
Write-Host "llama-server: $server"
if (-not (Test-LlamaServer $server)) {
    throw "llama-server --version failed (exit code $LASTEXITCODE)."
}
