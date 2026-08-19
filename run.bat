@echo off
setlocal

set "DIR=%~dp0"
set "VENV_PYTHON=%DIR%venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo venv not found at %DIR%venv -- creating one... 1>&2
    python -m venv "%DIR%venv"
    if errorlevel 1 (
        echo Failed to create venv 1>&2
        exit /b 1
    )
    echo Installing dependencies from requirements.txt... 1>&2
    "%VENV_PYTHON%" -m pip install -r "%DIR%requirements.txt"
    if errorlevel 1 (
        echo Failed to install dependencies 1>&2
        exit /b 1
    )
    echo venv ready. 1>&2
)

"%VENV_PYTHON%" "%DIR%main.py" %*
