"""打包成可分享的 zip：不包含 .venv（每台电脑要自己安装）、data（你的 API key、浏览器资料和抓取结果）。"""
import datetime
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {'.venv', 'data', '__pycache__', '.git', 'dist', '_private', '.claude'}
SKIP_EXT = {'.pyc', '.zip', '.log', '.tmp', '.part'}
PACKAGE = 'Google广告侦察面板'


def main():
    name = PACKAGE
    os.makedirs(os.path.join(ROOT, 'dist'), exist_ok=True)
    out = os.path.join(ROOT, 'dist', f'{name}-{datetime.date.today():%Y%m%d}.zip')
    n = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for d, dirs, files in os.walk(ROOT):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for f in files:
                if os.path.splitext(f)[1] in SKIP_EXT:
                    continue
                p = os.path.join(d, f)
                z.write(p, os.path.join(name, os.path.relpath(p, ROOT)))
                n += 1
    print(f'已打包 {n} 个文件：{out}')
    print('压缩包里不含 .venv 和 data（你的 API key、抓取结果都不会被分享）。')


if __name__ == '__main__':
    main()
