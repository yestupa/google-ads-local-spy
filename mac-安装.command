#!/bin/bash
# macOS 安装 / install：在“终端”里运行，或右键 → 打开 / run in Terminal, or right-click → Open
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "没有找到 python3，请先安装 Python 3.10+：https://www.python.org/downloads/"
  echo "python3 not found. Install Python 3.10+ from https://www.python.org/downloads/"
  exit 1
fi
python3 tools/setup.py
