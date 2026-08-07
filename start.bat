@echo off
REM Starts AirTouch, creating the venv and installing the dependencies the first time.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating the virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :failed
    echo Installing the dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :failed
)

".venv\Scripts\python.exe" main.py %*
goto :done

:failed
echo.
echo Installation failed. Make sure Python 3.10+ is on your PATH.
pause

:done
endlocal
