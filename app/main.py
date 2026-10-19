"""本地面板服务：python -m app.main 启动后，浏览器打开 http://127.0.0.1:8765 。只监听本机，不对外开放。"""
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config
from .jobs import Manager

PORT = int(os.environ.get('ADSPY_PORT', '8765'))
manager = None


def env_check():
    s = config.load_settings()
    out = []
    out.append({'name': 'Python', 'ok': sys.version_info >= (3, 10), 'detail': sys.version.split()[0] + ('' if sys.version_info >= (3, 10) else '（需要 3.10 或更高）')})
    try:
        import playwright  # noqa
        out.append({'name': 'Playwright', 'ok': True, 'detail': '已安装'})
    except ImportError:
        out.append({'name': 'Playwright', 'ok': False, 'detail': '未安装，请先运行“1-安装”'})
    found = []
    candidates = {
        'Chrome': [r'C:\Program Files\Google\Chrome\Application\chrome.exe', r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
                   os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'), '/Applications/Google Chrome.app'],
        'Edge': [r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
                 '/Applications/Microsoft Edge.app'],
    }
    for name, paths in candidates.items():
        if any(os.path.exists(p) for p in paths) or shutil.which(name.lower()):
            found.append(name)
    out.append({'name': '浏览器', 'ok': bool(found), 'detail': '、'.join(found) if found else '没找到 Chrome 或 Edge（Google 会拦截内置浏览器，强烈建议安装 Chrome）'})
    from .analyze import system_proxy
    proxy = s.get('proxy') or system_proxy()
    try:
        h = [urllib.request.ProxyHandler({'http': proxy, 'https': proxy})] if proxy else []
        urllib.request.build_opener(*h).open(urllib.request.Request('https://www.google.com/generate_204', headers={'User-Agent': 'Mozilla/5.0'}), timeout=8)
        out.append({'name': '访问 Google', 'ok': True, 'detail': '正常' + (f'（代理 {proxy}）' if proxy else '')})
    except Exception as e:
        out.append({'name': '访问 Google', 'ok': False, 'detail': f'失败：{str(e)[:80]}。请开启代理/VPN，或在设置里填写代理地址'})
    engine = s.get('engine') or 'claude_code'
    if engine == 'claude_code':
        ok, detail = claude_status_cached()
        out.append({'name': 'AI 分析：Claude Code', 'ok': ok, 'detail': detail + ('' if ok else '（不影响抓取，只是没有 AI 分析）')})
    elif engine == 'api':
        out.append({'name': 'AI 分析：Claude API', 'ok': bool(s.get('api_key')), 'detail': '已填写 API key' if s.get('api_key') else '未填写 API key（不影响抓取，只是没有 AI 分析）'})
    else:
        out.append({'name': 'AI 分析', 'ok': True, 'detail': '已关闭（只抓取）'})
    return out


_cc_cache = {'t': 0, 'v': None}


def claude_status_cached(force=False):
    """检查 Claude Code 登录状态会发一个极小的请求，所以结果缓存 5 分钟。"""
    import time
    from .analyze import claude_cli_status
    if force or not _cc_cache['v'] or time.time() - _cc_cache['t'] > 300:
        _cc_cache['v'] = claude_cli_status(settings=config.load_settings())
        _cc_cache['t'] = time.time()
    return _cc_cache['v']


# 署名水印：作者要求保留，请勿删除或修改。页面里缺了会在这里补上。
WATERMARK = ('<footer id="yestupa-wm" class="wm">made by <a href="https://yestupa.com" target="_blank" rel="noopener">yestupa.com</a>/'
             '<a href="https://yestupa.net" target="_blank" rel="noopener">yestupa.net</a> with ❤️</footer>')


def with_watermark(html):
    if 'id="yestupa-wm"' not in html or 'yestupa.com' not in html:
        html = html.replace('</body>', WATERMARK + '\n</body>') if '</body>' in html else html + WATERMARK
    return html


def export_report(job):
    from .util import check_bundle
    check_bundle()
    web = config.WEB
    html = open(os.path.join(web, 'index.html'), encoding='utf-8').read()
    css = open(os.path.join(web, 'style.css'), encoding='utf-8').read()
    js = open(os.path.join(web, 'app.js'), encoding='utf-8').read()
    data = json.dumps(job, ensure_ascii=False).replace('</', '<\\/')
    html = html.replace('<link rel="stylesheet" href="/static/style.css">', f'<style>{css}</style>')
    html = html.replace('<script src="/static/app.js"></script>', f'<script>window.STATIC_JOB = {data};</script><script>{js}</script>')
    path = os.path.join(config.JOBS, job['id'], '报告.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(with_watermark(html))
    return path


def export_csv(job):
    """把搜索广告和购物广告的全部字段写成 CSV（UTF-8 带 BOM，Excel 能直接打开中文）。"""
    import csv
    r = job.get('results', {})
    j = lambda xs: ' | '.join(xs or [])
    sheets = {
        '搜索广告.csv': (r.get('search') or [], [
            ('页码', lambda a: a.get('page')), ('位置', lambda a: a.get('position')), ('排名', lambda a: a.get('rank')),
            ('广告主', lambda a: a.get('advertiser')), ('域名', lambda a: a.get('domain')), ('Display URL', lambda a: a.get('displayUrl')),
            ('Final URL', lambda a: a.get('finalUrl')), ('标题', lambda a: a.get('headline')), ('描述', lambda a: a.get('description')),
            ('附加链接', lambda a: j(a.get('sitelinks'))), ('促销', lambda a: j(a.get('promos'))), ('运费/退货', lambda a: j(a.get('shipping'))),
            ('评分', lambda a: a.get('rating')), ('其他附加信息', lambda a: j(a.get('extras'))),
            ('AI 卖点角度', lambda a: (a.get('analysis') or {}).get('angle')), ('AI 优点', lambda a: j((a.get('analysis') or {}).get('strengths'))),
            ('AI 可改良', lambda a: j((a.get('analysis') or {}).get('improvements'))), ('AI 改写示例', lambda a: (a.get('analysis') or {}).get('rewrite'))]),
        '购物广告.csv': (r.get('shopping') or [], [
            ('页码', lambda p: p.get('page')), ('区块', lambda p: p.get('section')), ('商品标题', lambda p: p.get('title')),
            ('价格', lambda p: p.get('price')), ('原价', lambda p: p.get('originalPrice')), ('折扣', lambda p: p.get('discount')),
            ('商家', lambda p: p.get('merchant')), ('域名', lambda p: p.get('domain')), ('评分', lambda p: p.get('rating')),
            ('促销', lambda p: j(p.get('promos'))), ('运费/退货', lambda p: j(p.get('shipping'))), ('店铺信息', lambda p: p.get('storeInfo')),
            ('平台', lambda p: p.get('platform')), ('商品ID', lambda p: p.get('productId')), ('Merchant ID', lambda p: p.get('merchantId')),
            ('Offer ID', lambda p: p.get('offerId')), ('Final URL', lambda p: '（需点击广告才能获得，未获取）'),
            ('AI 卖点角度', lambda p: (p.get('analysis') or {}).get('angle')), ('AI 优点', lambda p: j((p.get('analysis') or {}).get('strengths'))),
            ('AI 可改良', lambda p: j((p.get('analysis') or {}).get('improvements'))), ('AI 改写标题', lambda p: (p.get('analysis') or {}).get('rewrite'))]),
    }
    out = []
    for name, (rows, cols) in sheets.items():
        path = os.path.join(config.JOBS, job['id'], name)
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow([c[0] for c in cols])
            for x in rows:
                w.writerow(['' if c[1](x) is None else c[1](x) for c in cols])
        out.append(path)
    return out


def export_copy_csv(job):
    """把最新一次生成的搜索广告导出成 Google Ads Editor 能识别的列（Headline 1-15 / Description 1-4 / Path 1-2 / Final URL）。"""
    import csv
    hist = (job.get('copy') or {}).get('history') or []
    if not hist:
        return None
    entry = hist[0]
    s = entry['result'].get('search') or {}
    hs, ds = (s.get('headlines') or [])[:15], (s.get('descriptions') or [])[:4]
    cols = ['Campaign', 'Ad group', 'Ad type'] + [f'Headline {i}' for i in range(1, 16)] + [f'Description {i}' for i in range(1, 5)] + ['Path 1', 'Path 2', 'Final URL']
    site = (entry['brief'].get('website') or '').strip()
    url = site if site.startswith('http') else (f'https://{site}' if site else '')
    row = ['', '', 'Responsive search ad'] + hs + [''] * (15 - len(hs)) + ds + [''] * (4 - len(ds)) + [s.get('path1', ''), s.get('path2', ''), url]
    path = os.path.join(config.JOBS, job['id'], '我方文案_搜索广告.csv')
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerow(row)
        w.writerow([])
        w.writerow(['附加链接', '描述 1', '描述 2'])
        for sl in s.get('sitelinks') or []:
            w.writerow([sl.get('text'), sl.get('desc1'), sl.get('desc2')])
        w.writerow([])
        w.writerow(['宣传信息'] + (s.get('callouts') or []))
    return path


def open_folder(path):
    if sys.platform.startswith('win'):
        os.startfile(path)  # noqa
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', path])
    else:
        subprocess.Popen(['xdg-open', path])


class ExclusiveServer(ThreadingHTTPServer):
    """独占端口：Windows 上默认的地址复用会让两个面板同时监听同一端口，导致任务被另一个进程接走。"""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        import socket
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path):
        if not os.path.isfile(path):
            return self._json({'error': 'not found'}, 404)
        ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype in ('application/javascript',):
            ctype += '; charset=utf-8'
        with open(path, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode('utf-8'))
        except ValueError:
            return {}

    @staticmethod
    def _safe_join(base, rel):
        p = os.path.realpath(os.path.join(base, rel))
        return p if p.startswith(os.path.realpath(base) + os.sep) else None

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(x) for x in u.path.strip('/').split('/') if x]
        if not parts:
            with open(os.path.join(config.WEB, 'index.html'), encoding='utf-8') as f:
                body = with_watermark(f.read()).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parts[0] == 'static' and len(parts) == 2:
            p = self._safe_join(config.WEB, parts[1])
            return self._file(p) if p else self._json({'error': 'bad path'}, 400)
        if parts[0] == 'files' and len(parts) >= 3:
            base = os.path.join(config.JOBS, parts[1])
            p = self._safe_join(base, '/'.join(parts[2:]))
            return self._file(p) if p else self._json({'error': 'bad path'}, 400)
        if parts[:2] == ['api', 'settings']:
            return self._json(config.public_settings())
        if parts[:2] == ['api', 'brief']:
            from .util import load_json
            return self._json(load_json(os.path.join(config.DATA, 'last_brief.json'), {}) or {})
        if parts[:2] == ['api', 'check']:
            if 'refresh=1' in (u.query or ''):
                _cc_cache['v'] = None
            return self._json(env_check())
        if parts[:2] == ['api', 'jobs']:
            if len(parts) == 2:
                return self._json(manager.list())
            j = manager.get(parts[2])
            return self._json(j) if j else self._json({'error': '任务不存在'}, 404)
        return self._json({'error': 'not found'}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(x) for x in u.path.strip('/').split('/') if x]
        body = self._body()
        if parts[:2] == ['api', 'settings']:
            config.save_settings(body)
            return self._json(config.public_settings())
        if parts[:2] == ['api', 'jobs'] and len(parts) == 2:
            kw = (body.get('keyword') or '').strip()
            if not kw:
                return self._json({'error': '请输入关键词'}, 400)
            return self._json(manager.create(kw, body.get('options') or {}))
        if parts[:2] == ['api', 'jobs'] and len(parts) == 4:
            jid, action = parts[2], parts[3]
            j = manager.get(jid)
            if not j:
                return self._json({'error': '任务不存在'}, 404)
            if action == 'analyze':
                r = manager.reanalyze(jid)
                return self._json(r or {'error': '任务正在运行，稍后再试'}, 200 if r else 409)
            if action == 'cancel':
                manager.cancel(jid)
                return self._json({'ok': True})
            if action == 'delete':
                manager.delete(jid)
                return self._json({'ok': True})
            if action == 'rerun':
                return self._json(manager.create(j['keyword'], j['options']))
            if action == 'export':
                p = export_report(j)
                open_folder(os.path.dirname(p))
                return self._json({'ok': True, 'path': p})
            if action == 'copywrite':
                brief = {k: str(v).strip()[:2000] for k, v in (body or {}).items() if k != 'types'}
                brief['types'] = [t for t in (body or {}).get('types', []) if t in ('search', 'shopping', 'display', 'video')] or ['search']
                if not brief.get('brand') or not brief.get('product'):
                    return self._json({'error': '请至少填写品牌名和产品/卖点'}, 400)
                r = manager.copywrite(jid, brief)
                return self._json(r, 409 if r and r.get('error') else 200)
            if action == 'copycsv':
                p = export_copy_csv(j)
                if not p:
                    return self._json({'error': '还没有生成文案'}, 400)
                open_folder(os.path.dirname(p))
                return self._json({'ok': True, 'path': p})
            if action == 'csv':
                files = export_csv(j)
                open_folder(os.path.dirname(files[0]))
                return self._json({'ok': True, 'files': files})
            if action == 'open':
                open_folder(os.path.join(config.JOBS, jid))
                return self._json({'ok': True})
        return self._json({'error': 'not found'}, 404)


def main():
    global manager
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    from .util import check_bundle
    check_bundle()
    url = f'http://127.0.0.1:{PORT}'
    try:
        srv = ExclusiveServer(('127.0.0.1', PORT), Handler)
    except OSError:
        print(f'面板已经在运行（端口 {PORT} 被占用），直接打开：{url}', flush=True)
        if os.environ.get('ADSPY_NO_BROWSER') != '1':
            webbrowser.open(url)
        return
    manager = Manager()
    print(f'面板已启动：{url}  （关闭这个窗口即可停止）', flush=True)
    if os.environ.get('ADSPY_NO_BROWSER') != '1':
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
