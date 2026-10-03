@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY goto nopython

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   正在创建虚拟环境...
  %PY% -m venv .venv
  if errorlevel 1 goto venvfail
)

echo.
echo   正在安装依赖，第一次需要几分钟（要下载约 100MB）...
echo.

".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto pipfail

echo.
echo   安装完成。现在可以双击 启动.bat 了。
echo.
pause
exit /b 0


:nopython
echo.
echo   没有找到可用的 Python（需要 3.10 或更高版本）。
echo   请先安装：https://www.python.org/downloads/
echo   安装时务必勾选 "Add Python to PATH"。
echo.
pause
exit /b 1

:venvfail
echo.
echo   创建虚拟环境失败。请确认 Python 版本不低于 3.10。
echo.
pause
exit /b 1

:pipfail
echo.
echo   依赖安装失败。可以换国内镜像源重试：
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
pause
exit /b 1
