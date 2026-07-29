[CmdletBinding()]
param(
    [int]$Port = 8080,
    [string]$ExpectedModel = "Qwen/Qwen3-0.6B-GGUF:Q8_0"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$pidPath = Join-Path $repoRoot ".runtime\local_slm\llama.pid"
$baseUrl = "http://127.0.0.1:$Port/v1"

if (-not (Test-Path -LiteralPath $pidPath)) {
    throw "PID file is missing: $pidPath"
}
$savedPid = [int](Get-Content -LiteralPath $pidPath -Raw)
$process = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
if (-not $process -or $process.ProcessName -notlike "llama-server*") {
    throw "Managed llama-server process is not running."
}

$started = Get-Date
$health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -Method Get -TimeoutSec 10
if ($health.status -notin @("ok", "no slot available")) {
    throw "/health returned unexpected status: $($health.status)"
}
$models = Invoke-RestMethod -Uri "$baseUrl/models" -Method Get -TimeoutSec 10
$modelIds = @($models.data | ForEach-Object { $_.id })
if (-not $modelIds) {
    throw "/v1/models returned no model IDs."
}
if ($ExpectedModel -notin $modelIds) {
    throw "Model mismatch: expected '$ExpectedModel', loaded '$($modelIds -join ', ')'."
}

$payload = @{
    model = $ExpectedModel
    messages = @(
        @{ role = "system"; content = "/no_think`nReturn only JSON." },
        @{ role = "user"; content = "Return {`"ok`":true}." }
    )
    max_tokens = 32
    temperature = 0
    stream = $false
    chat_template_kwargs = @{ enable_thinking = $false }
    response_format = @{ type = "json_object" }
} | ConvertTo-Json -Depth 10

$completion = Invoke-RestMethod -Uri "$baseUrl/chat/completions" -Method Post `
    -ContentType "application/json" -Body $payload -TimeoutSec 30
$elapsedMs = [int]((Get-Date) - $started).TotalMilliseconds
$content = [string]$completion.choices[0].message.content
if (-not $content) {
    throw "Chat completion returned empty content."
}
if ($content -match "(?i)<think|reasoning_content") {
    throw "Thinking content leaked into the completion."
}

Write-Host "Process: OK (PID $savedPid)"
Write-Host "Health: OK"
Write-Host "Model IDs: $($modelIds -join ', ')"
Write-Host "Expected model: $ExpectedModel"
Write-Host "Response time: $elapsedMs ms"
Write-Host "Completion: $content"
