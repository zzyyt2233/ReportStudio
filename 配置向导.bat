@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 配置向导：生成 config.local.yaml（大模型接入），只在本机生效
if exist "runtime\py\python.exe" (
  "runtime\py\python.exe" tools\setup_wizard.py
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" tools\setup_wizard.py
) else (
  py -3 tools\setup_wizard.py 2>nul || python tools\setup_wizard.py
)
echo.
pause
