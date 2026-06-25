# run_app.ps1
# Launches Better_Scope.
#
# Workflow:
#   1. Refresh the pinned pymeasure fork entry in uv.lock so the app picks up
#      the latest fork commit (uv lock --upgrade-package pymeasure).
#   2. Run the app via "uv run", which syncs the environment from the lock.
#
# Run directly with "pwsh -File run_app.ps1" or via the run_app.bat wrapper.

Set-Location -Path $PSScriptRoot

Write-Host "Updating pymeasure fork entry in uv.lock..."
uv lock --upgrade-package pymeasure
if ($LASTEXITCODE -ne 0) {
    Write-Warning "uv lock --upgrade-package pymeasure failed (exit $LASTEXITCODE). Continuing with the existing lock."
}

Write-Host "Starting Better_Scope..."
uv run python main.py
$appExit = $LASTEXITCODE

if ($appExit -ne 0) {
    Write-Error "Better_Scope exited with code $appExit."
}

exit $appExit
