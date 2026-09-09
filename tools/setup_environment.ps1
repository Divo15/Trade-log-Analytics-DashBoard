param(
    [string]$PythonPath = "",
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$environmentPath = Join-Path $projectRoot ".venv"
$environmentPython = Join-Path $environmentPath "Scripts\python.exe"

function Resolve-ProjectPython {
    if ($PythonPath) {
        if (-not (Test-Path -LiteralPath $PythonPath)) {
            throw "Python was not found at: $PythonPath"
        }
        & $PythonPath -c "import sys" 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw "Python exists but could not start: $PythonPath"
        }
        return (Resolve-Path -LiteralPath $PythonPath).Path
    }

    $knownLocations = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe")
    )
    foreach ($candidate in $knownLocations) {
        if (Test-Path -LiteralPath $candidate) {
            & $candidate -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
            Write-Warning "Skipping a Python installation that could not start: $candidate"
        }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        & $pythonCommand.Source -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return $pythonCommand.Source
        }
    }

    throw "A working Python 3.11, 3.12 or 3.13 was not found. Install Python 3.12, then run this setup again."
}

if ($Recreate -and (Test-Path -LiteralPath $environmentPath)) {
    $resolvedEnvironment = (Resolve-Path -LiteralPath $environmentPath).Path
    $resolvedProject = (Resolve-Path -LiteralPath $projectRoot).Path
    if (-not $resolvedEnvironment.StartsWith($resolvedProject, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove an environment outside the project."
    }
    Remove-Item -LiteralPath $resolvedEnvironment -Recurse -Force
}

if (-not (Test-Path -LiteralPath $environmentPython)) {
    $basePython = Resolve-ProjectPython
    Write-Host "Creating the project environment with $basePython"
    & $basePython -c "import sys; assert (3, 11) <= sys.version_info[:2] <= (3, 13), 'Use Python 3.11, 3.12 or 3.13'"
    if ($LASTEXITCODE -ne 0) { throw "The selected Python version is unsupported." }
    & $basePython -m venv $environmentPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $environmentPython)) {
        throw "Python could not create the project environment."
    }
}

Write-Host "Installing the dashboard and strategy libraries..."
& $environmentPython -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw "Could not prepare pip in the project environment." }
& $environmentPython -m pip install -e "$projectRoot[strategy]"
if ($LASTEXITCODE -ne 0) { throw "Could not install the dashboard strategy libraries." }

Write-Host "Verifying the shared backtest environment..."
& $environmentPython -u -c "import importlib,sys; modules=['duckdb','pandas','numpy','matplotlib','pyarrow','scipy','sklearn','seaborn','plotly','statsmodels','openpyxl']; [(print('Checking '+name+'...',flush=True),importlib.import_module(name)) for name in modules]; print('Environment ready: '+sys.executable,flush=True)"
if ($LASTEXITCODE -ne 0) { throw "One or more required strategy libraries failed verification." }

Write-Host "Setup complete. Open the browser dashboard with .\start_dashboard.cmd"
