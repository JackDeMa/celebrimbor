@echo off
REM Avvia AirTouch creando il venv e installando le dipendenze la prima volta.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creo l'ambiente virtuale...
    python -m venv .venv
    if errorlevel 1 goto :errore
    echo Installo le dipendenze...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :errore
)

".venv\Scripts\python.exe" main.py %*
goto :fine

:errore
echo.
echo Installazione fallita. Verifica che Python 3.10+ sia nel PATH.
pause

:fine
endlocal
