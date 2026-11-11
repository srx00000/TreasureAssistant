@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo Run SETUP.cmd or SETUP_CHINA.cmd first. Please read README_CN.md.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" main.py --lang zh-CN
if errorlevel 1 pause
