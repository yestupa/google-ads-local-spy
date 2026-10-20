"""路径、设置读写、地区代码。设置保存在 data/settings.json（只存在本机，不要分享这个文件）。"""
import json
import os
import threading

from .i18n import L

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
JOBS = os.path.join(DATA, 'jobs')
PROFILE = os.path.join(DATA, 'browser_profile')
SETTINGS_PATH = os.path.join(DATA, 'settings.json')
WEB = os.path.join(ROOT, 'app', 'web')

# Google 广告透明度中心的地区代码（Google Ads 地理位置 ID）
REGIONS = {
    'US': ('美国', 'United States', 2840), 'GB': ('英国', 'United Kingdom', 2826), 'CA': ('加拿大', 'Canada', 2124),
    'AU': ('澳大利亚', 'Australia', 2036), 'DE': ('德国', 'Germany', 2276), 'FR': ('法国', 'France', 2250),
    'IT': ('意大利', 'Italy', 2380), 'ES': ('西班牙', 'Spain', 2724), 'NL': ('荷兰', 'Netherlands', 2528),
    'JP': ('日本', 'Japan', 2392), 'SG': ('新加坡', 'Singapore', 2702), 'AE': ('阿联酋', 'UAE', 2784),
    'BR': ('巴西', 'Brazil', 2076), 'MX': ('墨西哥', 'Mexico', 2484), 'IN': ('印度', 'India', 2356),
}


def models():
    return {
        'claude-opus-5-5': L('Claude Opus 5.5（分析最细，较贵）', 'Claude Opus 5.5 (most detailed, pricier)'),
        'claude-sonnet-5-5': L('Claude Sonnet 5.5（推荐，性价比高）', 'Claude Sonnet 5.5 (recommended, best value)'),
        'claude-haiku-5-5': L('Claude Haiku 5.5（最便宜，分析较浅）', 'Claude Haiku 5.5 (cheapest, lighter analysis)'),
    }


def engines():
    return {
        'claude_code': L('Claude Code（用你的 Claude 订阅，不需要 API key）', 'Claude Code (uses your Claude subscription, no API key)'),
        'api': L('Claude API（需要 API key，按量付费）', 'Claude API (needs an API key, pay as you go)'),
        'none': L('不做 AI 分析（只抓取）', 'No AI analysis (collect only)'),
    }


def cc_models():
    return {'sonnet': L('Sonnet（推荐）', 'Sonnet (recommended)'), 'opus': L('Opus（分析更细，更耗额度）', 'Opus (more detailed, uses more quota)'),
            'haiku': L('Haiku（最快、最省额度）', 'Haiku (fastest, least quota)')}

DEFAULTS = {
    'engine': 'claude_code',
    'cc_model': 'sonnet',
    'api_key': '',
    'model': 'claude-sonnet-5-5',
    'effort': 'medium',
    'region': 'US',
    'hl': 'en',
    'browser': 'auto',          # auto / chrome / msedge / chromium
    'proxy': '',                # 例如 http://127.0.0.1:7890 ；留空则使用系统代理
    'show_browser': False,      # True：抓取时显示浏览器窗口
    'manual_captcha': True,     # 遇到 Google 人机验证时，弹出窗口让你手动完成
    'serp_pages': 5,            # 搜索结果抓前几页（搜索广告有时只出现在后面几页）
    'max_advertisers': 5,
    'max_display': 12,
    'max_video': 6,
    'video_seconds': 30,        # 每条视频最多播放并截图多少秒
    'auto_analyze': True,
    'ui_lang': '',              # 界面语言：zh / en；留空时按浏览器语言自动选
}

_lock = threading.Lock()


def ensure_dirs():
    from .util import check_bundle
    check_bundle()
    for d in (DATA, JOBS, PROFILE):
        os.makedirs(d, exist_ok=True)


def load_settings():
    ensure_dirs()
    s = dict(DEFAULTS)
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, encoding='utf-8') as f:
                s.update(json.load(f))
        except ValueError:
            pass
    if not s.get('api_key') and os.environ.get('ANTHROPIC_API_KEY'):
        s['api_key'] = os.environ['ANTHROPIC_API_KEY']
        s['api_key_from_env'] = True
    return s


def save_settings(new):
    with _lock:
        cur = load_settings()
        cur.pop('api_key_from_env', None)
        for k, v in new.items():
            if k not in DEFAULTS:
                continue
            if k == 'api_key' and (v is None or str(v).startswith('••')):
                continue  # 面板上显示的是打码后的值，没改就不覆盖
            if isinstance(DEFAULTS[k], bool):
                v = bool(v)
            elif isinstance(DEFAULTS[k], int):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    continue
            cur[k] = v
        with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
    return load_settings()


def public_settings():
    s = load_settings()
    key = s.get('api_key') or ''
    s['api_key'] = ('••••••••' + key[-4:]) if key else ''
    s['has_api_key'] = bool(key)
    s['regions'] = {k: L(v[0], v[1]) for k, v in REGIONS.items()}
    s['models'] = models()
    s['engines'] = engines()
    s['cc_models'] = cc_models()
    return s


def region_code(region):
    return REGIONS.get(region, REGIONS['US'])[2]
