@echo off
REM One-click launcher. Creates the venv if missing, installs deps, runs the bot.
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 goto :nopython
)

echo Checking dependencies...
venv\Scripts\python.exe -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo Could not install dependencies. Check your internet connection.
    pause
    exit /b 1
)

if not exist ".env" (
    echo.
    echo No .env file found. Copy .env.example to .env and fill it in.
    pause
    exit /b 1
)

:run
echo.
echo Starting Interview Triage. Leave this window open. Ctrl+C to stop.
echo.
venv\Scripts\python.exe app.py
if errorlevel 1 (
    echo.
    echo Exited unexpectedly. Restarting in 15 seconds... Ctrl+C to stop.
    timeout /t 15 /nobreak >nul
    goto run
)
echo.
echo Bot stopped.
pause
exit /b 0

:nopython
echo.
echo Python not found. Install it from python.org and tick "Add to PATH".
pause
exit /b 1
