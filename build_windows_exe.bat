@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title StockScope EXE 打包

where py >nul 2>nul
if errorlevel 1 (
  echo 请先安装 Python 3.11 或 3.12，并勾选 Add Python to PATH。
  pause
  exit /b 1
)

if not exist ".build-venv\Scripts\python.exe" py -3 -m venv .build-venv
if errorlevel 1 goto :failed

".build-venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-build.txt
if errorlevel 1 goto :failed

".build-venv\Scripts\pyinstaller.exe" --noconfirm --clean --windowed --onefile --name StockScope --collect-all matplotlib app.py
if errorlevel 1 goto :failed

copy /Y README.md dist\README.md >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\StockScope.exe','dist\README.md' -DestinationPath 'dist\StockScope-Windows.zip' -Force"
echo.
echo 打包完成：dist\StockScope-Windows.zip
pause
exit /b 0

:failed
echo.
echo 打包失败，请查看上方错误信息。
pause
exit /b 1
