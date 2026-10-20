"""一条命令完成安装和环境检查，全程不需要按键，方便 Claude Code / Codex 等 AI 助手代为安装。
One command to install and check the setup, with no prompts, so AI assistants (Claude Code, Codex...) can run it.

用法 / Usage（在项目文件夹里运行，用电脑上的 Python 3.10+ / run in the project folder with Python 3.10+）：
    python tools/setup.py                 安装并检查 / install, then check
    python tools/setup.py --check         只检查 / check only
    python tools/setup.py --no-chromium   跳过备用浏览器 / skip the bundled fallback browser
    python tools/setup.py --lang en       输出语言 zh / en（默认按系统语言）/ output language (default: system language)

最后一行输出 RESULT: OK / WARN / FAIL，退出码分别是 0 / 0 / 1。
The last line is RESULT: OK / WARN / FAIL; exit code 0 / 0 / 1.
"""
import locale
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIN = os.name == 'nt'
VPY = os.path.join(ROOT, '.venv', 'Scripts' if WIN else 'bin', 'python.exe' if WIN else 'python')
MIRROR = 'https://pypi.tuna.tsinghua.edu.cn/simple'


def detect_lang(args):
    if '--lang' in args:
        i = args.index('--lang')
        if i + 1 < len(args):
            return 'en' if args[i + 1].lower().startswith('en') else 'zh'
    try:
        loc = (locale.getlocale()[0] or locale.getdefaultlocale()[0] or '').lower()
    except Exception:
        loc = ''
    return 'zh' if loc.startswith(('zh', 'chinese')) else 'en'


LANG = detect_lang(sys.argv[1:])


def L(zh, en):
    return en if LANG == 'en' else zh


def say(msg):
    print(msg, flush=True)


def run(cmd):
    say('$ ' + ' '.join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


def install(chromium=True):
    if sys.version_info < (3, 10):
        say(L(f'[错误] Python 版本太低：{sys.version.split()[0]}，需要 3.10 或更高：https://www.python.org/downloads/',
              f'[error] Python {sys.version.split()[0]} is too old; 3.10 or newer is required: https://www.python.org/downloads/'))
        return False
    if not os.path.exists(VPY):
        say(L('[1/3] 创建独立的 Python 环境 .venv', '[1/3] Creating a private Python environment (.venv)'))
        if run([sys.executable, '-m', 'venv', '.venv']) != 0:
            say(L('[错误] 创建 .venv 失败', '[error] Could not create .venv'))
            return False
    say(L('[2/3] 安装依赖', '[2/3] Installing dependencies'))
    pip = [VPY, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', 'requirements.txt']
    if run(pip) != 0:
        say(L('默认源失败，改用清华镜像重试', 'Default package index failed, retrying with a mirror'))
        if run(pip[:4] + ['-i', MIRROR] + pip[4:]) != 0:
            say(L('[错误] 依赖安装失败：检查网络或代理（可以设置 HTTPS_PROXY 环境变量）后重试',
                  '[error] Installing dependencies failed: check your network or proxy (you can set HTTPS_PROXY) and retry'))
            return False
    if chromium:
        say(L('[3/3] 安装备用浏览器（有 Chrome 或 Edge 的话，失败也没关系）', '[3/3] Installing the fallback browser (fine if this fails when Chrome or Edge is installed)'))
        if run([VPY, '-m', 'playwright', 'install', 'chromium']) != 0:
            say(L('[提示] 备用浏览器没装上；电脑里有 Google Chrome 或 Microsoft Edge 就能正常使用',
                  '[note] Fallback browser not installed; the tool works fine with Google Chrome or Microsoft Edge'))
    return True


def check():
    """用 .venv 里的 Python 调用面板自己的环境检查（和面板右上角“环境检查”一样）。"""
    if not os.path.exists(VPY):
        say(L('[错误] 还没有安装：先运行 python tools/setup.py', '[error] Not installed yet: run python tools/setup.py first'))
        return 'FAIL'
    code = ('from app.i18n import set_lang\n'
            f'set_lang({LANG!r})\n'
            'from app.main import env_check\n'
            'r = env_check()\n'
            'for x in r: print(("  ✓ " if x["ok"] else "  ✗ ") + x["name"] + ' + repr(L('：', ': ')) + ' + x["detail"])\n'
            'hard = [x for x in r if not x["ok"] and x.get("key") in ("python", "playwright", "browser")]\n'
            'soft = [x for x in r if not x["ok"] and x not in hard]\n'
            'print("RESULT: " + ("FAIL" if hard else "WARN" if soft else "OK"))\n')
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    say(L('环境检查：', 'Setup check:'))
    out = subprocess.run([VPY, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
    text = (out.stdout or '') + (out.stderr or '')
    lines = text.rstrip().splitlines()
    for line in lines[:-1] if lines and lines[-1].startswith('RESULT:') else lines:
        say(line)
    last = lines[-1] if lines else ''
    return last.split(':', 1)[1].strip() if last.startswith('RESULT:') else 'FAIL'


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    args = sys.argv[1:]
    if '--check' not in args and not install(chromium='--no-chromium' not in args):
        say('RESULT: FAIL')
        sys.exit(1)
    result = check()
    if result == 'OK':
        say(L('安装完成，可以启动面板了。', 'Setup complete. You can start the panel.'))
    elif result == 'WARN':
        say(L('可以使用。上面打 ✗ 的项目不影响启动：访问 Google 失败要开代理/VPN；Claude Code 未登录只是没有 AI 分析。',
              'Ready to use. Items marked ✗ do not stop the panel: if Google access fails, turn on a proxy/VPN; without Claude Code login there is no AI analysis.'))
    say(L('启动面板：Windows 双击 “2-启动面板.bat”；macOS 运行 mac-启动面板.command。面板地址 http://127.0.0.1:8765',
          'Start the panel: on Windows double-click “2-启动面板.bat” (start panel); on macOS run mac-启动面板.command. Panel address: http://127.0.0.1:8765'))
    say('RESULT: ' + result)
    sys.exit(1 if result == 'FAIL' else 0)


if __name__ == '__main__':
    main()
