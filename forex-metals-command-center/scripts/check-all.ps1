# Runs every phase gate locally (Windows). Stops at the first failure.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Step($name, [scriptblock]$block) {
    Write-Host "==> $name" -ForegroundColor Cyan
    & $block
    if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: $name" -ForegroundColor Red; exit $LASTEXITCODE }
}

Push-Location "$root\services\api"
try {
    Step "api: uv sync"       { uv sync --frozen --python 3.12 }
    Step "api: ruff check"    { uv run ruff check . }
    Step "api: ruff format"   { uv run ruff format --check . }
    Step "api: mypy"          { uv run mypy app }
    Step "api: pytest"        { uv run pytest }
} finally { Pop-Location }

Push-Location $root
try {
    $env:NEXT_TELEMETRY_DISABLED = "1"
    Step "web: typecheck"     { npm run typecheck }
    Step "web: tests"         { npm test }
    Step "web: build"         { npm run build }
    $bash = (Get-Command bash -ErrorAction SilentlyContinue).Source
    if (-not $bash) { $bash = Join-Path $env:ProgramFiles "Git\bin\bash.exe" }
    if (-not (Test-Path $bash)) { Write-Host "FAILED: guardrails need bash (install Git for Windows)" -ForegroundColor Red; exit 1 }
    Step "guardrails"         { & $bash scripts/check-guardrails.sh }
} finally { Pop-Location }

Write-Host "All checks passed." -ForegroundColor Green
