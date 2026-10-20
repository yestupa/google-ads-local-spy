@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Google 广告侦察面板 - 安装（只需运行一次）
echo   Google Ad Spy Panel - install (run once)
echo ============================================
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [错误] 没有找到 Python。请先安装 Python 3.10 或更高版本：https://www.python.org/downloads/
  echo [error] Python not found. Install Python 3.10 or newer: https://www.python.org/downloads/
  echo 安装时务必勾选 "Add python.exe to PATH"，装好后重新运行本文件。
  echo Tick "Add python.exe to PATH" during installation, then run this file again.
  pause
  exit /b 1
)

%PY% tools\setup.py
echo.
pause
