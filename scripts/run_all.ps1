$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (Test-Path ".\.venv\Scripts\python.exe") {
    & ".\.venv\Scripts\python.exe" -m meowbot.apps.run_all
    exit $LASTEXITCODE
}

python -m meowbot.apps.run_all
exit $LASTEXITCODE
