#!/bin/bash
# macOS 安装：在“终端”里运行，或右键 → 打开
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "没有找到 python3，请先安装 Python 3.10+：https://www.python.org/downloads/"
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "Python 版本需要 3.10 或更高"; exit 1; }
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt || .venv/bin/python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt || exit 1
.venv/bin/python -m playwright install chromium || echo "备用浏览器没装上；有 Chrome/Edge 就能用。"
echo "安装完成，运行 mac-启动面板.command 开始使用。"
