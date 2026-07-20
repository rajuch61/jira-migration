@echo off
cd /d "%~dp0"
python main.py --retry-failed
if errorlevel 1 (
    echo.
    echo Retry failed.
    pause
    exit /b 1
)
