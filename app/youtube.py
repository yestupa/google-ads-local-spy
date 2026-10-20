"""YouTube 视频广告：读取视频信息，在浏览器里静音播放，定时截图并读取自动字幕（作为口播脚本）。

不下载视频文件。播放失败时退回到 YouTube 自带的 3 张关键帧缩略图。
"""
import os

from .util import download, grid
from .i18n import L

INFO_JS = """() => {
  const r = window.ytInitialPlayerResponse || {};
  const vd = r.videoDetails || {}; const ps = r.playabilityStatus || {};
  const caps = ((r.captions || {}).playerCaptionsTracklistRenderer || {}).captionTracks || [];
  return {status: ps.status || null, reason: ps.reason || (ps.errorScreen ? 'unplayable' : null), title: vd.title || null,
          author: vd.author || null, lengthSeconds: parseInt(vd.lengthSeconds || '0'), views: parseInt(vd.viewCount || '0'),
          description: (vd.shortDescription || '').slice(0, 600), hasCaptions: caps.length > 0,
          captionLangs: caps.map(c => c.languageCode + (c.kind === 'asr' ? '(自动)' : ''))};
}"""

STATE_JS = """() => {
  const v = document.querySelector('video'); const p = document.querySelector('#movie_player');
  const caps = [...document.querySelectorAll('.ytp-caption-segment')].map(e => e.textContent.trim()).filter(Boolean).join(' ');
  return {t: v ? v.currentTime : -1, paused: v ? v.paused : true, ended: v ? v.ended : false,
          ad: p ? p.classList.contains('ad-showing') : false, caps};
}"""


def _consent(page):
    for label in ('Reject all', 'Reject the use of cookies', 'Alle ablehnen', 'Tout refuser'):
        try:
            b = page.query_selector(f'button:has-text("{label}")')
            if b:
                b.click()
                page.wait_for_timeout(1500)
                return
        except Exception:
            pass


def capture(page, vid, settings, out_dir, rel_dir):
    res = {'videoId': vid, 'url': f'https://www.youtube.com/watch?v={vid}', 'frames': [], 'captions': [], 'notes': []}
    os.makedirs(out_dir, exist_ok=True)
    try:
        page.goto(res['url'], wait_until='domcontentloaded', timeout=45000)
        page.wait_for_timeout(4000)
        _consent(page)
        info = page.evaluate(INFO_JS)
        res.update({k: info.get(k) for k in ('title', 'author', 'lengthSeconds', 'views', 'description', 'hasCaptions', 'captionLangs')})
        if info.get('status') not in (None, 'OK'):
            res['notes'].append(L('YouTube 无法播放：', 'YouTube cannot play this video: ') + str(info.get("reason") or info.get("status")))
            raise RuntimeError('unplayable')
        # 打开字幕
        try:
            btn = page.query_selector('.ytp-subtitles-button')
            if btn and btn.get_attribute('aria-pressed') == 'false':
                btn.click()
        except Exception:
            pass
        # 隐藏播放器控件和加载动画，只保留画面和字幕
        page.add_style_tag(content='.ytp-chrome-bottom,.ytp-chrome-top,.ytp-gradient-bottom,.ytp-gradient-top,.ytp-spinner,'
                                   '.ytp-pause-overlay,.ytp-ce-element,.ytp-watermark,.ytp-paid-content-overlay,.ytp-bezel{display:none!important}')
        page.evaluate("() => { const v = document.querySelector('video'); if (v) { v.muted = true; v.currentTime = 0; v.play(); } }")
        limit = min(int(settings.get('video_seconds', 30)), max(5, res.get('lengthSeconds') or 30))
        step = 3 if limit > 20 else 2
        player = page.query_selector('#movie_player')
        last_shot, last_cap, waited_ad = -step, '', 0
        for _ in range(limit * 4 + 60):
            page.wait_for_timeout(500)
            st = page.evaluate(STATE_JS)
            if st['ad']:  # 前贴片广告：尝试跳过
                waited_ad += 1
                try:
                    sk = page.query_selector('.ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern')
                    if sk:
                        sk.click()
                except Exception:
                    pass
                if waited_ad > 80:
                    res['notes'].append(L('视频前有无法跳过的广告，已放弃播放', 'An unskippable pre-roll ad blocked playback'))
                    break
                continue
            if st['paused'] and not st['ended']:
                page.evaluate("() => { const v = document.querySelector('video'); if (v) v.play(); }")
            t = st['t']
            if st['caps'] and st['caps'] != last_cap:
                last_cap = st['caps']
                res['captions'].append({'t': round(t, 1), 'text': st['caps']})
            if t >= 0.4 and t - last_shot >= step and player:
                p = os.path.join(out_dir, f'f{len(res["frames"]):02d}.jpg')
                try:
                    player.screenshot(path=p, type='jpeg', quality=80)
                    res['frames'].append({'t': round(t, 1), 'path': p})
                    last_shot = t
                except Exception:
                    pass
            if st['ended'] or t >= limit:
                break
        page.evaluate("() => { const v = document.querySelector('video'); if (v) v.pause(); }")
        if res.get('lengthSeconds') and res['lengthSeconds'] > limit:
            res['notes'].append(L(f'视频时长 {res["lengthSeconds"]} 秒，只截取了前 {limit} 秒（可在设置里调整）', f'The video is {res["lengthSeconds"]}s long; only the first {limit}s were captured (change this in Settings)'))
        if not res['captions']:
            res['notes'].append(L('播放期间没有读到字幕（视频可能没有口播或没有自动字幕）', 'No captions during playback (the video may have no voice-over or no auto captions)'))
    except Exception as e:
        if str(e) != 'unplayable':
            res['notes'].append(L('播放截图失败：', 'Playback capture failed: ') + str(e).splitlines()[0][:120])
    # 合并字幕：YouTube 字幕是两行滚动显示，相邻两次读取会重叠。按词找重叠部分，只保留新出现的词。
    acc, script = [], []
    for c in res['captions']:
        words = c['text'].split()
        k = 0
        for n in range(min(len(acc), len(words)), 0, -1):
            if acc[-n:] == words[:n]:
                k = n
                break
        new = words[k:]
        if new:
            script.append({'t': c['t'], 'text': ' '.join(new)})
            acc += new
    res['captions'] = script
    res['transcript'] = ' '.join(acc)
    # 播放失败时用 YouTube 自带的 3 张关键帧
    if not res['frames']:
        for n in ('1', '2', '3'):
            p = os.path.join(out_dir, f'thumb{n}.jpg')
            try:
                download(f'https://i.ytimg.com/vi/{vid}/{n}.jpg', p, settings.get('proxy'), timeout=20)
                res['frames'].append({'t': None, 'path': p})
            except Exception:
                pass
        if res['frames']:
            res['notes'].append(L('使用 YouTube 自带的 3 张关键帧缩略图代替播放截图（分辨率较低）', 'Used YouTube’s 3 keyframe thumbnails instead of playback frames (lower resolution)'))
    try:
        download(f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg', os.path.join(out_dir, 'cover.jpg'), settings.get('proxy'), timeout=20)
        res['cover'] = f'{rel_dir}/cover.jpg'
    except Exception:
        pass
    if res['frames']:
        sb = grid([f['path'] for f in res['frames']], [f'{f["t"]}s' if f['t'] is not None else f'#{i + 1}' for i, f in enumerate(res['frames'])],
                  os.path.join(out_dir, 'storyboard.jpg'), per_row=4, cell_w=320)
        if sb:
            res['storyboard'] = f'{rel_dir}/storyboard.jpg'
    res['frames'] = [{'t': f['t'], 'path': f'{rel_dir}/{os.path.basename(f["path"])}'} for f in res['frames']]
    return res
