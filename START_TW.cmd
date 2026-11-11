@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo Run SETUP.cmd first. Please read README_TW.md.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" main.py --lang zh-TW
if errorlevel 1 pause
