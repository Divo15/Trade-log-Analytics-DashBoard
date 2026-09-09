param([int]$Port = 8790, [switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$environmentPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $environmentPython)) { throw "Run setup_environment.cmd first." }
$arguments = @("-m", "trade_log_dashboard.server", "--host", "127.0.0.1", "--port", $Port)
if ($NoBrowser) { $arguments += "--no-browser" }
& $environmentPython @arguments
exit $LASTEXITCODE
