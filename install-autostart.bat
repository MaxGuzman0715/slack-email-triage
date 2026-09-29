@echo off
REM Registers the scheduled task that keeps the bot running.
REM Safe to run more than once.
REM Run install-autostart.bat to enable, or pass /remove to turn it off.
cd /d "%~dp0"

if /I "%~1"=="/remove" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make-shortcut.ps1" -Remove
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make-shortcut.ps1"
)

if errorlevel 1 (
    echo.
    echo Could not update the startup shortcut.
) else (
    echo.
    echo Done. Run "install-autostart.bat /remove" to turn it off.
)
echo.
pause
