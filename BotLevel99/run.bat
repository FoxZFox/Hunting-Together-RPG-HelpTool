@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Opens the control panel at http://127.0.0.1:8765 . Close this window to shut it down.
rem The bot runs in its own Python environment (.venv in this folder), so the libraries it
rem needs never touch the PC's own Python. It is made on the first run, and the libraries are
rem installed again whenever requirements.txt changes.

set "VPY=.venv\Scripts\python.exe"
if exist "%VPY%" goto :deps

set "PY=python"
where python >nul 2>&1 || set "PY=py -3"
%PY% -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1 || goto :nopython
echo กำลังสร้าง Python venv ให้บอท ครั้งแรกเท่านั้น...
%PY% -m venv .venv || goto :failed

:deps
fc /b requirements.txt ".venv\requirements.txt" >nul 2>&1 && goto :run
echo กำลังติดตั้ง library ที่บอทใช้ ลงใน .venv ...
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :failed
copy /y requirements.txt ".venv\requirements.txt" >nul

:run
"%VPY%" ui.py %*
pause
exit /b

:nopython
echo ไม่เจอ Python 3.11 ขึ้นไปในเครื่องนี้
echo ติดตั้งได้จาก https://www.python.org/downloads/ ตอนติดตั้งติ๊ก Add python.exe to PATH แล้วเปิด run.bat ใหม่
pause
exit /b 1

:failed
echo สร้าง venv หรือติดตั้ง library ไม่สำเร็จ ดูข้อความด้านบน ถ้าแก้แล้วให้ลบโฟลเดอร์ .venv แล้วเปิด run.bat ใหม่
pause
exit /b 1
