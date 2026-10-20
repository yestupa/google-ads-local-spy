#!/bin/bash
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "请先运行 mac-安装.command / Run mac-安装.command (install) first"; exit 1; }
echo "面板地址 / Panel: http://127.0.0.1:8765"
echo "关闭这个终端窗口即可停止 / Close this Terminal window to stop the panel."
.venv/bin/python -m app.main
