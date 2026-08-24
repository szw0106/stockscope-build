@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title StockScope 首次启动

where py >nul 2>nul
if errorlevel 1 (
  echo [StockScope] 未检测到 Python。
  echo 请先从 https://www.python.org/downloads/windows/ 安装 Python 3.11 或 3.12，
  echo 安装时勾选 Add Python to PATH，然后重新双击本文件。
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [StockScope] 正在建立独立运行环境，只在首次启动时执行……
  py -3 -m venv .venv
  if errorlevel 1 goto :failed
)

echo [StockScope] 正在检查图表组件……
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :failed

start "StockScope" ".venv\Scripts\pythonw.exe" app.py
exit /b 0

:failed
echo.
echo 安装未完成，请检查网络后重试。如果电脑使用代理，请先保证 pip 可以联网。
pause
exit /b 1
