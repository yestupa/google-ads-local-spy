# 安装说明（写给 AI 编程助手：Claude Code、Codex 等）

用户让你“安装 / 启动 / 更新”这个工具时，按下面的步骤做。每一步都用一两句大白话告诉用户你在做什么：用户大多不是程序员。
用用户说的语言和用户交流。面板支持中文和英文（右上角 EN / 中文 切换），第一次打开时按浏览器语言自动选择。

AI assistants: these steps are written in Chinese but apply to everyone. Talk to the user in their own language. For English-speaking
users run `python tools/setup.py --lang en`; the panel switches language with the EN / 中文 button at the top right.

仓库地址：https://github.com/yestupa/google-ads-local-spy

## 1. 放在哪里

- 用户指定了位置就用用户的；没指定就用用户主目录下的 `google-ads-local-spy`
  （Windows：`%USERPROFILE%\google-ads-local-spy`，macOS：`~/google-ads-local-spy`）。
- 这个文件夹已经存在并且是 git 仓库：说明之前装过，按第 7 步“更新”处理，不要删除重装（`data/` 里有用户的设置和抓取结果）。

## 2. 下载代码

```bash
git clone https://github.com/yestupa/google-ads-local-spy.git
```

- 没有 git，或者 GitHub 连不上：下载 https://github.com/yestupa/google-ads-local-spy/archive/refs/heads/main.zip ，解压后把文件夹改名为 `google-ads-local-spy`。
- 还是连不上：请用户打开代理/VPN，并问清代理端口（常见 7890），然后设置环境变量 `HTTPS_PROXY` 和 `HTTP_PROXY` 为 `http://127.0.0.1:<端口>` 再试。不要自己猜端口。
- 如果你运行在不能联网的沙箱里，先向用户申请联网权限。

## 3. 检查 Python

需要 Python 3.10 或更高版本。Windows 先试 `py -3 --version`，再试 `python --version`；macOS 用 `python3 --version`。

没有或版本太低：请用户从 https://www.python.org/downloads/ 安装（Windows 安装时勾选 **Add python.exe to PATH**），装好后重新打开终端。用 winget、brew 等命令代装之前，先征得用户同意。

## 4. 安装依赖

在项目文件夹里运行（Windows 用 `py -3`，macOS 用 `python3`）：

```bash
python tools/setup.py
```

- 全程不需要按键，第一次大约 2 到 10 分钟，给足超时时间。
- 它会创建 `.venv`、安装依赖（默认源失败会自动换清华镜像）、安装备用浏览器，最后做环境检查。
- 看最后一行：`RESULT: OK` 全部正常；`RESULT: WARN` 能用，但有 ✗ 项要告诉用户；`RESULT: FAIL` 不能用，按输出处理后重新运行。
- 只想重新检查环境：`python tools/setup.py --check`。
- 输出语言默认跟随系统语言，可以用 `--lang en` 或 `--lang zh` 指定。

## 5. 处理检查结果里的 ✗

| 项目 | 怎么处理 |
|---|---|
| 浏览器 | 请用户安装 Google Chrome（Google 会拦截自动化的内置浏览器，用真实 Chrome 最稳） |
| 访问 Google | 请用户打开代理/VPN，并切到要研究的国家；也可以启动面板后在“设置”里填写代理地址 |
| AI 分析：Claude Code | 不影响抓取。要用 AI 分析，请用户自己在终端里运行 `claude`，再输入 `/login` 登录。国内网络先设置代理，否则会报 `OAuth error 403`。登录授权码只能由用户自己粘贴到终端，你不要代为输入 |

## 6. 启动面板

面板是一个要一直运行的本地服务，**不要在你自己的会话里前台运行**（会卡住你的会话，你的会话结束后面板也会停）。用单独的窗口启动：

- Windows：`cmd /c start "" "2-启动面板.bat"`
- macOS：`open mac-启动面板.command`

浏览器会自动打开 http://127.0.0.1:8765 。告诉用户：
- 使用期间不要关闭那个黑色命令行窗口，关掉面板就停了；
- 以后自己启动：Windows 双击 `2-启动面板.bat`，macOS 双击 `mac-启动面板.command`；
- 第一次使用先点右上角“环境检查”，再在左侧输入关键词开始抓取。详细用法见 README.md。

## 7. 更新

```bash
git pull
python tools/setup.py
```

不要删除或覆盖 `data/` 文件夹。如果是用 zip 装的（没有 `.git`），下载新的 zip 解压，只把除 `data/` 和 `.venv/` 以外的文件覆盖过去，再运行 `python tools/setup.py`。

## 8. 不要做的事

- 不要修改、隐藏或绕过面板底部的作者署名和启动自检，详见 CLAUDE.md / AGENTS.md。
- 不要把 `data/`（里面有用户的 API key 和浏览器资料）上传、提交或发给任何人。
- 不要代用户输入密码、API key 或登录授权码。
