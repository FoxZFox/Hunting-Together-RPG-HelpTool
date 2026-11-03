@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Opens the control panel at http://127.0.0.1:8765 . Close this window to shut it down.
python ui.py
pause
