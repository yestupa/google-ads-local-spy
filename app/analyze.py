"""用 Claude 分析广告素材：每条素材的卖点角度、优点、可改进之处、改写示例；视频额外拆解逐镜头脚本；最后给出关键词整体洞察。

- 模型、思考深度在面板“设置”里选；API key 只保存在本机 data/settings.json。
- Opus 5.5 / Sonnet 5.5 默认开启服务端兜底（fallbacks="default"）：请求被安全分类器拒绝时，由 API 自动换模型重跑。
"""
import base64
import json
import os
import re
import subprocess
import urllib.request
from typing import List

from pydantic import BaseModel, Field

SYSTEM = ('你是资深的 Google Ads 投放与广告创意顾问，帮助跨境电商卖家学习竞对的广告素材。'
          '只根据提供的文字和图片下结论，不编造看不到的数据（点击率、转化率、花费等）。'
          '用简体中文回答，具体、可执行；改写示例保持广告原本的语言（通常是英文）。')

FALLBACK_MODELS = {'claude-opus-5-5', 'claude-sonnet-5-5'}


class Review(BaseModel):
    id: str = Field(description='素材编号，原样返回')
    angle: str = Field(description='核心卖点/诉求角度，一句话')
    strengths: List[str] = Field(description='优点，2-4 条，具体指出哪个词、哪个画面好')
    improvements: List[str] = Field(description='可改良的空间，2-4 条，每条给出具体改法')
    rewrite: str = Field(description='改良示例：搜索广告给新标题+描述，购物广告给新商品标题，展示广告给新的主文案')


class ReviewBatch(BaseModel):
    reviews: List[Review]


class DisplayReview(Review):
    textInImage: str = Field(description='图中可读到的文字（OCR），没有写空字符串')
    visual: str = Field(description='画面描述：主体、构图、配色、品牌露出')


class DisplayBatch(BaseModel):
    reviews: List[DisplayReview]


class Shot(BaseModel):
    time: str = Field(description='时间段，如 0–3s')
    visual: str
    audio: str = Field(description='口播/字幕/音乐；没有写空字符串')
    purpose: str = Field(description='这个镜头的作用')


class VideoReview(BaseModel):
    hook: str = Field(description='前 3-5 秒的钩子是什么，好不好')
    structure: str = Field(description='整体脚本结构，如 痛点→产品→证明→优惠→CTA')
    script: List[Shot]
    angle: str
    strengths: List[str]
    improvements: List[str]
    rewrite: str = Field(description='改良后的脚本思路（分镜级别，3-6 步）')


class Signal(BaseModel):
    title: str
    body: str


class KeywordSummary(BaseModel):
    overview: str = Field(description='这个关键词下的广告竞争概况，3-5 句')
    signals: List[Signal] = Field(description='关键发现 4-8 条，每条带具体证据（广告主、素材编号、价格等）')
    commonAngles: List[str] = Field(description='竞对最常用的卖点角度')
    offers: List[str] = Field(description='出现过的优惠/价格策略')
    opportunities: List[str] = Field(description='竞对没做好、我方可以切入的空白点')
    recommendations: List[str] = Field(description='给我方的 3-6 条具体素材/文案建议')


# ---- 我方文案（参考竞对素材生成可直接上架的广告文案） ----
class Sitelink(BaseModel):
    text: str = Field(description='附加链接文字，≤25 字符')
    desc1: str = Field(description='描述第 1 行，≤35 字符')
    desc2: str = Field(description='描述第 2 行，≤35 字符')


class SearchCopy(BaseModel):
    headlines: List[str] = Field(description='自适应搜索广告标题，15 个，每个 ≤30 字符，角度要多样（品牌、卖点、优惠、信任、CTA、关键词）')
    descriptions: List[str] = Field(description='描述 4 条，每条 ≤90 字符')
    path1: str = Field(description='显示路径 1，≤15 字符')
    path2: str = Field(description='显示路径 2，≤15 字符')
    sitelinks: List[Sitelink] = Field(description='附加链接 4 个')
    callouts: List[str] = Field(description='宣传信息 4-6 条，每条 ≤25 字符')


class ShoppingTitle(BaseModel):
    title: str = Field(description='优化后的商品标题，≤150 字符，最重要的词放在前 70 字符内')
    note: str = Field(description='为什么这样写（中文，一句话）')


class DisplayCopy(BaseModel):
    shortHeadlines: List[str] = Field(description='短标题 5 个，每个 ≤30 字符')
    longHeadline: str = Field(description='长标题 1 个，≤90 字符')
    descriptions: List[str] = Field(description='描述 5 条，每条 ≤90 字符')


class VideoScript(BaseModel):
    name: str = Field(description='脚本名称（中文），说明这个版本的思路')
    hook: str = Field(description='前 5 秒钩子')
    shots: List[Shot]
    cta: str = Field(description='结尾行动号召')


class MyCopy(BaseModel):
    positioning: str = Field(description='一句话定位：我方相对竞对的切入点（中文）')
    differentiation: List[str] = Field(description='3-5 条差异化要点，每条注明参考了哪些竞对素材（编号），中文')
    search: SearchCopy
    shopping: List[ShoppingTitle] = Field(description='购物广告标题；没有要求时给空列表')
    display: DisplayCopy
    video: List[VideoScript] = Field(description='视频脚本 2 套；没有要求时给空列表')
    notes: List[str] = Field(description='投放和合规提醒（中文），例如哪些说法需要证据支撑')


class CopyFix(BaseModel):
    fixes: List[str] = Field(description='按顺序给出改短后的文案，数量和顺序与要求完全一致')


# Google Ads 字符上限；中日韩等全角字符按 2 个字符计算
LIMITS = {'headline': 30, 'description': 90, 'path': 15, 'sitelink': 25, 'sitelinkDesc': 35, 'callout': 25,
          'shortHeadline': 30, 'longHeadline': 90, 'displayDesc': 90, 'shoppingTitle': 150}


def ad_len(s):
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in (s or ''))


def copy_fields(c):
    """把生成结果展开成 (路径, 文案, 上限) 列表，用于检查长度和回填。"""
    out = []
    s, d = c.get('search') or {}, c.get('display') or {}
    out += [(('search', 'headlines', i), t, LIMITS['headline']) for i, t in enumerate(s.get('headlines') or [])]
    out += [(('search', 'descriptions', i), t, LIMITS['description']) for i, t in enumerate(s.get('descriptions') or [])]
    out += [(('search', 'path1'), s.get('path1', ''), LIMITS['path']), (('search', 'path2'), s.get('path2', ''), LIMITS['path'])]
    for i, sl in enumerate(s.get('sitelinks') or []):
        out += [(('search', 'sitelinks', i, 'text'), sl.get('text', ''), LIMITS['sitelink']),
                (('search', 'sitelinks', i, 'desc1'), sl.get('desc1', ''), LIMITS['sitelinkDesc']),
                (('search', 'sitelinks', i, 'desc2'), sl.get('desc2', ''), LIMITS['sitelinkDesc'])]
    out += [(('search', 'callouts', i), t, LIMITS['callout']) for i, t in enumerate(s.get('callouts') or [])]
    out += [(('display', 'shortHeadlines', i), t, LIMITS['shortHeadline']) for i, t in enumerate(d.get('shortHeadlines') or [])]
    out += [(('display', 'longHeadline'), d.get('longHeadline', ''), LIMITS['longHeadline'])]
    out += [(('display', 'descriptions', i), t, LIMITS['displayDesc']) for i, t in enumerate(d.get('descriptions') or [])]
    out += [(('shopping', i, 'title'), x.get('title', ''), LIMITS['shoppingTitle']) for i, x in enumerate(c.get('shopping') or [])]
    return out


def set_path(obj, path, value):
    for k in path[:-1]:
        obj = obj[k]
    obj[path[-1]] = value


class AnalyzeError(RuntimeError):
    pass


def system_proxy():
    try:
        p = urllib.request.getproxies()
        return p.get('https') or p.get('http') or ''
    except Exception:
        return ''


class Analyzer:
    def __init__(self, settings):
        import anthropic
        self.anthropic = anthropic
        self.settings = settings
        proxy = settings.get('proxy') or system_proxy()
        kw = {'api_key': settings['api_key'], 'max_retries': 3}
        if proxy:
            kw['http_client'] = anthropic.DefaultHttpxClient(proxy=proxy)
        self.client = anthropic.Anthropic(**kw)
        self.model = settings.get('model') or 'claude-sonnet-5-5'
        self.effort = settings.get('effort') or 'medium'
        self.usage = {'input': 0, 'output': 0, 'calls': 0}

    def _call(self, content, schema, max_tokens=16000):
        a = self.anthropic
        content = [self._b64(c['path']) if c.get('type') == 'image_path' else c for c in content]
        kw = dict(model=self.model, max_tokens=max_tokens, system=SYSTEM, messages=[{'role': 'user', 'content': content}],
                  output_config={'effort': self.effort}, output_format=schema)
        if self.model in FALLBACK_MODELS:
            kw.update(fallbacks='default', betas=['server-side-fallback-2026-07-01'])
        try:
            resp = self.client.beta.messages.parse(**kw)
        except a.AuthenticationError as e:
            raise AnalyzeError('API key 无效，请在“设置”里检查') from e
        except a.PermissionDeniedError as e:
            raise AnalyzeError(f'API key 没有权限：{e.message}') from e
        except a.NotFoundError as e:
            raise AnalyzeError(f'模型不存在或不可用：{self.model}') from e
        except a.RateLimitError as e:
            raise AnalyzeError('API 调用太频繁或额度不足（429），请稍后再试') from e
        except a.BadRequestError as e:
            raise AnalyzeError(f'请求被拒绝（400）：{e.message[:200]}') from e
        except a.APIStatusError as e:
            raise AnalyzeError(f'Claude API 错误 {e.status_code}：{e.message[:200]}') from e
        except a.APIConnectionError as e:
            raise AnalyzeError('连不上 Claude API。请检查网络/代理（设置里可以填代理地址）') from e
        u = resp.usage
        self.usage['input'] += (u.input_tokens or 0) + (getattr(u, 'cache_read_input_tokens', 0) or 0)
        self.usage['output'] += u.output_tokens or 0
        self.usage['calls'] += 1
        if resp.stop_reason == 'refusal':
            raise AnalyzeError('模型拒绝分析这条内容')
        if resp.stop_reason == 'max_tokens':
            raise AnalyzeError('输出过长被截断')
        if resp.parsed_output is None:
            raise AnalyzeError('模型没有返回可解析的结果')
        return resp.parsed_output

    @staticmethod
    def img(path):
        """图片先记成本地路径；API 引擎发送前转成 base64，Claude Code 引擎让模型自己用 Read 工具读取。"""
        return {'type': 'image_path', 'path': path}

    @staticmethod
    def _b64(path):
        ext = os.path.splitext(path)[1].lower()
        media = {'.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif'}.get(ext, 'image/jpeg')
        with open(path, 'rb') as f:
            return {'type': 'image', 'source': {'type': 'base64', 'media_type': media, 'data': base64.standard_b64encode(f.read()).decode()}}

    # ---- 各类素材 ----
    def search_ads(self, keyword, ads):
        lite = [{k: a.get(k) for k in ('id', 'position', 'rank', 'advertiser', 'headline', 'displayUrl', 'description', 'sitelinks', 'promos', 'extras')} for a in ads]
        text = (f'关键词：{keyword}\n以下是 Google 搜索结果页上的搜索广告（JSON）。请逐条点评：卖点角度、优点、可改良的空间，并给出改写示例（新标题 + 新描述）。\n'
                + json.dumps(lite, ensure_ascii=False))
        return {r.id: r.model_dump() for r in self._call([{'type': 'text', 'text': text}], ReviewBatch).reviews}

    def shopping_ads(self, keyword, items, root, batch=8):
        out = {}
        for i in range(0, len(items), batch):
            content = []
            for it in items[i:i + batch]:
                meta = {k: it.get(k) for k in ('id', 'title', 'price', 'originalPrice', 'merchant', 'rating', 'promos', 'shipping', 'storeInfo')}
                content.append({'type': 'text', 'text': json.dumps(meta, ensure_ascii=False)})
                if it.get('image'):
                    content.append(self.img(os.path.join(root, it['image'])))
            content.append({'type': 'text', 'text': f'关键词：{keyword}。以上是 Google 购物广告（每条的 JSON 后面是商品主图）。请逐条点评商品图、标题、价格和促销信息：卖点角度、优点、可改良的空间，改写示例给出优化后的商品标题。每一条都要点评，id 原样返回。'})
            out.update({r.id: r.model_dump() for r in self._call(content, ReviewBatch).reviews})
        missing = [it for it in items if it['id'] not in out]
        if missing and len(missing) < len(items):  # 有条目被漏掉时补一次
            out.update(self.shopping_ads(keyword, missing, root, batch) if len(missing) <= batch else {})
        return out

    def display_ads(self, keyword, items, root, batch=4):
        out = {}
        for i in range(0, len(items), batch):
            content = []
            for it in items[i:i + batch]:
                meta = {k: it.get(k) for k in ('id', 'advertiser', 'firstShown', 'lastShown', 'daysShown')}
                meta['textsFromAd'] = it.get('texts', [])[:2]
                content.append({'type': 'text', 'text': json.dumps(meta, ensure_ascii=False)})
                content.append(self.img(os.path.join(root, it['image'])))
            content.append({'type': 'text', 'text': f'关键词：{keyword}。以上是竞对的 Google 展示广告（每条 JSON 后面是广告截图）。请逐条识别图中文字、描述画面，并点评：卖点角度、优点、可改良的空间，改写示例给出新的主文案。每一条都要点评，id 原样返回。'})
            out.update({r.id: r.model_dump() for r in self._call(content, DisplayBatch).reviews})
        missing = [it for it in items if it['id'] not in out]
        if missing and len(missing) < len(items):  # 有条目被漏掉时补一次
            out.update(self.display_ads(keyword, missing, root, batch) if len(missing) <= batch else {})
        return out

    def video_ad(self, keyword, item, root):
        yt = item.get('youtube') or {}
        meta = {'advertiser': item.get('advertiser'), 'title': yt.get('title'), 'channel': yt.get('author'), 'lengthSeconds': yt.get('lengthSeconds'),
                'views': yt.get('views'), 'description': (yt.get('description') or '')[:400], 'captions': yt.get('captions', [])[:80],
                'capturedSeconds': (yt.get('frames') or [{}])[-1].get('t'), 'textsFromAd': item.get('texts', [])[:2]}
        content = []
        if yt.get('storyboard'):
            content.append(self.img(os.path.join(root, yt['storyboard'])))
        elif item.get('image'):
            content.append(self.img(os.path.join(root, item['image'])))
        content.append({'type': 'text', 'text': f'关键词：{keyword}。这是竞对的 YouTube 视频广告。图片是播放过程中按时间截取的画面（左上角是秒数），captions 是播放时读到的字幕（口播）。'
                                                f'请拆解逐镜头脚本，评价钩子和结构，指出优点和可改良的空间，并给出改良后的脚本思路。\n' + json.dumps(meta, ensure_ascii=False)})
        return self._call(content, VideoReview).model_dump()

    def summary(self, keyword, job):
        r = job['results']

        def lite(a):  # 总结只需要每条点评的要点，控制输入长度
            if not a:
                return None
            return {'angle': a.get('angle'), 'strengths': (a.get('strengths') or [])[:2], 'improvements': (a.get('improvements') or [])[:2]}

        def pick(xs, keys):
            return [{k: (lite(x.get(k)) if k == 'analysis' else x.get(k)) for k in keys} for x in xs]
        payload = {
            'keyword': keyword, 'region': job['options'].get('region'),
            'search': pick(r.get('search', []), ('id', 'advertiser', 'headline', 'description', 'promos', 'analysis')),
            'shopping': pick(r.get('shopping', []), ('id', 'title', 'price', 'originalPrice', 'merchant', 'promos', 'analysis')),
            'display': pick(r.get('display', []), ('id', 'advertiser', 'daysShown', 'analysis')),
            'video': [{'id': v.get('id'), 'advertiser': v.get('advertiser'), 'title': (v.get('youtube') or {}).get('title'),
                       'views': (v.get('youtube') or {}).get('views'), 'analysis': lite(v.get('analysis'))} for v in r.get('video', [])],
            'advertisers': r.get('sources', []),
        }
        text = '下面是一个关键词下抓到的全部 Google 广告和逐条点评（JSON）。请总结竞争格局、常用卖点、优惠策略、空白机会，并给出我方的素材建议。\n' + json.dumps(payload, ensure_ascii=False)
        return self._call([{'type': 'text', 'text': text}], KeywordSummary).model_dump()

    def write_copy(self, keyword, job, brief):
        """参考本任务的竞对素材和点评，为我方生成广告文案；超出字符上限的自动让模型改短一次。"""
        r = job['results']
        comp = {
            'search': [{k: a.get(k) for k in ('id', 'advertiser', 'headline', 'description', 'sitelinks', 'promos')} |
                       {'angle': (a.get('analysis') or {}).get('angle'), 'weak': (a.get('analysis') or {}).get('improvements', [])[:2]}
                       for a in r.get('search', [])][:20],
            'shopping': [{k: p.get(k) for k in ('id', 'title', 'price', 'originalPrice', 'merchant', 'promos')} |
                         {'angle': (p.get('analysis') or {}).get('angle')} for p in r.get('shopping', [])][:30],
            'display': [{'id': d.get('id'), 'advertiser': d.get('advertiser'), 'text': (d.get('analysis') or {}).get('textInImage'),
                         'angle': (d.get('analysis') or {}).get('angle')} for d in r.get('display', []) if d.get('analysis')][:12],
            'video': [{'id': v.get('id'), 'title': (v.get('youtube') or {}).get('title'), 'hook': (v.get('analysis') or {}).get('hook'),
                       'structure': (v.get('analysis') or {}).get('structure')} for v in r.get('video', []) if v.get('analysis')][:6],
            'summary': {k: (job.get('summary') or {}).get(k) for k in ('overview', 'commonAngles', 'offers', 'opportunities')},
        }
        want = brief.get('types') or ['search', 'shopping', 'display', 'video']
        lang = brief.get('language') or 'English'
        text = (f'关键词：{keyword}\n我方信息（用户填写）：\n{json.dumps({k: v for k, v in brief.items() if k != "types"}, ensure_ascii=False)}\n\n'
                f'竞对广告素材和点评（JSON）：\n{json.dumps(comp, ensure_ascii=False)}\n\n'
                f'请为“我方”写 Google 广告文案，要用到的类型：{"、".join(want)}（没要求的类型给空列表或空字符串）。\n'
                f'要求：\n- 广告文案使用 {lang}；分析、理由和提醒用中文。\n'
                '- 吸收竞对做得好的写法，避开点评里指出的问题，突出我方与竞对不同的地方。\n'
                '- 严格遵守字符上限：标题 30、描述 90、显示路径 15、附加链接 25、附加链接描述 35、宣传信息 25、展示长标题 90、购物标题 150。\n'
                '- 只使用用户提供的事实（价格、优惠、认证、数据），不要编造用户没给出的数字、奖项或保证；需要证据的说法写进 notes 提醒。\n'
                '- 15 个标题角度要分散，避免互相重复，至少 3 个包含关键词，至少 2 个包含优惠或价格（用户提供了的话）。')
        result = self._call([{'type': 'text', 'text': text}], MyCopy).model_dump()
        over = [(p, t, lim) for p, t, lim in copy_fields(result) if ad_len(t) > lim]
        if over:  # 超长的改短一次
            ask = '\n'.join(f'{i + 1}. （上限 {lim} 字符，现在 {ad_len(t)}）{t}' for i, (p, t, lim) in enumerate(over))
            try:
                fx = self._call([{'type': 'text', 'text': f'下面这些 Google 广告文案超出了字符上限。请逐条改短到上限以内，保留原意和语言，不要加编号。\n{ask}'}], CopyFix)
                for (p, t, lim), new in zip(over, fx.fixes):
                    if new and ad_len(new) <= lim:
                        set_path(result, p, new.strip())
            except AnalyzeError:
                pass
        result['violations'] = [{'path': list(p), 'text': t, 'limit': lim, 'len': ad_len(t)} for p, t, lim in copy_fields(result) if ad_len(t) > lim]
        return result


# ---------------------------------------------------------------------------
# Claude Code 引擎：调用本机已登录的 Claude Code 命令行（claude -p），
# 使用你自己的 Claude 订阅（Pro / Max），不需要 API key。
# ---------------------------------------------------------------------------
CC_MODELS = {'sonnet': 'Sonnet（推荐）', 'opus': 'Opus（分析更细，更耗额度）', 'haiku': 'Haiku（最快、最省额度）'}


def find_claude_cli():
    import shutil
    p = shutil.which('claude')
    if p:
        return p
    home = os.path.expanduser('~')
    for c in (os.path.join(home, '.local', 'bin', 'claude.exe'), os.path.join(home, '.local', 'bin', 'claude'),
              os.path.join(os.environ.get('APPDATA', ''), 'npm', 'claude.cmd'), '/usr/local/bin/claude', '/opt/homebrew/bin/claude'):
        if c and os.path.exists(c):
            return c
    return None


def schema_for(model_cls):
    """Pydantic 模型 → 内联了 $ref 的 JSON Schema（放进命令行参数更稳）。"""
    sch = model_cls.model_json_schema()
    defs = sch.pop('$defs', {})

    def inline(o):
        if isinstance(o, dict):
            if '$ref' in o:
                return inline(dict(defs[o['$ref'].split('/')[-1]]))
            # 只去掉 Pydantic 自动加的 "title" 说明（值是字符串）；名为 title 的字段本身要保留
            out = {k: inline(v) for k, v in o.items() if not (k == 'title' and isinstance(v, str))}
            if out.get('type') == 'object':
                out['additionalProperties'] = False
                out.setdefault('required', list(out.get('properties', {}).keys()))
            return out
        if isinstance(o, list):
            return [inline(x) for x in o]
        return o
    return inline(sch)


def cli_env(settings=None):
    """给 claude 子进程用的环境变量：
    - 带上代理（Claude Code 命令行不会自动读取 Windows 系统代理，国内直连会 403）；
    - 去掉“我是某个 Claude Code 会话的子进程”这类变量，否则 claude 会等宿主提供登录而报“未登录”。"""
    env = {k: v for k, v in os.environ.items()
           if not (k == 'CLAUDECODE' or k.startswith(('CLAUDE_CODE_', 'CLAUDE_AGENT_SDK_')) or k in ('CLAUDE_PID', 'CLAUDE_EFFORT'))}
    proxy = (settings or {}).get('proxy') or system_proxy()
    if proxy and not (env.get('HTTPS_PROXY') or env.get('https_proxy')):
        env['HTTPS_PROXY'] = proxy
        env['HTTP_PROXY'] = proxy
    return env


def claude_cli_status(exe=None, timeout=60, settings=None):
    """用一个极短的请求检查 Claude Code 是否已登录。返回 (ok, 说明)。"""
    exe = exe or find_claude_cli()
    if not exe:
        return False, '没有安装 Claude Code（claude 命令）'
    try:
        p = subprocess.run([exe, '-p', '--output-format', 'json', '--model', 'haiku', '--max-turns', '1'], input='只回复 OK',
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, env=cli_env(settings))
        d = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
    except Exception as e:
        return False, f'无法运行 Claude Code：{str(e)[:100]}'
    if d.get('is_error') or not d:
        msg = str(d.get('result') or p.stderr.strip()[:200])
        if 'login' in msg.lower():
            return False, '已安装，但还没有登录：打开终端运行 claude，输入 /login 登录（国内网络需先给终端设置代理，见使用说明）'
        return False, f'已安装，但调用失败：{msg[:120]}'
    return True, '已安装并已登录'


class ClaudeCodeAnalyzer(Analyzer):
    def __init__(self, settings, workdir):
        self.settings = settings
        self.exe = find_claude_cli()
        if not self.exe:
            raise AnalyzeError('没有找到 Claude Code 命令行（claude）。请先安装 Claude Code（https://claude.com/claude-code），安装后在终端运行 claude 并登录')
        self.workdir = workdir
        self.model = settings.get('cc_model') or 'sonnet'
        self.usage = {'input': 0, 'output': 0, 'calls': 0, 'engine': 'Claude Code'}

    def _call(self, content, schema, max_tokens=None):
        """格式不符时自动重试一次；两次都失败时把原始输出存到任务目录 debug_claude_code.txt 方便排查。"""
        try:
            return self._call_once(content, schema)
        except AnalyzeError as e:
            if '格式' not in str(e):
                raise
            return self._call_once(content + [{'type': 'text', 'text': '注意：上一次的回答没有按要求输出。这次务必只给出符合 JSON Schema 的结果。'}], schema)

    def _call_once(self, content, schema):
        lines, n = [SYSTEM, ''], 0
        for c in content:
            if c.get('type') == 'image_path':
                n += 1
                rel = os.path.relpath(c['path'], self.workdir).replace(os.sep, '/')
                lines.append(f'[图片 {n}：请用 Read 工具查看文件 {rel}]')
            else:
                lines.append(c['text'])
        if n:
            lines.append('\n要求：先用 Read 工具逐一查看上面列出的全部图片文件，再作答。只读取列出的文件，不要修改任何文件。')
        lines.append('最终结果必须符合给定的 JSON Schema。')
        cmd = [self.exe, '-p', '--output-format', 'json', '--json-schema', json.dumps(schema_for(schema), ensure_ascii=False),
               '--allowedTools', 'Read', '--model', self.model]
        env = cli_env(self.settings)
        try:
            p = subprocess.run(cmd, input='\n'.join(lines), capture_output=True, text=True, encoding='utf-8', errors='replace',
                               cwd=self.workdir, timeout=900, env=env)
        except subprocess.TimeoutExpired as e:
            raise AnalyzeError('Claude Code 超过 15 分钟没有返回') from e
        except OSError as e:
            raise AnalyzeError(f'无法运行 Claude Code：{e}') from e
        try:
            d = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
        except ValueError:
            d = {}
        res_text = str(d.get('result') or '')
        if not d or d.get('is_error'):
            msg = res_text or p.stderr.strip()[:300] or f'退出码 {p.returncode}'
            low = msg.lower()
            if 'not logged in' in low or '/login' in low:
                raise AnalyzeError('Claude Code 还没有登录。请打开终端运行 claude，输入 /login 登录后，再点“AI 分析”')
            if 'limit' in low or 'usage' in low or 'quota' in low:
                raise AnalyzeError(f'Claude 订阅的使用额度达到上限：{msg[:200]}')
            raise AnalyzeError(f'Claude Code 返回错误：{msg[:300]}')
        u = d.get('usage') or {}
        self.usage['input'] += (u.get('input_tokens') or 0) + (u.get('cache_read_input_tokens') or 0) + (u.get('cache_creation_input_tokens') or 0)
        self.usage['output'] += u.get('output_tokens') or 0
        self.usage['calls'] += 1
        data = d.get('structured_output')
        if data is None:  # 兼容：没有结构化字段时，从文本里找 JSON
            m = re.search(r'\{.*\}', res_text, re.S)
            if m:
                try:
                    data = json.loads(m.group(0))
                except ValueError:
                    data = None
        try:
            if data is None:
                raise ValueError('没有结构化结果')
            return schema.model_validate(data)
        except Exception as e:
            try:
                with open(os.path.join(self.workdir, 'debug_claude_code.txt'), 'w', encoding='utf-8') as f:
                    f.write(f'{e}\n\n--- stdout ---\n{p.stdout[-20000:]}\n\n--- stderr ---\n{p.stderr[-5000:]}')
            except OSError:
                pass
            raise AnalyzeError(f'Claude Code 返回的结果格式不符（{str(e)[:120]}），原始输出已保存到任务文件夹 debug_claude_code.txt') from e
