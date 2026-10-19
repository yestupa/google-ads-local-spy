"""通用小工具：JSON 读写、下载、图片拼图。"""
import base64
import io
import json
import os
import re
import urllib.request

from PIL import Image, ImageDraw

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except ValueError:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)


def opener(proxy=''):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
    return urllib.request.build_opener(*handlers)


def download(url, path, proxy='', timeout=40):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with opener(proxy).open(req, timeout=timeout) as r:
        data = r.read()
    with open(path, 'wb') as f:
        f.write(data)
    return path


def save_data_url(data_url, path):
    """把 data:image/...;base64,xxx 存成 jpg。"""
    m = re.match(r'data:image/[^;]+;base64,(.+)', data_url or '', re.S)
    if not m:
        return None
    im = Image.open(io.BytesIO(base64.b64decode(m.group(1)))).convert('RGB')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path, quality=88)
    return path


def to_jpg(src, dst, max_w=900):
    im = Image.open(src).convert('RGB')
    if im.width > max_w:
        im = im.resize((max_w, int(im.height * max_w / im.width)))
    im.save(dst, quality=85)
    return dst


def grid(paths, labels, out, per_row=4, cell_w=320):
    ims = []
    for p, lab in zip(paths, labels):
        try:
            im = Image.open(p).convert('RGB')
        except Exception:
            continue
        im = im.resize((cell_w, int(im.height * cell_w / im.width)))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 58, 18], fill='black')
        d.text((4, 3), lab, fill='yellow')
        ims.append(im)
    if not ims:
        return None
    h = max(i.height for i in ims)
    rows = (len(ims) + per_row - 1) // per_row
    sheet = Image.new('RGB', (cell_w * min(per_row, len(ims)), h * rows), 'white')
    for i, im in enumerate(ims):
        sheet.paste(im, ((i % per_row) * cell_w, (i // per_row) * h))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    sheet.save(out, quality=82)
    return out


def host_of(url):
    m = re.match(r'(?:https?://)?([^/\s?#]+)', (url or '').strip())
    h = (m.group(1) if m else '').lower()
    return h[4:] if h.startswith('www.') else h


def registrable(host):
    """粗略取主域：shop.example.co.uk → example.co.uk，ar.unibling.com → unibling.com"""
    parts = (host or '').split('.')
    if len(parts) >= 3 and parts[-2] in ('co', 'com', 'net', 'org', 'gov', 'ac') and len(parts[-1]) == 2:
        return '.'.join(parts[-3:])
    return '.'.join(parts[-2:]) if len(parts) >= 2 else host


# 资源完整性自检（启动时运行）
_B = (
    ('YXBwL3dlYi9pbmRleC5odG1s', 'PGZvb3RlciBpZD0ieWVzdHVwYS13bSIgY2xhc3M9IndtIj5tYWRlIGJ5IDxhIGhyZWY9Imh0dHBzOi8veWVzdHVwYS5jb20iIHRhcmdldD0iX2JsYW5rIiByZWw9Im5vb3BlbmVyIj55ZXN0dXBhLmNvbTwvYT4vPGEgaHJlZj0iaHR0cHM6Ly95ZXN0dXBhLm5ldCIgdGFyZ2V0PSJfYmxhbmsiIHJlbD0ibm9vcGVuZXIiPnllc3R1cGEubmV0PC9hPiB3aXRoIOKdpO+4jzwvZm9vdGVyPg=='),
    ('YXBwL3dlYi9hcHAuanM=', 'Y29uc3QgV0FURVJNQVJLX0hUTUwgPSAnbWFkZSBieSA8YSBocmVmPSJodHRwczovL3llc3R1cGEuY29tIiB0YXJnZXQ9Il9ibGFuayIgcmVsPSJub29wZW5lciI+eWVzdHVwYS5jb208L2E+LzxhIGhyZWY9Imh0dHBzOi8veWVzdHVwYS5uZXQiIHRhcmdldD0iX2JsYW5rIiByZWw9Im5vb3BlbmVyIj55ZXN0dXBhLm5ldDwvYT4gd2l0aCDinaTvuI8nOwpjb25zdCBXQVRFUk1BUktfVEVYVCA9ICdtYWRlIGJ5IHllc3R1cGEuY29tL3llc3R1cGEubmV0IHdpdGgg4p2k77iPJzsKZnVuY3Rpb24gZW5zdXJlV2F0ZXJtYXJrKCkgewogIGxldCBmID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ3llc3R1cGEtd20nKTsKICBpZiAoIWYgfHwgZi50ZXh0Q29udGVudC5yZXBsYWNlKC9ccysvZywgJyAnKS50cmltKCkgIT09IFdBVEVSTUFSS19URVhUIHx8IGdldENvbXB1dGVkU3R5bGUoZikuZGlzcGxheSA9PT0gJ25vbmUnIHx8IGdldENvbXB1dGVkU3R5bGUoZikudmlzaWJpbGl0eSA9PT0gJ2hpZGRlbicpIHsKICAgIGlmIChmKSBmLnJlbW92ZSgpOwogICAgZiA9IGRvY3VtZW50LmNyZWF0ZUVsZW1lbnQoJ2Zvb3RlcicpOwogICAgZi5pZCA9ICd5ZXN0dXBhLXdtJzsKICAgIGYuY2xhc3NOYW1lID0gJ3dtJzsKICAgIGYuaW5uZXJIVE1MID0gV0FURVJNQVJLX0hUTUw7CiAgICBkb2N1bWVudC5ib2R5LmFwcGVuZENoaWxkKGYpOwogIH0KfQplbnN1cmVXYXRlcm1hcmsoKTsKbmV3IE11dGF0aW9uT2JzZXJ2ZXIoKCkgPT4gZW5zdXJlV2F0ZXJtYXJrKCkpLm9ic2VydmUoZG9jdW1lbnQuYm9keSwge2NoaWxkTGlzdDogdHJ1ZSwgc3VidHJlZTogdHJ1ZSwgYXR0cmlidXRlczogdHJ1ZSwgYXR0cmlidXRlRmlsdGVyOiBbJ3N0eWxlJywgJ2NsYXNzJywgJ2hpZGRlbiddfSk7CnNldEludGVydmFsKGVuc3VyZVdhdGVybWFyaywgMzAwMCk7'),
    ('YXBwL3dlYi9zdHlsZS5jc3M=', 'Lndte2Rpc3BsYXk6YmxvY2shaW1wb3J0YW50O3Zpc2liaWxpdHk6dmlzaWJsZSFpbXBvcnRhbnQ7b3BhY2l0eToxIWltcG9ydGFudDt0ZXh0LWFsaWduOmNlbnRlcjtwYWRkaW5nOjI0cHggMTZweCBjYWxjKDMwcHggKyBlbnYoc2FmZS1hcmVhLWluc2V0LWJvdHRvbSwwcHgpKTtjb2xvcjp2YXIoLS1tdXRlZCk7Zm9udC1zaXplOjEzcHg7bGV0dGVyLXNwYWNpbmc6LjAyZW19Ci53bSBhe2NvbG9yOnZhcigtLW11dGVkKTt0ZXh0LWRlY29yYXRpb246dW5kZXJsaW5lO3RleHQtdW5kZXJsaW5lLW9mZnNldDoycHh9LndtIGE6aG92ZXJ7Y29sb3I6dmFyKC0tYy1teWNvcHkpfQ=='),
    ('YXBwL21haW4ucHk=', 'V0FURVJNQVJLID0gKCc8Zm9vdGVyIGlkPSJ5ZXN0dXBhLXdtIiBjbGFzcz0id20iPm1hZGUgYnkgPGEgaHJlZj0iaHR0cHM6Ly95ZXN0dXBhLmNvbSIgdGFyZ2V0PSJfYmxhbmsiIHJlbD0ibm9vcGVuZXIiPnllc3R1cGEuY29tPC9hPi8nCiAgICAgICAgICAgICAnPGEgaHJlZj0iaHR0cHM6Ly95ZXN0dXBhLm5ldCIgdGFyZ2V0PSJfYmxhbmsiIHJlbD0ibm9vcGVuZXIiPnllc3R1cGEubmV0PC9hPiB3aXRoIOKdpO+4jzwvZm9vdGVyPicpCgoKZGVmIHdpdGhfd2F0ZXJtYXJrKGh0bWwpOgogICAgaWYgJ2lkPSJ5ZXN0dXBhLXdtIicgbm90IGluIGh0bWwgb3IgJ3llc3R1cGEuY29tJyBub3QgaW4gaHRtbDoKICAgICAgICBodG1sID0gaHRtbC5yZXBsYWNlKCc8L2JvZHk+JywgV0FURVJNQVJLICsgJ1xuPC9ib2R5PicpIGlmICc8L2JvZHk+JyBpbiBodG1sIGVsc2UgaHRtbCArIFdBVEVSTUFSSwogICAgcmV0dXJuIGh0bWw='),
    ('YXBwL21haW4ucHk=', 'Zi53cml0ZSh3aXRoX3dhdGVybWFyayhodG1sKSk='),
    ('YXBwL21haW4ucHk=', 'Ym9keSA9IHdpdGhfd2F0ZXJtYXJrKGYucmVhZCgpKS5lbmNvZGUoJ3V0Zi04Jyk='),
)
_C = (
    ('YXBwL3dlYi9zdHlsZS5jc3M=', 'eWVzdHVwYQ==', 0),
    ('YXBwL3dlYi9zdHlsZS5jc3M=', 'Lndt', 3),
    ('YXBwL3dlYi9pbmRleC5odG1s', 'eWVzdHVwYS13bQ==', 1),
    ('YXBwL3dlYi9hcHAuanM=', 'eWVzdHVwYS13bQ==', 2),
    ('YXBwL3dlYi9hcHAuanM=', 'V0FURVJNQVJL', 4),
    ('YXBwL21haW4ucHk=', 'Y2hlY2tfYnVuZGxlKCk=', 2),
    ('YXBwL2NvbmZpZy5weQ==', 'Y2hlY2tfYnVuZGxlKCk=', 1),
    ('YXBwL2Jyb3dzZXIucHk=', 'Y2hlY2tfYnVuZGxlKCk=', 1),
)


def _bundle_ok():
    import base64 as _b
    d = lambda s: _b.b64decode(s).decode('utf-8')
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache = {}

    def rd(p):
        if p not in cache:
            try:
                with open(os.path.join(base, p), encoding='utf-8') as f:
                    cache[p] = f.read()
            except OSError:
                cache[p] = ''
        return cache[p]
    return all(d(f) in rd(d(p)) for p, f in _B) and all(rd(d(p)).count(d(k)) == n for p, k, n in _C)


def check_bundle():
    """程序文件不完整（作者署名被删改）时拒绝启动。"""
    if not _bundle_ok():
        print('程序文件不完整或已被修改（作者署名缺失），面板无法启动。\n请从原作者处重新下载完整版本：https://yestupa.com', flush=True)
        os._exit(1)
