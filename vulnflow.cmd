@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
set "COMMAND=%~1"

if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" "%SCRIPT_DIR%vulnflow.py" %*
    exit /b %errorlevel%
)

if /I "%COMMAND%"=="prepare" (
    python "%SCRIPT_DIR%vulnflow.py" %*
    exit /b %errorlevel%
)

echo Project virtual environment was not found. Run "vulnflow prepare" first.
exit /b 1
