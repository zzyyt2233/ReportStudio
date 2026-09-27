@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 免安装版：包里自带 Python 运行时，优先用它，不用装 Python、不用联网
if exist "runtime\py\python.exe" goto runportable

if not exist ".venv\Scripts\python.exe" goto bootstrap
goto run

:runportable
echo.
echo   ReportStudio 正在启动（使用包内自带的 Python 运行时）...
echo   产物目录：%CD%\outputs
echo   启动后浏览器会自动打开 http://127.0.0.1:8765
echo   关掉这个黑窗口就会停止服务
echo.
"runtime\py\python.exe" app.py
if errorlevel 1 goto failed
exit /b 0

:run
echo.
echo   ReportStudio 正在启动...
echo   产物目录：%CD%\outputs
echo   启动后浏览器会自动打开 http://127.0.0.1:8765
echo   关掉这个黑窗口就会停止服务
echo.
".venv\Scripts\python.exe" app.py
if errorlevel 1 goto failed
exit /b 0


:bootstrap
echo.
echo   ============================================================
echo   首次运行：正在准备运行环境
echo   会在本目录建一个虚拟环境并下载依赖，约 100MB，需要几分钟。
echo   请保持联网，不要关闭这个窗口。
echo   ============================================================
echo.

set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY goto nopython

echo   使用：%PY%
%PY% -m venv .venv
if errorlevel 1 goto venvfail

".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto pipfail

echo.
echo   环境准备完成。
goto run


:nopython
echo   没有找到可用的 Python（需要 3.10 或更高版本）。
echo.
echo   请先安装：https://www.python.org/downloads/
echo   安装时务必勾选 "Add Python to PATH"，装完重新双击本文件。
echo.
pause
exit /b 1

:venvfail
echo   创建虚拟环境失败。请确认 Python 版本不低于 3.10。
echo.
pause
exit /b 1

:pipfail
echo   依赖安装失败。常见原因是网络不通，或下载太慢。
echo.
echo   可以手动重试（换国内镜像源）：
echo     python -m venv .venv
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
pause
exit /b 1

:failed
echo   启动失败。常见原因：
echo     1. 依赖没装全 —— 删掉 .venv 目录后重新双击本文件
echo     2. 8765 端口被占用 —— 改 config.yaml 里的 port
echo.
pause
exit /b 1
