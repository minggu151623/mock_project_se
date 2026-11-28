param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('backend', 'frontend')]
    [string] $Service,
    [ValidateRange(1024, 65535)]
    [int] $BackendPort = 8766
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

if ($Service -eq 'backend') {
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw 'Run scripts\setup_fe2.ps1 first to install backend dependencies.'
    }

    Push-Location $projectRoot
    try {
        Write-Host "Starting backend. API documentation: http://127.0.0.1:$BackendPort/docs"
        & $venvPython -m uvicorn backend.main:create_app --factory --host 127.0.0.1 --port $BackendPort
    } finally {
        Pop-Location
    }
    exit $LASTEXITCODE
}

$frontendRoot = Join-Path $projectRoot 'frontend'
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npm) {
    $npmPath = 'C:\Program Files\nodejs\npm.cmd'
    if (-not (Test-Path -LiteralPath $npmPath)) { throw 'Node.js/npm was not found. Install Node.js and try again.' }
} else {
    $npmPath = $npm.Source
}
$env:PATH = (Split-Path -Parent $npmPath) + [IO.Path]::PathSeparator + $env:PATH

if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot 'node_modules\.bin\vite.cmd'))) {
    throw 'Run scripts\setup_fe2.ps1 first to install frontend dependencies.'
}

Push-Location $frontendRoot
try {
    $env:FE2_BACKEND_PORT = [string] $BackendPort
    & $npmPath run dev -- --config vite.fe2.config.js
} finally {
    Pop-Location
}
exit $LASTEXITCODE
