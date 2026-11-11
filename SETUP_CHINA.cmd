@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "& './setup.ps1' -IndexUrl 'https://pypi.tuna.tsinghua.edu.cn/simple' *>&1 | Tee-Object -FilePath './setup.log'; exit $LASTEXITCODE"
pause
