@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 还没有安装，请先双击运行 "1-安装.bat"。
  echo Not installed yet. Double-click "1-安装.bat" (install) first.
  pause
  exit /b 1
)
echo 正在启动面板，浏览器会自动打开 http://127.0.0.1:8765
echo Starting the panel; your browser will open http://127.0.0.1:8765
echo 使用期间不要关闭这个窗口；关闭窗口即可停止面板。
echo Keep this window open while using the panel; close it to stop.
echo.
".venv\Scripts\python.exe" -m app.main
pause
