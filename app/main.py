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
from .i18n import L, lang, set_lang
from .jobs import Manager

PORT = int(os.environ.get('ADSPY_PORT', '8765'))
manager = None


def env_check():
    s = config.load_settings()
    out = []
    out.append({'key': 'python', 'name': 'Python', 'ok': sys.version_info >= (3, 10),
                'detail': sys.version.split()[0] + ('' if sys.version_info >= (3, 10) else L('（需要 3.10 或更高）', ' (3.10 or newer required)'))})
    try:
        import playwright  # noqa
        out.append({'key': 'playwright', 'name': 'Playwright', 'ok': True, 'detail': L('已安装', 'installed')})
    except ImportError:
        out.append({'key': 'playwright', 'name': 'Playwright', 'ok': False, 'detail': L('未安装，请先运行“1-安装”', 'not installed; run the installer (1-安装.bat) first')})
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
    out.append({'key': 'browser', 'name': L('浏览器', 'Browser'), 'ok': bool(found), 'detail': L('、', ', ').join(found) if found else
                L('没找到 Chrome 或 Edge（Google 会拦截内置浏览器，强烈建议安装 Chrome）', 'Chrome or Edge not found (Google blocks the bundled browser; please install Chrome)')})
    from .analyze import system_proxy
    proxy = s.get('proxy') or system_proxy()
    try:
        h = [urllib.request.ProxyHandler({'http': proxy, 'https': proxy})] if proxy else []
        urllib.request.build_opener(*h).open(urllib.request.Request('https://www.google.com/generate_204', headers={'User-Agent': 'Mozilla/5.0'}), timeout=8)
        out.append({'key': 'google', 'name': L('访问 Google', 'Google access'), 'ok': True, 'detail': L('正常', 'OK') + (L(f'（代理 {proxy}）', f' (proxy {proxy})') if proxy else '')})
    except Exception as e:
        out.append({'key': 'google', 'name': L('访问 Google', 'Google access'), 'ok': False,
                    'detail': L(f'失败：{str(e)[:80]}。请开启代理/VPN，或在设置里填写代理地址', f'failed: {str(e)[:80]}. Turn on your proxy/VPN, or set a proxy in Settings')})
    engine = s.get('engine') or 'claude_code'
    if engine == 'claude_code':
        ok, detail = claude_status_cached()
        out.append({'key': 'ai', 'name': L('AI 分析：Claude Code', 'AI analysis: Claude Code'), 'ok': ok,
                    'detail': detail + ('' if ok else L('（不影响抓取，只是没有 AI 分析）', ' (collecting still works, just without AI analysis)'))})
    elif engine == 'api':
        out.append({'key': 'ai', 'name': L('AI 分析：Claude API', 'AI analysis: Claude API'), 'ok': bool(s.get('api_key')),
                    'detail': L('已填写 API key', 'API key set') if s.get('api_key') else L('未填写 API key（不影响抓取，只是没有 AI 分析）', 'no API key (collecting still works, just without AI analysis)')})
    else:
        out.append({'key': 'ai', 'name': L('AI 分析', 'AI analysis'), 'ok': True, 'detail': L('已关闭（只抓取）', 'off (collect only)')})
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
    html = html.replace('<script src="/static/app.js"></script>', f'<script>window.STATIC_JOB = {data}; window.STATIC_LANG = "{lang()}";</script><script>{js}</script>')
    path = os.path.join(config.JOBS, job['id'], L('报告.html', 'report.html'))
    with open(path, 'w', encoding='utf-8') as f:
        f.write(with_watermark(html))
    return path


def pos_name(p):
    """搜索广告位置在数据里存的是“顶部/底部”，导出时按界面语言显示。"""
    return {'顶部': L('顶部', 'Top'), '底部': L('底部', 'Bottom')}.get(p, p)


def export_csv(job):
    """把搜索广告和购物广告的全部字段写成 CSV（UTF-8 带 BOM，Excel 能直接打开中文）。"""
    import csv
    r = job.get('results', {})
    j = lambda xs: ' | '.join(xs or [])
    sheets = {
        L('搜索广告.csv', 'search_ads.csv'): (r.get('search') or [], [
            (L('页码', 'Page'), lambda a: a.get('page')), (L('位置', 'Position'), lambda a: pos_name(a.get('position'))), (L('排名', 'Rank'), lambda a: a.get('rank')),
            (L('广告主', 'Advertiser'), lambda a: a.get('advertiser')), (L('域名', 'Domain'), lambda a: a.get('domain')), ('Display URL', lambda a: a.get('displayUrl')),
            ('Final URL', lambda a: a.get('finalUrl')), (L('标题', 'Headline'), lambda a: a.get('headline')), (L('描述', 'Description'), lambda a: a.get('description')),
            (L('附加链接', 'Sitelinks'), lambda a: j(a.get('sitelinks'))), (L('促销', 'Promotions'), lambda a: j(a.get('promos'))),
            (L('运费/退货', 'Shipping/returns'), lambda a: j(a.get('shipping'))),
            (L('评分', 'Rating'), lambda a: a.get('rating')), (L('其他附加信息', 'Other assets'), lambda a: j(a.get('extras'))),
            (L('AI 卖点角度', 'AI angle'), lambda a: (a.get('analysis') or {}).get('angle')), (L('AI 优点', 'AI strengths'), lambda a: j((a.get('analysis') or {}).get('strengths'))),
            (L('AI 可改良', 'AI improvements'), lambda a: j((a.get('analysis') or {}).get('improvements'))), (L('AI 改写示例', 'AI rewrite'), lambda a: (a.get('analysis') or {}).get('rewrite'))]),
        L('购物广告.csv', 'shopping_ads.csv'): (r.get('shopping') or [], [
            (L('页码', 'Page'), lambda p: p.get('page')), (L('区块', 'Section'), lambda p: p.get('section')), (L('商品标题', 'Product title'), lambda p: p.get('title')),
            (L('价格', 'Price'), lambda p: p.get('price')), (L('原价', 'Original price'), lambda p: p.get('originalPrice')), (L('折扣', 'Discount'), lambda p: p.get('discount')),
            (L('商家', 'Merchant'), lambda p: p.get('merchant')), (L('域名', 'Domain'), lambda p: p.get('domain')), (L('评分', 'Rating'), lambda p: p.get('rating')),
            (L('促销', 'Promotions'), lambda p: j(p.get('promos'))), (L('运费/退货', 'Shipping/returns'), lambda p: j(p.get('shipping'))),
            (L('店铺信息', 'Store info'), lambda p: p.get('storeInfo')),
            (L('平台', 'Platform'), lambda p: p.get('platform')), (L('商品ID', 'Product ID'), lambda p: p.get('productId')), ('Merchant ID', lambda p: p.get('merchantId')),
            ('Offer ID', lambda p: p.get('offerId')), ('Final URL', lambda p: L('（需点击广告才能获得，未获取）', '(only available by clicking the ad; not collected)')),
            (L('AI 卖点角度', 'AI angle'), lambda p: (p.get('analysis') or {}).get('angle')), (L('AI 优点', 'AI strengths'), lambda p: j((p.get('analysis') or {}).get('strengths'))),
            (L('AI 可改良', 'AI improvements'), lambda p: j((p.get('analysis') or {}).get('improvements'))), (L('AI 改写标题', 'AI rewritten title'), lambda p: (p.get('analysis') or {}).get('rewrite'))]),
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
    path = os.path.join(config.JOBS, job['id'], L('我方文案_搜索广告.csv', 'my_copy_search_ads.csv'))
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerow(row)
        w.writerow([])
        w.writerow([L('附加链接', 'Sitelink'), L('描述 1', 'Description 1'), L('描述 2', 'Description 2')])
        for sl in s.get('sitelinks') or []:
            w.writerow([sl.get('text'), sl.get('desc1'), sl.get('desc2')])
        w.writerow([])
        w.writerow([L('宣传信息', 'Callouts')] + (s.get('callouts') or []))
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
        set_lang(config.load_settings().get('ui_lang'))
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
            return self._json(j) if j else self._json({'error': L('任务不存在', 'Job not found')}, 404)
        return self._json({'error': 'not found'}, 404)

    def do_POST(self):
        set_lang(config.load_settings().get('ui_lang'))
        u = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(x) for x in u.path.strip('/').split('/') if x]
        body = self._body()
        if parts[:2] == ['api', 'settings']:
            s = config.save_settings(body)
            if 'ui_lang' in body:
                set_lang(s.get('ui_lang'))
                _cc_cache['v'] = None  # 登录状态说明要换语言
            return self._json(config.public_settings())
        if parts[:2] == ['api', 'jobs'] and len(parts) == 2:
            kw = (body.get('keyword') or '').strip()
            if not kw:
                return self._json({'error': L('请输入关键词', 'Please enter a keyword')}, 400)
            return self._json(manager.create(kw, body.get('options') or {}))
        if parts[:2] == ['api', 'jobs'] and len(parts) == 4:
            jid, action = parts[2], parts[3]
            j = manager.get(jid)
            if not j:
                return self._json({'error': L('任务不存在', 'Job not found')}, 404)
            if action == 'analyze':
                r = manager.reanalyze(jid)
                return self._json(r or {'error': L('任务正在运行，稍后再试', 'The job is running; try again later')}, 200 if r else 409)
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
                    return self._json({'error': L('请至少填写品牌名和产品/卖点', 'Please fill in at least the brand and the product/selling points')}, 400)
                r = manager.copywrite(jid, brief)
                return self._json(r, 409 if r and r.get('error') else 200)
            if action == 'copycsv':
                p = export_copy_csv(j)
                if not p:
                    return self._json({'error': L('还没有生成文案', 'No copy generated yet')}, 400)
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
        print(f'面板已经在运行（端口 {PORT} 被占用），直接打开 / Panel already running, opening: {url}', flush=True)
        if os.environ.get('ADSPY_NO_BROWSER') != '1':
            webbrowser.open(url)
        return
    manager = Manager()
    print(f'面板已启动 / Panel started: {url}\n关闭这个窗口即可停止 / Close this window to stop the panel', flush=True)
    if os.environ.get('ADSPY_NO_BROWSER') != '1':
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
