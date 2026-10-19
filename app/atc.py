"""Google 广告透明度中心（Ads Transparency Center）：找广告主 → 取展示广告 / 视频广告 → 渲染截图。

透明度中心没有官方 API，这里在它自己的页面里调用前端使用的 RPC（同源请求，不需要登录）。
"""
import datetime
import os
import re

from .util import download, registrable

HOME = 'https://adstransparency.google.com/?region={r}'
CREATIVE = 'https://adstransparency.google.com/advertiser/{a}/creative/{c}?region={r}'

RPC_JS = """async ({service, req}) => {
  const r = await fetch('/anji/_/rpc/' + service + '?authuser=', {method: 'POST',
    headers: {'content-type': 'application/x-www-form-urlencoded'}, body: 'f.req=' + encodeURIComponent(JSON.stringify(req))});
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return await r.json();
}"""

FMT = {1: 'text', 2: 'image', 3: 'video'}


def _date(ts):
    try:
        return str(datetime.date.fromtimestamp(int(ts)))
    except (TypeError, ValueError):
        return None


class ATC:
    def __init__(self, page, settings, region_code, log):
        self.page, self.settings, self.rc, self.log = page, settings, region_code, log
        self.region = settings.get('region', 'US')

    def open(self):
        self.page.goto(HOME.format(r=self.region), wait_until='domcontentloaded', timeout=45000)
        self.page.wait_for_timeout(2500)

    def rpc(self, service, req):
        return self.page.evaluate(RPC_JS, {'service': service, 'req': req})

    def search(self, flt, fmt=None, n=40, window=None):
        """flt: {'domain': 'x.com'} 或 {'advertiser': 'AR..'}；fmt: 2 图片 / 3 视频 / None 全部；
        window: (开始日期, 结束日期) 的 date，结束日期不含当天，例如“昨天”= (昨天, 今天)。"""
        q = {'8': [self.rc]}
        if fmt:
            q['4'] = fmt
        if window:
            q['6'], q['7'] = (int(d.strftime('%Y%m%d')) for d in window)
        if 'domain' in flt:
            q['12'] = {'1': flt['domain'], '2': True}
        else:
            q['13'] = {'1': [flt['advertiser']]}
        res, tok = [], None
        while len(res) < n:
            req = {'2': min(100, n), '3': q, '7': {'1': 1, '2': 30, '3': self.rc}}
            if tok:
                req['4'] = tok
            j = self.rpc('SearchService/SearchCreatives', req)
            res += j.get('1') or []
            tok = j.get('2')
            if not tok:
                break
        return res[:n]

    def suggestions(self, keyword):
        j = self.rpc('SearchService/SearchSuggestions', {'1': keyword, '2': 10, '3': 10})
        advs, doms = [], []
        for s in j.get('1') or []:
            if '1' in s:
                a = s['1']
                rng = (a.get('4') or {}).get('2') or {}
                advs.append({'id': a.get('2'), 'name': a.get('1'), 'country': a.get('3'),
                             'adsMin': int(rng.get('1') or 0), 'adsMax': int(rng.get('2') or 0)})
            elif '2' in s:
                doms.append(s['2'].get('1'))
        return advs, doms

    def discover(self, keyword, serp_domains, max_sources):
        """返回要抓取的来源列表：优先用搜索结果页里出现的广告商家域名，其次用透明度中心的名称联想。"""
        sources, notes = [], []
        seen = set()
        for d in serp_domains:
            if len(sources) >= max_sources:
                break
            dom = registrable(d)
            if not dom or dom in seen:
                continue
            seen.add(dom)
            try:
                got = self.search({'domain': dom}, None, 5)
            except Exception as e:
                notes.append(f'{dom}：查询失败（{e}）')
                continue
            if not got:
                notes.append(f'{dom}：透明度中心在该地区没有找到这个网域的广告')
                continue
            names = sorted({g.get('12') for g in got if g.get('12')})
            sources.append({'type': 'domain', 'value': dom, 'label': dom, 'advertisers': names, 'from': '搜索结果页广告'})
        if len(sources) < max_sources:
            try:
                advs, doms = self.suggestions(keyword)
            except Exception as e:
                advs, doms = [], []
                notes.append(f'名称联想查询失败（{e}）')
            tokens = [t for t in re.split(r'\W+', keyword.lower()) if len(t) >= 3]
            for d in doms:
                if len(sources) >= max_sources:
                    break
                dom = registrable(d)
                if dom not in seen and any(t in dom for t in tokens):
                    seen.add(dom)
                    sources.append({'type': 'domain', 'value': dom, 'label': dom, 'advertisers': [], 'from': '透明度中心联想（网域）'})
            # 名称联想噪音较大：只要名称包含关键词、且广告数较多的广告主
            advs = [a for a in advs if any(t in (a['name'] or '').lower() for t in tokens) and a['adsMax'] >= 20]
            advs.sort(key=lambda a: -a['adsMax'])
            for a in advs:
                if len(sources) >= max_sources:
                    break
                sources.append({'type': 'advertiser', 'value': a['id'], 'label': a['name'], 'advertisers': [a['name']],
                                'from': f'透明度中心联想（广告主，约 {a["adsMin"]}–{a["adsMax"]} 条广告，注册地 {a["country"]}）'})
        return sources, notes

    def creatives(self, sources, fmt, limit, window=None):
        """按来源轮流取，避免某一个广告主占满名额；按最近展示时间排序。window 见 search()。"""
        per = max(2, -(-limit // max(1, len(sources))) + 2)
        out, seen = [], set()
        for s in sources:
            flt = {'domain': s['value']} if s['type'] == 'domain' else {'advertiser': s['value']}
            try:
                rows = self.search(flt, fmt, per, window)
            except Exception as e:
                self.log(f'  {s["label"]} 取素材失败：{e}')
                continue
            for r in rows:
                if r.get('2') in seen:
                    continue
                seen.add(r.get('2'))
                prev = str(r.get('3') or {})
                simgad = re.search(r'simgad/(\d+)', prev)
                out.append({'id': r.get('2'), 'advertiserId': r.get('1'), 'advertiser': r.get('12'), 'source': s['label'],
                            'format': FMT.get(r.get('4'), str(r.get('4'))), 'firstShown': _date((r.get('6') or {}).get('1')),
                            'lastShown': _date((r.get('7') or {}).get('1')), 'daysShown': r.get('13'),
                            'previewImage': f'https://tpc.googlesyndication.com/archive/simgad/{simgad.group(1)}' if simgad else None,
                            'url': CREATIVE.format(a=r.get('1'), c=r.get('2'), r=self.region)})
        out.sort(key=lambda x: x['lastShown'] or '', reverse=True)
        # 轮流挑选，保证多样性
        by_src = {}
        for x in out:
            by_src.setdefault(x['source'], []).append(x)
        picked = []
        while len(picked) < limit and any(by_src.values()):
            for k in list(by_src):
                if by_src[k] and len(picked) < limit:
                    picked.append(by_src[k].pop(0))
        return picked

    def _visible_adframe(self):
        frames = []
        for f in self.page.query_selector_all('iframe[src*="adframe"]'):
            b = f.bounding_box()
            if b and b['width'] > 50 and b['height'] > 30:
                frames.append((b['width'] * b['height'], f))
        return max(frames, key=lambda x: x[0])[1] if frames else None

    def _yt_ids(self, reqs):
        out = []
        for u in reqs:
            for m in re.findall(r'(?:ytimg\.com/vi(?:_webp)?/|youtube(?:-nocookie)?\.com/embed/|[?&]video_id=)([A-Za-z0-9_-]{11})', u):
                if m not in out:
                    out.append(m)
        return out

    def creatives_recent(self, sources, fmt, limit, today=None):
        """先取“昨天”展示过的广告；昨天没有，再放宽到“过去 7 天”。返回 (列表, 时间段说明, 是否放宽过)。"""
        today = today or datetime.date.today()
        windows = [('昨天', (today - datetime.timedelta(days=1), today)),
                   ('过去 7 天', (today - datetime.timedelta(days=7), today))]
        for i, (name, w) in enumerate(windows):
            items = self.creatives(sources, fmt, limit, w)
            label = f'{name}（{w[0]:%Y-%m-%d}' + ('）' if name == '昨天' else f' 至 {w[1] - datetime.timedelta(days=1):%Y-%m-%d}）')
            if items:
                for it in items:
                    it['window'] = label
                return items, label, i > 0
        return [], label, True

    def render(self, item, shot_path, want='image'):
        """打开广告详情页：截图广告区域，读取广告内文字，找 YouTube 视频 ID。
        want='image'：当前变体没有画面时切到下一个变体；want='video'：没找到 YouTube 视频时切到下一个变体。最多试 5 个变体。"""
        reqs = []
        handler = lambda r: reqs.append(r.url)
        self.page.on('request', handler)
        shot, texts, variants, tried = None, [], None, 0
        try:
            self.page.goto(item['url'], wait_until='domcontentloaded', timeout=45000)
            self.page.wait_for_timeout(6000)
            m = re.search(r'(\d+)\s+(?:of|/|个变体（共)\s*(\d+)', self.page.inner_text('body')[:3000])
            variants = int(m.group(2)) if m else 1
            for tried in range(1, min(variants, 5) + 1):
                el = self._visible_adframe()
                if el and not shot:
                    try:
                        os.makedirs(os.path.dirname(shot_path), exist_ok=True)
                        el.screenshot(path=shot_path, type='jpeg', quality=85)
                        shot = shot_path
                    except Exception:
                        pass
                done = bool(shot) if want == 'image' else bool(self._yt_ids(reqs))
                if done or tried >= min(variants, 5):
                    break
                nxt = self.page.query_selector('.variation-right-arrow')
                if not nxt:
                    break
                nxt.click()
                self.page.wait_for_timeout(3500)
                if want == 'video' and self._yt_ids(reqs):
                    # 视频变体：用这个变体的画面替换之前的截图
                    el = self._visible_adframe()
                    if el:
                        try:
                            el.screenshot(path=shot_path, type='jpeg', quality=85)
                            shot = shot_path
                        except Exception:
                            pass
                    break
        finally:
            self.page.remove_listener('request', handler)
        for f in self.page.frames:
            if 'adframe' not in f.url:
                continue
            try:
                t = f.inner_text('body', timeout=3000).strip()
            except Exception:
                continue
            if t and not t.startswith('window.') and '{' not in t[:80]:
                texts.append(re.sub(r'\n{2,}', '\n', t)[:600])
        return {'screenshot': shot, 'texts': texts, 'ytIds': self._yt_ids(reqs), 'variants': variants, 'variantsTried': tried}

    def fetch_image(self, item, path):
        """下载原始图片。Google 已删除的旧图会返回 404 或 1×1 占位图，这时返回 None 并记下原因。"""
        if not item.get('previewImage'):
            return None
        try:
            download(item['previewImage'], path, self.settings.get('proxy'), timeout=25)
            from PIL import Image
            im = Image.open(path)
            if im.width < 10 or im.height < 10:
                item['imageNote'] = 'Google 已删除这条广告的原始图片'
                os.remove(path)
                return None
            im.convert('RGB').save(path, 'JPEG', quality=88)
            return path
        except Exception as e:
            item['imageNote'] = 'Google 已删除这条广告的原始图片' if '404' in str(e) else f'原始图片下载失败：{str(e)[:60]}'
            if os.path.exists(path):
                os.remove(path)
            return None
