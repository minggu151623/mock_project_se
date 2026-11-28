$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$frontendRoot = Join-Path $projectRoot 'frontend'
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host 'Creating Python virtual environment...'
    python -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python virtual environment.' }
}

Write-Host 'Installing backend dependencies...'
& $venvPython -m pip install -r (Join-Path $projectRoot 'requirements-dev.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install Python dependencies. Check the internet connection and try again.' }

$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npm) {
    $npmPath = 'C:\Program Files\nodejs\npm.cmd'
    if (-not (Test-Path -LiteralPath $npmPath)) { throw 'Node.js/npm was not found. Install Node.js and try again.' }
} else {
    $npmPath = $npm.Source
}
$env:PATH = (Split-Path -Parent $npmPath) + [IO.Path]::PathSeparator + $env:PATH

if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot 'node_modules\.bin\vite.cmd'))) {
    Write-Host 'Installing frontend dependencies...'
    Push-Location $frontendRoot
    try {
        & $npmPath ci
        if ($LASTEXITCODE -ne 0) { throw 'Could not install frontend dependencies.' }
    } finally {
        Pop-Location
    }
}

Write-Host 'Preparing sample accounts and confirmed orders...'
Push-Location $projectRoot
try {
    & $venvPython -m backend.seed_be3_dev
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare sample data.' }
} finally {
    Pop-Location
}

Write-Host 'FE2 setup is ready. Run scripts\run_fe2.ps1 -Service backend and -Service frontend in separate terminals.'
