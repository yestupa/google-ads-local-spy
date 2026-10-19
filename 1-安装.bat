@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Google 广告侦察面板 - 安装（只需运行一次）
echo ============================================
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [错误] 没有找到 Python。
  echo 请先安装 Python 3.10 或更高版本：https://www.python.org/downloads/
  echo 安装时务必勾选 "Add python.exe to PATH"，装好后重新运行本文件。
  pause
  exit /b 1
)

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo [错误] Python 版本太低，需要 3.10 或更高。当前版本：
  %PY% --version
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] 创建独立的 Python 环境 .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [错误] 创建环境失败。
    pause
    exit /b 1
  )
)

echo [2/3] 安装依赖（第一次可能要几分钟）...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo 默认源安装失败，改用清华镜像重试 ...
  ".venv\Scripts\python.exe" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
  if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
  )
)

echo [3/3] 安装备用浏览器（电脑里有 Chrome 或 Edge 的话，这一步失败也没关系）...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 (
  echo [提示] 备用浏览器没装上。只要电脑里有 Google Chrome 或 Microsoft Edge 就能正常使用。
)

echo.
echo 安装完成！双击 "2-启动面板.bat" 开始使用。
pause
