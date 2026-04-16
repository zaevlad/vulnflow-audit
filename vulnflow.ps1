param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$command = if ($Args.Count -gt 0) { $Args[0] } else { $null }

if (Test-Path $venvPython) {
    & $venvPython "$PSScriptRoot\vulnflow.py" @Args
    exit $LASTEXITCODE
}

if ($command -eq "prepare") {
    python "$PSScriptRoot\vulnflow.py" @Args
    exit $LASTEXITCODE
}

Write-Host 'Project virtual environment was not found. Run "vulnflow prepare" first.'
exit 1
