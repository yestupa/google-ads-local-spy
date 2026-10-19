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
from .util import host_of, load_json, registrable, save_json, to_jpg

STEPS = [
    ('browser', '启动浏览器'),
    ('serp', '搜索结果页（搜索广告 + 购物广告）'),
    ('sources', '在透明度中心查找广告主'),
    ('display', '展示广告'),
    ('video', '视频广告'),
    ('analysis', 'AI 分析'),
]


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
        self.q = queue.Queue()
        self.jobs = {}
        for p in glob.glob(os.path.join(config.JOBS, '*', 'job.json')):
            d = load_json(p)
            if d:
                if d.get('status') in ('queued', 'running'):
                    d['status'] = 'failed'
                    d.setdefault('notices', []).append({'scope': '任务', 'level': 'error', 'message': '面板被关闭，任务中断。可以点“重新抓取”。'})
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
                'options': opts, 'steps': {k: {'label': lab, 'status': 'pending'} for k, lab in STEPS},
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
        j.data['steps']['analysis'] = {'label': 'AI 分析', 'status': 'pending'}
        j.data['notices'] = [n for n in j.data.get('notices', []) if n['scope'] != 'AI 分析']
        j.save()
        self.q.put((jid, 'analyze'))
        return j.data

    def copywrite(self, jid, brief):
        """排队生成我方文案。不改变任务本身的抓取状态，进度记在 job.data['copy']。"""
        j = self.jobs.get(jid)
        if not j:
            return None
        if j.data['status'] in ('queued', 'running') or (j.data.get('copy') or {}).get('status') in ('queued', 'running'):
            return {'error': '这个任务正在运行，等它结束后再生成文案'}
        j.data['copy'] = {'status': 'queued', 'msg': '排队中', 'brief': brief, 'history': (j.data.get('copy') or {}).get('history', [])}
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
                job.log('任务已取消')
            except Exception as e:
                job.data['status'] = 'failed'
                job.notice('任务', f'任务出错：{e}', 'error')
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
        job.step('browser', 'running', '正在启动浏览器')
        try:
            ctx, name = br.launch(pw, s, job.log)
        except br.BrowserError as e:
            job.step('browser', 'failed', str(e))
            for k in ('serp', 'sources', 'display', 'video'):
                job.step(k, 'skipped', '浏览器没有启动')
            job.notice('浏览器', str(e), 'error')
            return
        job.step('browser', 'done', f'已启动 {name}')
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            # ---- 搜索结果页 ----
            serp_domains = []
            if types.get('search') or types.get('shopping') or types.get('display') or types.get('video'):
                job.step('serp', 'running', '正在打开 Google 搜索结果页')
                try:
                    search, shopping, meta = serp_mod.collect(page, kw, s, root, lambda m: job.step('serp', 'running', m), job.log,
                                                              pages=opts.get('serpPages', 5))
                    res['serpMeta'] = meta
                    res['search'] = search if types.get('search') else []
                    res['shopping'] = shopping if types.get('shopping') else []
                    msg = f'抓了 {meta["pagesDone"]} 页：搜索广告 {len(search)} 条，购物广告 {len(shopping)} 条'
                    for n in meta.get('pageNotes', []):
                        job.notice('翻页', n, 'info' if '没有更多页面' in n else 'warn')
                    if meta.get('location'):
                        msg += f'；Google 判断的位置：{meta["location"]}'
                    job.step('serp', 'done', msg, len(search) + len(shopping))
                    if not search and types.get('search'):
                        job.notice('搜索广告', f'前 {meta["pagesDone"]} 页搜索结果都没有出现搜索广告。可能这个词没有广告主竞价，或者广告按你的 IP 所在地投放（当前位置见上方）。可以换更有商业意图的词，或者把代理切到目标国家再试。')
                    if not shopping and types.get('shopping'):
                        job.notice('购物广告', '这次搜索结果页没有出现购物广告（Sponsored products / stores）。品牌词、服务类关键词通常没有购物广告；也可能当前地区没有投放。')
                    if meta.get('location') and opts['region'] == 'US' and not re.search(r'(?i)(, [A-Z]{2}\b|United States|USA)', meta['location']):
                        job.notice('地区', f'Google 判断你的位置是“{meta["location"]}”，和所选地区（{opts["region"]}）可能不一致。搜索/购物广告按访问者 IP 投放，请把代理切到目标国家。')
                    # 透明度中心的候选网站，按“搜索结果页上的先后”排：搜索广告（第 1 页顶部优先）→ 购物广告 → 自然结果
                    order = lambda a: (a.get('page', 1), 0 if a.get('position') == '顶部' else 1, a.get('rank', 99))
                    for a in sorted(search, key=order):
                        if a.get('domain'):
                            serp_domains.append((a['domain'], f'第 {a.get("page", 1)} 页{a.get("position", "")}第 {a.get("rank")} 条搜索广告（{a.get("advertiser") or a["domain"]}）'))
                    for p in shopping:
                        if p.get('domain'):
                            serp_domains.append((p['domain'], f'第 {p.get("page", 1)} 页购物广告（{p.get("merchant") or p["domain"]}）'))
                    for i, d in enumerate(meta.get('organic', [])):
                        serp_domains.append((d, f'第 1 页第 {i + 1} 条自然搜索结果'))
                except Cancelled:
                    raise
                except Exception as e:
                    job.step('serp', 'failed', str(e)[:300], reason=str(e)[:300])
                    job.notice('搜索/购物广告', f'抓取失败：{e}', 'error')
                    res['search'], res['shopping'] = [], []
            job.check()

            # ---- 透明度中心：只抓一个目标网站 ----
            # 透明度中心不能按关键词搜素材，所以用“搜索结果页上的第一个网站”（或用户手动指定的网站）作为目标。
            need_atc = types.get('display') or types.get('video')
            sources = []
            if need_atc:
                job.step('sources', 'running', '正在确定透明度中心的目标网站')
                atc = atc_mod.ATC(page, s, config.region_code(opts['region']), job.log)
                cands, seen = [], set()
                if opts.get('targetDomain'):
                    cands.append((opts['targetDomain'], '你在面板里手动指定'))
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
                        tried.append(f'{dom}（{why}）')
                        if opts.get('targetDomain') and dom == registrable(host_of(opts['targetDomain'])):
                            break  # 用户指定的网站查不到，就不自动换别的网站
                    res['sources'] = sources
                    res['atcTarget'] = sources[0] if sources else None
                    if sources:
                        t = sources[0]
                        job.step('sources', 'done', f'目标网站：{t["value"]}（来自{t["from"]}）；透明度中心里的广告主：{"、".join(t["advertisers"]) or "—"}', 1)
                        if tried:
                            job.notice('目标网站', '这些网站在透明度中心没有查到广告，已顺延：' + '；'.join(tried), 'info')
                    else:
                        why = ('你指定的网站在透明度中心没有查到该地区的广告：' + '；'.join(tried)) if opts.get('targetDomain') else \
                              ('搜索结果页上没有可以作为目标的网站' if not cands else '搜索结果页前几个网站在透明度中心都没有查到该地区的广告：' + '；'.join(tried))
                        job.step('sources', 'failed', why, 0, reason=why)
                        job.notice('展示/视频广告', why + '。可以在“指定网站”里填竞对的域名再试。')
                except Exception as e:
                    job.step('sources', 'failed', f'透明度中心无法访问：{e}', reason=str(e)[:200])
                    job.notice('展示/视频广告', f'Google 广告透明度中心无法访问：{e}', 'error')
            else:
                job.step('sources', 'skipped', '没有勾选展示/视频广告')
            job.check()

            # ---- 展示广告 ----
            if types.get('display') and sources:
                job.step('display', 'running', '正在获取展示广告列表（先查昨天，没有再查过去 7 天）')
                items, label, widened = atc.creatives_recent(sources, 2, opts['maxDisplay'])
                res.setdefault('windows', {})['display'] = label if items else None
                if items and widened:
                    job.notice('展示广告', f'昨天没有找到展示广告，已改为抓取过去 7 天：{label}', 'info')
                elif not items:
                    job.notice('展示广告', f'昨天和过去 7 天都没有找到这些广告主的展示广告（{label}）')
                done, rendered_fail = [], 0
                for i, it in enumerate(items):
                    job.check()
                    job.step('display', 'running', f'处理展示广告 {i + 1}/{len(items)}（{it["advertiser"]}）')
                    p = os.path.join(root, 'assets', 'display', f'{it["id"]}.jpg')
                    if atc.fetch_image(it, p):
                        it['image'] = f'assets/display/{it["id"]}.jpg'
                        it['captureMethod'] = '原始图片'
                    else:
                        try:
                            r = atc.render(it, p, want='image')
                            it['texts'], it['variants'] = r['texts'], r['variants']
                            if r['screenshot']:
                                it['image'] = f'assets/display/{it["id"]}.jpg'
                                it['captureMethod'] = '渲染截图'
                        except Exception as e:
                            it['error'] = f'渲染失败：{str(e).splitlines()[0][:120]}'
                    if not it.get('image'):
                        rendered_fail += 1
                        reason = '透明度中心没有显示这条广告的预览（Google 未提供可渲染的画面）'
                        if it.get('imageNote'):
                            reason = it['imageNote'] + '，' + reason
                        it.setdefault('error', reason)
                    done.append(it)
                res['display'] = done
                ok = sum(1 for x in done if x.get('image'))
                job.step('display', 'done' if ok else ('failed' if items else 'done'),
                         f'展示广告 {ok} 条' + (f'，{rendered_fail} 条没拿到画面' if rendered_fail else '') + (f'；时间段：{label}' if items else '；昨天和过去 7 天都没有'), ok)
            elif types.get('display'):
                job.step('display', 'skipped', '没有可追查的广告主')
            else:
                job.step('display', 'skipped', '未勾选')
            job.check()

            # ---- 视频广告 ----
            if types.get('video') and sources:
                job.step('video', 'running', '正在获取视频广告列表（先查昨天，没有再查过去 7 天）')
                items, vlabel, widened = atc.creatives_recent(sources, 3, opts['maxVideo'] * 2)
                res.setdefault('windows', {})['video'] = vlabel if items else None
                if items and widened:
                    job.notice('视频广告', f'昨天没有找到视频广告，已改为抓取过去 7 天：{vlabel}', 'info')
                elif not items:
                    job.notice('视频广告', f'昨天和过去 7 天都没有找到这些广告主的视频广告（{vlabel}）')
                done, seen_vid = [], set()
                for i, it in enumerate(items):
                    if len([x for x in done if x.get('youtube')]) >= opts['maxVideo']:
                        break
                    job.check()
                    job.step('video', 'running', f'处理视频广告 {i + 1}/{len(items)}（{it["advertiser"]}）')
                    p = os.path.join(root, 'assets', 'video', it['id'], 'preview.jpg')
                    try:
                        r = atc.render(it, p, want='video')
                    except Exception as e:
                        it['error'] = f'渲染失败：{str(e).splitlines()[0][:120]}'
                        done.append(it)
                        continue
                    it['texts'], it['variants'] = r['texts'], r['variants']
                    if r['screenshot']:
                        it['image'] = f'assets/video/{it["id"]}/preview.jpg'
                    vid = next((v for v in r['ytIds'] if v not in seen_vid), None)
                    if not vid:
                        it['error'] = f'这条“视频广告”在透明度中心只显示为其他版式（如地图/门店卡片），试了 {r.get("variantsTried") or 1} 个变体都没有 YouTube 视频'
                        done.append(it)
                        continue
                    seen_vid.add(vid)
                    job.step('video', 'running', f'播放并截图 YouTube 视频 {vid}（{it["advertiser"]}）')
                    it['youtube'] = youtube.capture(page, vid, s, os.path.join(root, 'assets', 'video', it['id']), f'assets/video/{it["id"]}')
                    done.append(it)
                res['video'] = done
                ok = sum(1 for x in done if x.get('youtube'))
                job.step('video', 'done' if ok or not items else 'failed',
                         f'视频广告 {ok} 条（含 YouTube 视频）' + (f'，另有 {len(done) - ok} 条没找到视频源' if len(done) > ok else '')
                         + (f'；时间段：{vlabel}' if items else '；昨天和过去 7 天都没有'), ok)
                if items and not ok:
                    job.notice('视频广告', '找到了视频广告，但都没有对应的 YouTube 视频（多是效果最大化广告生成的地图/门店版式）。')
            elif types.get('video'):
                job.step('video', 'skipped', '没有可追查的广告主')
            else:
                job.step('video', 'skipped', '未勾选')
        finally:
            try:
                ctx.close()
            except Exception:
                pass


FATAL = ('无效', '连不上', '没有登录', '额度', '没有找到 Claude Code', '无法运行 Claude Code')


def run_analysis(job, settings, force=False):
    from .analyze import AnalyzeError, Analyzer, ClaudeCodeAnalyzer

    opts, res, kw = job.data['options'], job.data['results'], job.data['keyword']
    engine = settings.get('engine') or 'claude_code'
    if not force and not opts.get('analyze'):
        job.step('analysis', 'skipped', '本次任务没有勾选“抓完自动 AI 分析”，可以稍后点“AI 分析”')
        return
    if engine == 'none':
        job.step('analysis', 'skipped', '设置里选择了“不做 AI 分析”')
        return
    if engine == 'api' and not settings.get('api_key'):
        job.step('analysis', 'skipped', '未配置 Claude API key，跳过 AI 分析')
        job.notice('AI 分析', '分析引擎选的是 Claude API，但没有填 API key。可以在“设置”里填 key，或者把分析引擎改成 Claude Code（用订阅，不需要 key），然后点“AI 分析”。')
        return
    try:
        an = ClaudeCodeAnalyzer(settings, job.dir) if engine == 'claude_code' else Analyzer(settings)
    except AnalyzeError as e:
        job.step('analysis', 'failed', str(e), reason=str(e))
        job.notice('AI 分析', str(e), 'error')
        return
    except Exception as e:
        job.step('analysis', 'failed', f'无法初始化分析引擎：{e}')
        return
    job.log(f'分析引擎：{"Claude Code（" + an.model + "）" if engine == "claude_code" else "Claude API（" + an.model + "）"}')
    errors = []

    def attempt(label, fn):
        job.check()
        job.step('analysis', 'running', f'正在分析：{label}')
        try:
            return fn()
        except AnalyzeError as e:
            errors.append(f'{label}：{e}')
            job.log(f'分析失败 {label}：{e}')
            if any(k in str(e) for k in FATAL):
                raise
        return None

    # 已经分析过的素材不再重复分析（再次点“AI 分析”时只补缺的，省额度）；整体总结每次都重新生成
    todo = lambda xs: [x for x in xs if not x.get('analysis')]
    try:
        search = todo(res.get('search', []))
        if search:
            r = attempt('搜索广告', lambda: an.search_ads(kw, search)) or {}
            for a in search:
                if a['id'] in r:
                    a['analysis'] = r[a['id']]
        shop = todo(res.get('shopping', []))
        if shop:
            r = attempt(f'购物广告（{len(shop)} 条）', lambda: an.shopping_ads(kw, shop, job.dir)) or {}
            for a in shop:
                if a['id'] in r:
                    a['analysis'] = r[a['id']]
        disp = todo([x for x in res.get('display', []) if x.get('image')])
        if disp:
            r = attempt(f'展示广告（{len(disp)} 条）', lambda: an.display_ads(kw, disp, job.dir)) or {}
            for a in disp:
                if a['id'] in r:
                    a['analysis'] = r[a['id']]
        for v in todo([x for x in res.get('video', []) if x.get('youtube') or x.get('image')]):
            out = attempt(f'视频 {(v.get("youtube") or {}).get("title") or v["id"]}', lambda v=v: an.video_ad(kw, v, job.dir))
            if out:
                v['analysis'] = out
        job.save()
        s = attempt('整体总结', lambda: an.summary(kw, job.data))
        if s:
            job.data['summary'] = s
    except AnalyzeError as e:
        job.step('analysis', 'failed', str(e), reason=str(e))
        job.notice('AI 分析', str(e), 'error')
        return
    job.data['usage'] = an.usage
    msg = f'完成 {an.usage["calls"]} 次调用（输入约 {an.usage["input"]:,} tokens，输出约 {an.usage["output"]:,} tokens）'
    if errors:
        job.step('analysis', 'done', msg + f'；{len(errors)} 项失败')
        for e in errors:
            job.notice('AI 分析', e)
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
        return upd('failed', '设置里的分析引擎是“不做 AI 分析”，请先选择 Claude Code 或 Claude API')
    if engine == 'api' and not settings.get('api_key'):
        return upd('failed', '分析引擎是 Claude API，但没有填 API key')
    upd('running', '正在生成我方文案（通常 1–3 分钟）')
    try:
        an = ClaudeCodeAnalyzer(settings, job.dir) if engine == 'claude_code' else Analyzer(settings)
        result = an.write_copy(job.data['keyword'], job.data, c['brief'])
    except AnalyzeError as e:
        return upd('failed', str(e))
    except Exception as e:
        job.log(traceback.format_exc()[-1500:])
        return upd('failed', f'生成失败：{e}')
    entry = {'createdAt': datetime.datetime.now().isoformat(timespec='seconds'), 'brief': c['brief'], 'result': result, 'usage': an.usage}
    c['history'] = ([entry] + c.get('history', []))[:10]  # 保留最近 10 次
    over = len(result.get('violations') or [])
    upd('done', f'已生成（{an.usage["calls"]} 次调用）' + (f'；仍有 {over} 条超出字符上限，已标红' if over else '；全部在字符上限内'))
