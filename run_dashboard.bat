@echo off
title BTC 5M Polymarket Dashboard
echo Starting BTC 5M Polymarket Dashboard...
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\launch_ui.py
) else (
    python scripts\launch_ui.py
)
pause
