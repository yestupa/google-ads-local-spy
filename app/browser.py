"""浏览器启动与人机验证处理。

Google 会拦截无头浏览器和带自动化标记的浏览器，所以这里：
- 优先调用本机安装的 Chrome，其次 Edge，最后用 Playwright 自带的 Chromium；
- 用有界面模式启动，但把窗口放到屏幕外（设置里勾选“显示浏览器窗口”则正常显示）；
- 使用固定的浏览器资料目录 data/browser_profile，保留 Cookie，减少触发验证；
- 遇到 Google 人机验证时，可把窗口挪回屏幕中间，等用户手动完成。
"""
import time

from .config import PROFILE
from .i18n import L

OFFSCREEN = '--window-position=-2400,-2400'


class BrowserError(RuntimeError):
    pass


def launch(pw, settings, log):
    from .util import check_bundle
    check_bundle()
    order = {'chrome': ['chrome'], 'msedge': ['msedge'], 'chromium': [None]}.get(settings.get('browser'), ['chrome', 'msedge', None])
    args = ['--disable-blink-features=AutomationControlled', '--autoplay-policy=no-user-gesture-required', '--mute-audio']
    if not settings.get('show_browser'):
        args.append(OFFSCREEN)
    kw = dict(headless=False, locale='en-US', viewport={'width': 1366, 'height': 900},
              ignore_default_args=['--enable-automation'], args=args)
    if settings.get('proxy'):
        kw['proxy'] = {'server': settings['proxy']}
    errors = []
    for ch in order:
        try:
            ctx = pw.chromium.launch_persistent_context(PROFILE, channel=ch, **kw) if ch else \
                pw.chromium.launch_persistent_context(PROFILE, **kw)
            log(L('浏览器：', 'Browser: ') + {'chrome': 'Google Chrome', 'msedge': 'Microsoft Edge', None: L('内置 Chromium', 'bundled Chromium')}[ch])
            return ctx, ch or 'chromium'
        except Exception as e:
            msg = str(e).split('\n')[0][:160]
            errors.append(f'{ch or "chromium"}: {msg}')
    raise BrowserError(L('无法启动浏览器。请安装 Google Chrome 或 Microsoft Edge，或者先关闭正在运行的抓取任务。详情：', 'Cannot start a browser. Install Google Chrome or Microsoft Edge, or stop the running job first. Details: ') + ' ; '.join(errors))


def bring_to_front(page):
    """把（屏幕外的）窗口挪回屏幕中间。"""
    try:
        cdp = page.context.new_cdp_session(page)
        win = cdp.send('Browser.getWindowForTarget')
        cdp.send('Browser.setWindowBounds', {'windowId': win['windowId'], 'bounds': {'left': 80, 'top': 60, 'width': 1300, 'height': 900, 'windowState': 'normal'}})
        page.bring_to_front()
    except Exception:
        pass


def send_offscreen(page):
    try:
        cdp = page.context.new_cdp_session(page)
        win = cdp.send('Browser.getWindowForTarget')
        cdp.send('Browser.setWindowBounds', {'windowId': win['windowId'], 'bounds': {'left': -2400, 'top': -2400}})
    except Exception:
        pass


def is_google_captcha(page):
    try:
        return '/sorry/' in page.url or bool(page.query_selector('form#captcha-form, iframe[src*="recaptcha"]'))
    except Exception:
        return False


def wait_for_human(page, settings, set_status, timeout=180):
    """弹出窗口让用户完成验证。返回 True 表示已通过。"""
    if not settings.get('manual_captcha'):
        return False
    bring_to_front(page)
    set_status(L('等待人工验证：请在弹出的浏览器窗口里完成 Google 人机验证（最多等 3 分钟）', 'Waiting for you: solve the Google CAPTCHA in the pop-up browser window (up to 3 minutes)'))
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(2)
        if not is_google_captcha(page):
            if not settings.get('show_browser'):
                send_offscreen(page)
            return True
    return False
