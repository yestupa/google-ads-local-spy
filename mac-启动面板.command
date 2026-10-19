#!/bin/bash
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "请先运行 mac-安装.command"; exit 1; }
echo "面板地址 http://127.0.0.1:8765 ，关闭这个终端窗口即可停止。"
.venv/bin/python -m app.main
