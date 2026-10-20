"""抓取任务：排队、逐步执行、记录每一步的状态和失败原因。任务数据保存在 data/jobs/<任务ID>/job.json。"""
import datetime
import glob
import os
import queue
import re
import shutil
import threading
import traceback

from . import config
from .i18n import L, set_lang
from .util import host_of, load_json, registrable, save_json, to_jpg

def steps():
    return [
        ('browser', L('启动浏览器', 'Start browser')),
        ('serp', L('搜索结果页（搜索广告 + 购物广告）', 'Search results (search + shopping ads)')),
        ('sources', L('在透明度中心查找广告主', 'Find advertiser in Transparency Center')),
        ('display', L('展示广告', 'Display ads')),
        ('video', L('视频广告', 'Video ads')),
        ('analysis', L('AI 分析', 'AI analysis')),
    ]


AI_SCOPES = ('AI 分析', 'AI analysis')


class Cancelled(Exception):
    pass


def _slug(s):
    return re.sub(r'[^0-9A-Za-z]+', '-', s).strip('-')[:30] or 'kw'


class Job:
    def __init__(self, data):
        self.data = data
        self.dir = os.path.join(config.JOBS, data['id'])
        self.lock = threading.Lock()
        self.cancel = False
        self.deleted = False

    @property
    def id(self):
        return self.data['id']

    def save(self):
        if self.deleted:  # 任务已被删除：后台线程收尾时不要再把文件夹写回来
            return
        with self.lock:
            save_json(os.path.join(self.dir, 'job.json'), self.data)

    def log(self, msg):
        line = datetime.datetime.now().strftime('%H:%M:%S ') + msg
        self.data.setdefault('log', []).append(line)
        self.data['log'] = self.data['log'][-300:]
        print(f'[{self.id}] {msg}', flush=True)
        self.save()

    def step(self, key, status=None, msg=None, count=None, reason=None):
        st = self.data['steps'][key]
        if status:
            st['status'] = status
        if msg is not None:
            st['msg'] = msg
        if count is not None:
            st['count'] = count
        if reason:
            st.setdefault('reasons', []).append(reason)
        self.data['current'] = msg if status == 'running' else self.data.get('current')
        self.save()

    def notice(self, scope, message, level='warn'):
        self.data.setdefault('notices', []).append({'scope': scope, 'level': level, 'message': message})
        self.save()

    def check(self):
        if self.cancel:
            raise Cancelled()


class Manager:
    def __init__(self):
        config.ensure_dirs()
        set_lang(config.load_settings().get('ui_lang'))
        self.q = queue.Queue()
        self.jobs = {}
        for p in glob.glob(os.path.join(config.JOBS, '*', 'job.json')):
            d = load_json(p)
            if d:
                if d.get('status') in ('queued', 'running'):
                    d['status'] = 'failed'
                    d.setdefault('notices', []).append({'scope': L('任务', 'Job'), 'level': 'error',
                                                        'message': L('面板被关闭，任务中断。可以点“重新抓取”。', 'The panel was closed and the job stopped. Click “Run again”.')})
                self.jobs[d['id']] = Job(d)
        threading.Thread(target=self._worker, daemon=True).start()

    # ---- 对外接口 ----
    def list(self):
        rows = []
        for j in self.jobs.values():
            d = j.data
            rows.append({'id': d['id'], 'keyword': d['keyword'], 'status': d['status'], 'createdAt': d['createdAt'],
                         'region': d['options'].get('region'), 'counts': {k: len(v) for k, v in d.get('results', {}).items() if isinstance(v, list)}})
        return sorted(rows, key=lambda r: r['createdAt'], reverse=True)

    def get(self, jid):
        j = self.jobs.get(jid)
        return j.data if j else None

    def create(self, keyword, options):
        s = config.load_settings()
        now = datetime.datetime.now()
        jid = now.strftime('%Y%m%d-%H%M%S-') + _slug(keyword)
        opts = {'region': options.get('region') or s['region'],
                'types': options.get('types') or {'search': True, 'shopping': True, 'display': True, 'video': True},
                'serpPages': max(1, min(10, int(options.get('serpPages') or s['serp_pages']))),
                'targetDomain': registrable(host_of(options.get('targetDomain') or '')) or '',
                'maxAdvertisers': int(options.get('maxAdvertisers') or s['max_advertisers']),
                'maxDisplay': int(options.get('maxDisplay') or s['max_display']),
                'maxVideo': int(options.get('maxVideo') or s['max_video']),
                'analyze': bool(options.get('analyze', s['auto_analyze']))}
        data = {'id': jid, 'keyword': keyword.strip(), 'createdAt': now.isoformat(timespec='seconds'), 'status': 'queued',
                'lang': 'en' if L('zh', 'en') == 'en' else 'zh',
                'options': opts, 'steps': {k: {'label': lab, 'status': 'pending'} for k, lab in steps()},
                'results': {}, 'notices': [], 'log': []}
        job = Job(data)
        os.makedirs(job.dir, exist_ok=True)
        job.save()
        self.jobs[jid] = job
        self.q.put((jid, 'full'))
        return data

    def reanalyze(self, jid):
        j = self.jobs.get(jid)
        if not j or j.data['status'] in ('queued', 'running'):
            return None
        j.data['status'] = 'queued'
        j.data['lang'] = L('zh', 'en')  # 重新分析时用当前的界面语言
        j.data['steps']['analysis'] = {'label': L('AI 分析', 'AI analysis'), 'status': 'pending'}
        j.data['notices'] = [n for n in j.data.get('notices', []) if n['scope'] not in AI_SCOPES]
        j.save()
        self.q.put((jid, 'analyze'))
        return j.data

    def copywrite(self, jid, brief):
        """排队生成我方文案。不改变任务本身的抓取状态，进度记在 job.data['copy']。"""
        j = self.jobs.get(jid)
        if not j:
            return None
        if j.data['status'] in ('queued', 'running') or (j.data.get('copy') or {}).get('status') in ('queued', 'running'):
            return {'error': L('这个任务正在运行，等它结束后再生成文案', 'This job is running. Generate copy after it finishes.')}
        j.data['copy'] = {'status': 'queued', 'msg': L('排队中', 'Queued'), 'brief': brief, 'lang': L('zh', 'en'), 'history': (j.data.get('copy') or {}).get('history', [])}
        j.save()
        save_json(os.path.join(config.DATA, 'last_brief.json'), brief)  # 记住上次填写的我方信息，方便下次预填
        self.q.put((jid, 'copy'))
        return j.data

    def cancel(self, jid):
        j = self.jobs.get(jid)
        if j and j.data['status'] in ('queued', 'running'):
            j.cancel = True
            if j.data['status'] == 'queued':
                j.data['status'] = 'cancelled'
                j.save()
        return bool(j)

    def delete(self, jid):
        j = self.jobs.pop(jid, None)
        if j:
            j.cancel = True
            j.deleted = True
            shutil.rmtree(j.dir, ignore_errors=True)
        return bool(j)

    # ---- 执行 ----
    def _worker(self):
        while True:
            jid, mode = self.q.get()
            job = self.jobs.get(jid)
            if not job or job.data['status'] == 'cancelled':
                continue
            # 任务里的提示和 AI 输出用创建任务（或点按钮）时的界面语言
            set_lang((job.data.get('copy') or {}).get('lang') if mode == 'copy' else job.data.get('lang'))
            if mode == 'copy':
                run_copy(job, config.load_settings())
                continue
            job.data['status'] = 'running'
            job.data['startedAt'] = datetime.datetime.now().isoformat(timespec='seconds')
            job.save()
            try:
                settings = config.load_settings()
                if mode == 'full':
                    run_collect(job, settings)
                run_analysis(job, settings, force=(mode == 'analyze'))
                job.data['status'] = 'done'
            except Cancelled:
                job.data['status'] = 'cancelled'
                job.log(L('任务已取消', 'Job cancelled'))
            except Exception as e:
                job.data['status'] = 'failed'
                job.notice(L('任务', 'Job'), L('任务出错：', 'Job error: ') + str(e), 'error')
                job.log(traceback.format_exc()[-1500:])
            job.data['finishedAt'] = datetime.datetime.now().isoformat(timespec='seconds')
            job.data['current'] = None
            job.save()
            if job.deleted:  # 运行中被删除：等浏览器关闭后再清一次素材文件
                shutil.rmtree(job.dir, ignore_errors=True)


def run_collect(job, settings):
    from playwright.sync_api import sync_playwright

    from . import atc as atc_mod
    from . import browser as br
    from . import serp as serp_mod
    from . import youtube

    opts, res, kw = job.data['options'], job.data['results'], job.data['keyword']
    types = opts['types']
    s = dict(settings, region=opts['region'])
    root = job.dir
    with sync_playwright() as pw:
        job.step('browser', 'running', L('正在启动浏览器', 'Starting the browser'))
        try:
            ctx, name = br.launch(pw, s, job.log)
        except br.BrowserError as e:
            job.step('browser', 'failed', str(e))
            for k in ('serp', 'sources', 'display', 'video'):
                job.step(k, 'skipped', L('浏览器没有启动', 'The browser did not start'))
            job.notice(L('浏览器', 'Browser'), str(e), 'error')
            return
        job.step('browser', 'done', L('已启动 ', 'Started ') + name)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            # ---- 搜索结果页 ----
            serp_domains = []
            if types.get('search') or types.get('shopping') or types.get('display') or types.get('video'):
                job.step('serp', 'running', L('正在打开 Google 搜索结果页', 'Opening Google search results'))
                try:
                    search, shopping, meta = serp_mod.collect(page, kw, s, root, lambda m: job.step('serp', 'running', m), job.log,
                                                              pages=opts.get('serpPages', 5))
                    res['serpMeta'] = meta
                    res['search'] = search if types.get('search') else []
                    res['shopping'] = shopping if types.get('shopping') else []
                    msg = L(f'抓了 {meta["pagesDone"]} 页：搜索广告 {len(search)} 条，购物广告 {len(shopping)} 条',
                            f'{meta["pagesDone"]} pages: {len(search)} search ads, {len(shopping)} shopping ads')
                    for n in meta.get('pageNotes', []):
                        job.notice(L('翻页', 'Pages'), n, 'info' if ('没有更多页面' in n or 'no more pages' in n) else 'warn')
                    if meta.get('location'):
                        msg += L('；Google 判断的位置：', '; location according to Google: ') + meta['location']
                    job.step('serp', 'done', msg, len(search) + len(shopping))
                    if not search and types.get('search'):
                        job.notice(L('搜索广告', 'Search ads'), L(
                            f'前 {meta["pagesDone"]} 页搜索结果都没有出现搜索广告。可能这个词没有广告主竞价，或者广告按你的 IP 所在地投放（当前位置见上方）。可以换更有商业意图的词，或者把代理切到目标国家再试。',
                            f'No search ads in the first {meta["pagesDone"]} pages. Nobody may be bidding on this keyword, or ads are served by your IP location (shown above). Try a more commercial keyword, or switch your proxy/VPN to the target country.'))
                    if not shopping and types.get('shopping'):
                        job.notice(L('购物广告', 'Shopping ads'), L(
                            '这次搜索结果页没有出现购物广告（Sponsored products / stores）。品牌词、服务类关键词通常没有购物广告；也可能当前地区没有投放。',
                            'No Shopping ads (Sponsored products / stores) on the results pages. Brand and service keywords usually have none, or nobody advertises in this region.'))
                    if meta.get('location') and opts['region'] == 'US' and not re.search(r'(?i)(, [A-Z]{2}\b|United States|USA)', meta['location']):
                        job.notice(L('地区', 'Region'), L(
                            f'Google 判断你的位置是“{meta["location"]}”，和所选地区（{opts["region"]}）可能不一致。搜索/购物广告按访问者 IP 投放，请把代理切到目标国家。',
                            f'Google thinks you are in “{meta["location"]}”, which may not match the selected region ({opts["region"]}). Ads are served by IP location; switch your proxy/VPN to the target country.'))
                    # 透明度中心的候选网站，按“搜索结果页上的先后”排：搜索广告（第 1 页顶部优先）→ 购物广告 → 自然结果
                    order = lambda a: (a.get('page', 1), 0 if a.get('position') == '顶部' else 1, a.get('rank', 99))
                    for a in sorted(search, key=order):
                        if a.get('domain'):
                            pos = a.get('position', '')
                            serp_domains.append((a['domain'], L(
                                f'第 {a.get("page", 1)} 页{pos}第 {a.get("rank")} 条搜索广告（{a.get("advertiser") or a["domain"]}）',
                                f'search ad #{a.get("rank")} at the {"top" if pos == "顶部" else "bottom"} of page {a.get("page", 1)} ({a.get("advertiser") or a["domain"]})')))
                    for p in shopping:
                        if p.get('domain'):
                            serp_domains.append((p['domain'], L(f'第 {p.get("page", 1)} 页购物广告（{p.get("merchant") or p["domain"]}）',
                                                                f'shopping ad on page {p.get("page", 1)} ({p.get("merchant") or p["domain"]})')))
                    for i, d in enumerate(meta.get('organic', [])):
                        serp_domains.append((d, L(f'第 1 页第 {i + 1} 条自然搜索结果', f'organic result #{i + 1} on page 1')))
                except Cancelled:
                    raise
                except Exception as e:
                    job.step('serp', 'failed', str(e)[:300], reason=str(e)[:300])
                    job.notice(L('搜索/购物广告', 'Search/shopping ads'), L('抓取失败：', 'Collection failed: ') + str(e), 'error')
                    res['search'], res['shopping'] = [], []
            job.check()

            # ---- 透明度中心：只抓一个目标网站 ----
            # 透明度中心不能按关键词搜素材，所以用“搜索结果页上的第一个网站”（或用户手动指定的网站）作为目标。
            need_atc = types.get('display') or types.get('video')
            sources = []
            if need_atc:
                job.step('sources', 'running', L('正在确定透明度中心的目标网站', 'Choosing the target website for the Transparency Center'))
                atc = atc_mod.ATC(page, s, config.region_code(opts['region']), job.log)
                cands, seen = [], set()
                if opts.get('targetDomain'):
                    cands.append((opts['targetDomain'], L('你在面板里手动指定', 'entered by you in the panel')))
                for d, why in serp_domains:
                    dom = registrable(d)
                    if dom and dom not in seen:
                        seen.add(dom)
                        cands.append((dom, why))
                try:
                    atc.open()
                    tried = []
                    for dom, why in cands[:5]:
                        dom = registrable(host_of(dom)) or dom
                        got = atc.search({'domain': dom}, None, 5)
                        if got:
                            names = sorted({g.get('12') for g in got if g.get('12')})
                            sources = [{'type': 'domain', 'value': dom, 'label': dom, 'advertisers': names, 'from': why}]
                            break
                        tried.append(L(f'{dom}（{why}）', f'{dom} ({why})'))
                        if opts.get('targetDomain') and dom == registrable(host_of(opts['targetDomain'])):
                            break  # 用户指定的网站查不到，就不自动换别的网站
                    res['sources'] = sources
                    res['atcTarget'] = sources[0] if sources else None
                    if sources:
                        t = sources[0]
                        job.step('sources', 'done', L(f'目标网站：{t["value"]}（来自{t["from"]}）；透明度中心里的广告主：{"、".join(t["advertisers"]) or "—"}',
                                                      f'Target website: {t["value"]} (from {t["from"]}); advertiser in the Transparency Center: {", ".join(t["advertisers"]) or "—"}'), 1)
                        if tried:
                            job.notice(L('目标网站', 'Target website'), L('这些网站在透明度中心没有查到广告，已顺延：', 'No ads in the Transparency Center for these websites, moved on: ') + '; '.join(tried), 'info')
                    else:
                        why = (L('你指定的网站在透明度中心没有查到该地区的广告：', 'The website you entered has no ads in this region in the Transparency Center: ') + '; '.join(tried)) if opts.get('targetDomain') else \
                              (L('搜索结果页上没有可以作为目标的网站', 'No website on the results pages could be used as the target') if not cands else
                               L('搜索结果页前几个网站在透明度中心都没有查到该地区的广告：', 'None of the first websites on the results pages has ads in this region in the Transparency Center: ') + '; '.join(tried))
                        job.step('sources', 'failed', why, 0, reason=why)
                        job.notice(L('展示/视频广告', 'Display/video ads'), why + L('。可以在“目标网站”里填竞对的域名再试。', '. Enter a competitor domain under “Target website” and try again.'))
                except Exception as e:
                    job.step('sources', 'failed', L('透明度中心无法访问：', 'Cannot open the Transparency Center: ') + str(e), reason=str(e)[:200])
                    job.notice(L('展示/视频广告', 'Display/video ads'), L('Google 广告透明度中心无法访问：', 'Cannot open the Google Ads Transparency Center: ') + str(e), 'error')
            else:
                job.step('sources', 'skipped', L('没有勾选展示/视频广告', 'Display/video ads not selected'))
            job.check()

            # ---- 展示广告 ----
            if types.get('display') and sources:
                job.step('display', 'running', L('正在获取展示广告列表（先查昨天，没有再查过去 7 天）', 'Getting display ads (yesterday first, then the last 7 days)'))
                items, label, widened = atc.creatives_recent(sources, 2, opts['maxDisplay'])
                res.setdefault('windows', {})['display'] = label if items else None
                if items and widened:
                    job.notice(L('展示广告', 'Display ads'), L('昨天没有找到展示广告，已改为抓取过去 7 天：', 'No display ads yesterday, used the last 7 days instead: ') + label, 'info')
                elif not items:
                    job.notice(L('展示广告', 'Display ads'), L(f'昨天和过去 7 天都没有找到这些广告主的展示广告（{label}）', f'No display ads from this advertiser yesterday or in the last 7 days ({label})'))
                done, rendered_fail = [], 0
                for i, it in enumerate(items):
                    job.check()
                    job.step('display', 'running', L(f'处理展示广告 {i + 1}/{len(items)}（{it["advertiser"]}）', f'Display ad {i + 1}/{len(items)} ({it["advertiser"]})'))
                    p = os.path.join(root, 'assets', 'display', f'{it["id"]}.jpg')
                    if atc.fetch_image(it, p):
                        it['image'] = f'assets/display/{it["id"]}.jpg'
                        it['captureMethod'] = L('原始图片', 'original image')
                    else:
                        try:
                            r = atc.render(it, p, want='image')
                            it['texts'], it['variants'] = r['texts'], r['variants']
                            if r['screenshot']:
                                it['image'] = f'assets/display/{it["id"]}.jpg'
                                it['captureMethod'] = L('渲染截图', 'rendered screenshot')
                        except Exception as e:
                            it['error'] = L('渲染失败：', 'Rendering failed: ') + str(e).splitlines()[0][:120]
                    if not it.get('image'):
                        rendered_fail += 1
                        reason = L('透明度中心没有显示这条广告的预览（Google 未提供可渲染的画面）', 'The Transparency Center shows no preview for this ad')
                        if it.get('imageNote'):
                            reason = it['imageNote'] + L('，', '; ') + reason
                        it.setdefault('error', reason)
                    done.append(it)
                res['display'] = done
                ok = sum(1 for x in done if x.get('image'))
                job.step('display', 'done' if ok else ('failed' if items else 'done'),
                         L(f'展示广告 {ok} 条', f'{ok} display ads') + (L(f'，{rendered_fail} 条没拿到画面', f', {rendered_fail} without an image') if rendered_fail else '')
                         + (L('；时间段：', '; period: ') + label if items else L('；昨天和过去 7 天都没有', '; none yesterday or in the last 7 days')), ok)
            elif types.get('display'):
                job.step('display', 'skipped', L('没有可追查的广告主', 'No advertiser to look up'))
            else:
                job.step('display', 'skipped', L('未勾选', 'Not selected'))
            job.check()

            # ---- 视频广告 ----
            if types.get('video') and sources:
                job.step('video', 'running', L('正在获取视频广告列表（先查昨天，没有再查过去 7 天）', 'Getting video ads (yesterday first, then the last 7 days)'))
                items, vlabel, widened = atc.creatives_recent(sources, 3, opts['maxVideo'] * 2)
                res.setdefault('windows', {})['video'] = vlabel if items else None
                if items and widened:
                    job.notice(L('视频广告', 'Video ads'), L('昨天没有找到视频广告，已改为抓取过去 7 天：', 'No video ads yesterday, used the last 7 days instead: ') + vlabel, 'info')
                elif not items:
                    job.notice(L('视频广告', 'Video ads'), L(f'昨天和过去 7 天都没有找到这些广告主的视频广告（{vlabel}）', f'No video ads from this advertiser yesterday or in the last 7 days ({vlabel})'))
                done, seen_vid = [], set()
                for i, it in enumerate(items):
                    if len([x for x in done if x.get('youtube')]) >= opts['maxVideo']:
                        break
                    job.check()
                    job.step('video', 'running', L(f'处理视频广告 {i + 1}/{len(items)}（{it["advertiser"]}）', f'Video ad {i + 1}/{len(items)} ({it["advertiser"]})'))
                    p = os.path.join(root, 'assets', 'video', it['id'], 'preview.jpg')
                    try:
                        r = atc.render(it, p, want='video')
                    except Exception as e:
                        it['error'] = L('渲染失败：', 'Rendering failed: ') + str(e).splitlines()[0][:120]
                        done.append(it)
                        continue
                    it['texts'], it['variants'] = r['texts'], r['variants']
                    if r['screenshot']:
                        it['image'] = f'assets/video/{it["id"]}/preview.jpg'
                    vid = next((v for v in r['ytIds'] if v not in seen_vid), None)
                    if not vid:
                        it['error'] = L(f'这条“视频广告”在透明度中心只显示为其他版式（如地图/门店卡片），试了 {r.get("variantsTried") or 1} 个变体都没有 YouTube 视频',
                                        f'This “video ad” only shows other formats in the Transparency Center (e.g. map/store cards); no YouTube video in {r.get("variantsTried") or 1} variant(s)')
                        done.append(it)
                        continue
                    seen_vid.add(vid)
                    job.step('video', 'running', L(f'播放并截图 YouTube 视频 {vid}（{it["advertiser"]}）', f'Playing and capturing YouTube video {vid} ({it["advertiser"]})'))
                    it['youtube'] = youtube.capture(page, vid, s, os.path.join(root, 'assets', 'video', it['id']), f'assets/video/{it["id"]}')
                    done.append(it)
                res['video'] = done
                ok = sum(1 for x in done if x.get('youtube'))
                job.step('video', 'done' if ok or not items else 'failed',
                         L(f'视频广告 {ok} 条（含 YouTube 视频）', f'{ok} video ads with a YouTube video')
                         + (L(f'，另有 {len(done) - ok} 条没找到视频源', f', {len(done) - ok} more without a video source') if len(done) > ok else '')
                         + (L('；时间段：', '; period: ') + vlabel if items else L('；昨天和过去 7 天都没有', '; none yesterday or in the last 7 days')), ok)
                if items and not ok:
                    job.notice(L('视频广告', 'Video ads'), L('找到了视频广告，但都没有对应的 YouTube 视频（多是效果最大化广告生成的地图/门店版式）。',
                                                          'Video ads were found, but none has a YouTube video (mostly map/store formats generated by Performance Max).'))
            elif types.get('video'):
                job.step('video', 'skipped', L('没有可追查的广告主', 'No advertiser to look up'))
            else:
                job.step('video', 'skipped', L('未勾选', 'Not selected'))
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def run_analysis(job, settings, force=False):
    from .analyze import AnalyzeError, Analyzer, ClaudeCodeAnalyzer

    opts, res, kw = job.data['options'], job.data['results'], job.data['keyword']
    engine = settings.get('engine') or 'claude_code'
    if not force and not opts.get('analyze'):
        job.step('analysis', 'skipped', L('本次任务没有勾选“抓完自动 AI 分析”，可以稍后点“AI 分析”', 'Auto AI analysis was off for this job; click “AI analysis” any time'))
        return
    if engine == 'none':
        job.step('analysis', 'skipped', L('设置里选择了“不做 AI 分析”', 'AI analysis is turned off in Settings'))
        return
    if engine == 'api' and not settings.get('api_key'):
        job.step('analysis', 'skipped', L('未配置 Claude API key，跳过 AI 分析', 'No Claude API key, AI analysis skipped'))
        job.notice(L('AI 分析', 'AI analysis'), L('分析引擎选的是 Claude API，但没有填 API key。可以在“设置”里填 key，或者把分析引擎改成 Claude Code（用订阅，不需要 key），然后点“AI 分析”。',
                                               'The engine is Claude API but no API key is set. Add a key in Settings, or switch the engine to Claude Code (uses your subscription, no key), then click “AI analysis”.'))
        return
    try:
        an = ClaudeCodeAnalyzer(settings, job.dir) if engine == 'claude_code' else Analyzer(settings)
    except AnalyzeError as e:
        job.step('analysis', 'failed', str(e), reason=str(e))
        job.notice(L('AI 分析', 'AI analysis'), str(e), 'error')
        return
    except Exception as e:
        job.step('analysis', 'failed', L('无法初始化分析引擎：', 'Cannot start the analysis engine: ') + str(e))
        return
    job.log(L('分析引擎：', 'Analysis engine: ') + ('Claude Code' if engine == 'claude_code' else 'Claude API') + f' ({an.model})')
    errors = []

    def attempt(label, fn):
        job.check()
        job.step('analysis', 'running', L('正在分析：', 'Analyzing: ') + label)
        try:
            return fn()
        except AnalyzeError as e:
            errors.append(f'{label}: {e}')
            job.log(L('分析失败 ', 'Analysis failed ') + f'{label}: {e}')
            if e.fatal:
                raise
        return None

    # 已经分析过的素材不再重复分析（再次点“AI 分析”时只补缺的，省额度）；整体总结每次都重新生成
    todo = lambda xs: [x for x in xs if not x.get('analysis')]
    try:
        search = todo(res.get('search', []))
        if search:
            r = attempt(L('搜索广告', 'search ads'), lambda: an.search_ads(kw, search)) or {}
            for a in search:
                if a['id'] in r:
                    a['analysis'] = r[a['id']]
        shop = todo(res.get('shopping', []))
        if shop:
            r = attempt(L(f'购物广告（{len(shop)} 条）', f'{len(shop)} shopping ads'), lambda: an.shopping_ads(kw, shop, job.dir)) or {}
            for a in shop:
                if a['id'] in r:
                    a['analysis'] = r[a['id']]
        disp = todo([x for x in res.get('display', []) if x.get('image')])
        if disp:
            r = attempt(L(f'展示广告（{len(disp)} 条）', f'{len(disp)} display ads'), lambda: an.display_ads(kw, disp, job.dir)) or {}
            for a in disp:
                if a['id'] in r:
                    a['analysis'] = r[a['id']]
        for v in todo([x for x in res.get('video', []) if x.get('youtube') or x.get('image')]):
            out = attempt(L('视频 ', 'video ') + str((v.get("youtube") or {}).get("title") or v["id"]), lambda v=v: an.video_ad(kw, v, job.dir))
            if out:
                v['analysis'] = out
        job.save()
        s = attempt(L('整体总结', 'overall summary'), lambda: an.summary(kw, job.data))
        if s:
            job.data['summary'] = s
    except AnalyzeError as e:
        job.step('analysis', 'failed', str(e), reason=str(e))
        job.notice(L('AI 分析', 'AI analysis'), str(e), 'error')
        return
    job.data['usage'] = an.usage
    msg = L(f'完成 {an.usage["calls"]} 次调用（输入约 {an.usage["input"]:,} tokens，输出约 {an.usage["output"]:,} tokens）',
            f'Done: {an.usage["calls"]} calls (about {an.usage["input"]:,} input tokens, {an.usage["output"]:,} output tokens)')
    if errors:
        job.step('analysis', 'done', msg + L(f'；{len(errors)} 项失败', f'; {len(errors)} failed'))
        for e in errors:
            job.notice(L('AI 分析', 'AI analysis'), e)
    else:
        job.step('analysis', 'done', msg)


def run_copy(job, settings):
    """参考竞对素材，为我方生成广告文案（搜索 / 购物 / 展示 / 视频）。"""
    from .analyze import AnalyzeError, Analyzer, ClaudeCodeAnalyzer

    c = job.data['copy']
    engine = settings.get('engine') or 'claude_code'

    def upd(status, msg):
        c['status'], c['msg'] = status, msg
        job.save()

    if engine == 'none':
        return upd('failed', L('设置里的分析引擎是“不做 AI 分析”，请先选择 Claude Code 或 Claude API', 'AI analysis is turned off in Settings. Choose Claude Code or Claude API first.'))
    if engine == 'api' and not settings.get('api_key'):
        return upd('failed', L('分析引擎是 Claude API，但没有填 API key', 'The engine is Claude API but no API key is set'))
    upd('running', L('正在生成我方文案（通常 1–3 分钟）', 'Writing your ad copy (usually 1–3 minutes)'))
    try:
        an = ClaudeCodeAnalyzer(settings, job.dir) if engine == 'claude_code' else Analyzer(settings)
        result = an.write_copy(job.data['keyword'], job.data, c['brief'])
    except AnalyzeError as e:
        return upd('failed', str(e))
    except Exception as e:
        job.log(traceback.format_exc()[-1500:])
        return upd('failed', L('生成失败：', 'Generation failed: ') + str(e))
    entry = {'createdAt': datetime.datetime.now().isoformat(timespec='seconds'), 'brief': c['brief'], 'result': result, 'usage': an.usage}
    c['history'] = ([entry] + c.get('history', []))[:10]  # 保留最近 10 次
    over = len(result.get('violations') or [])
    upd('done', L(f'已生成（{an.usage["calls"]} 次调用）', f'Done ({an.usage["calls"]} calls)')
        + (L(f'；仍有 {over} 条超出字符上限，已标红', f'; {over} still over the character limit (in red)') if over else L('；全部在字符上限内', '; all within the character limits')))
