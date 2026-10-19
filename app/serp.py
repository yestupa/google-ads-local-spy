"""Google 搜索结果页：搜索广告（Sponsored results）+ 购物广告（Sponsored products / Sponsored stores）。"""
import os
import random
import re
import urllib.parse

from . import browser as br
from .util import host_of, save_data_url, download

EXTRACT_JS = r"""() => {
  const lines = e => (e.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
  const textAds = [...document.querySelectorAll('[data-text-ad]')].map(e => {
    const img = e.querySelector('img');
    return {
      slot: e.getAttribute('data-ta-slot'), pos: e.getAttribute('data-ta-slot-pos'),
      heading: (e.querySelector('[role=heading]') || {}).innerText || '',
      lines: lines(e),
      links: [...e.querySelectorAll('a')].map(a => (a.innerText || '').trim()).filter(Boolean),
      // 标题链接的 data-pcu 就是广告的最终落地页（不需要点击广告）
      finalUrl: (e.querySelector('a[data-pcu]') || {getAttribute: () => ''}).getAttribute('data-pcu') || '',
      hasSellerRating: !!e.querySelector('a[href*="/shopping/ratings/"]'),
      img: img ? (img.currentSrc || img.src || '') : '',
    };
  });
  const heads = [...document.querySelectorAll('span,div,h2,h3')].filter(e => e.childElementCount === 0 &&
      /^Sponsored (products|stores|product|store|deals)/i.test(e.textContent.trim()));
  const sections = [];
  const seen = new Set();
  for (const h of heads) {
    let c = h;
    for (let i = 0; i < 12 && c; i++) { c = c.parentElement; if (c && c.querySelectorAll('a[href*="aclk"]').length >= 2) break; }
    if (!c || seen.has(c)) continue;
    seen.add(c);
    const units = [...c.querySelectorAll('.pla-unit')];
    let items;
    if (units.length) {
      items = units.map(u => { const img = u.querySelector('img');
        return {kind: 'product', lines: lines(u), img: img ? (img.currentSrc || img.src || '') : '',
          domain: u.getAttribute('data-dtld') || '', merchantId: u.getAttribute('data-merchant-id') || '',
          offerId: u.getAttribute('data-offer-id') || '', offerDocId: u.getAttribute('data-offer-docid') || ''}; });
    } else {
      items = [...c.querySelectorAll('a[href*="aclk"]')].map(a => { const img = a.querySelector('img');
        const d = a.closest('[data-dtld]');
        const m = a.querySelector('[data-merchant-id]') || a.closest('[data-merchant-id]');
        const at = n => (m && m.getAttribute(n)) || a.getAttribute(n) || '';
        return {kind: 'link', lines: lines(a), img: img ? (img.currentSrc || img.src || '') : '', domain: d ? d.getAttribute('data-dtld') : '',
          merchantId: at('data-merchant-id'), offerId: at('data-offer-id')}; });
    }
    sections.push({label: h.textContent.trim(), items});
  }
  // 自然搜索结果（非广告）的网址，按页面顺序
  // 自然结果的链接现在是加密的 /goto 跳转，所以从结果里显示的网址（cite，如 “https://www.ties.com › ...”）取网站
  const organic = [];
  for (const h of document.querySelectorAll('h3')) {
    const a = h.closest('a');
    if (!a || a.closest('[data-text-ad], .pla-unit') || (a.getAttribute('href') || '').includes('aclk')) continue;
    let url = '';
    if (/^https?:/.test(a.getAttribute('href') || '') && !/(^|\.)google\./.test(new URL(a.href).hostname)) url = a.href;
    if (!url) {
      let box = a;
      for (let i = 0; i < 6 && box && !box.querySelector('cite'); i++) box = box.parentElement;
      const c = box && box.querySelector('cite');
      const m = c && c.innerText.match(/^(https?:\/\/)?([a-z0-9.-]+\.[a-z]{2,})/i);
      if (m) url = 'https://' + m[2];
    }
    if (url && !organic.includes(url)) organic.push(url);
    if (organic.length >= 10) break;
  }
  let loc = '';
  const ca = [...document.querySelectorAll('a,span,div')].find(e => e.childElementCount === 0 && /^(Choose area|Update location|Use precise location)$/.test(e.textContent.trim()));
  if (ca) { const p = ca.closest('div'); loc = p && p.parentElement ? p.parentElement.innerText.split('\n')[0] : ''; }
  return {textAds, sections, organic, loc, title: document.title};
}"""

RATING = re.compile(r'(?<![\d$£€.,])[1-5]\.\d\s?(?:★+|\(\s?[\d.,]+[KkMm]?\s?\))')
PRICE = re.compile(r'(?:[$£€¥₹]|US\$|A\$|C\$|R\$)\s?\d[\d,]*(?:\.\d{1,2})?|\d[\d,]*(?:\.\d{1,2})?\s?(?:€|zł|kr)')


def _clean(s):
    return s.replace('‎', '').replace('‏', '').strip(' ·|')


def parse_text_ad(a):
    lines = [_clean(l) for l in a['lines'] if _clean(l)]
    head = _clean(a['heading'])
    url = next((l for l in lines if re.match(r'https?://', l) or '›' in l), '')
    adv = ''
    if url and url in lines:
        i = lines.index(url)
        if i > 0 and lines[i - 1] != head:
            adv = lines[i - 1]
    NOISE = {'Read more', 'Rating', 'Learn more', 'More', 'Sponsored', 'Ad', 'Ads', 'Why this ad?', 'About this ad'}
    sitelinks = []
    for t in a['links']:
        t = _clean(t)
        if t and t != head and t not in NOISE and not t.startswith('http') and len(t) < 60 and t not in sitelinks and t != adv:
            sitelinks.append(t)
    rest = [l for l in lines if l not in (head, url, adv) and l not in NOISE and l not in sitelinks]
    rest = [l for l in rest if not re.fullmatch(r'[·\s]*', l)]

    def is_sitelink_row(l):  # 附加链接常被合并成一行 “A · B · C”
        parts = [_clean(x) for x in l.split('·') if _clean(x)]
        return len(parts) >= 2 and all(len(x) < 60 for x in parts) and (
            not sitelinks or sum(1 for x in parts if x in sitelinks) >= max(1, len(parts) - 1))

    rows = [l for l in rest if is_sitelink_row(l)]
    if not sitelinks and rows:
        sitelinks = [_clean(x) for x in rows[0].split('·') if _clean(x)]
    rest = [l for l in rest if l not in rows]
    # 卖家评分：“Rating for xxx.com: 4.9 - 409 reviews”
    seller = next((re.search(r'([1-5](?:\.\d)?)\s*[-–]\s*([\d,.]+[KkMm]?)\s+reviews?', l) for l in rest if l.lower().startswith('rating for')), None)
    rest = [l for l in rest if not l.lower().startswith('rating for')]
    desc = max(rest, key=len) if rest else ''
    extras = [l for l in rest if l != desc]
    promos = [l for l in extras if re.search(r'(?i)(\boff\b|sale|valid|code|save|\d+%|deal|coupon)', l)]
    shipping = [l for l in extras if l not in promos and re.search(r'(?i)(free|shipping|delivery|returns|ships)', l)]
    rating = next((m.group(0).strip() for l in extras for m in [RATING.search(l)] if m), '')
    if not rating and seller:
        rating = f'{seller.group(1)}（{seller.group(2)} 条评价）'
    final_url = (a.get('finalUrl') or '').strip()
    domain = host_of(url.split('›')[0].strip()) or host_of(final_url)
    return {
        'position': '顶部' if a['slot'] in ('0', '1', '2') else '底部', 'slot': a['slot'], 'rank': int(a['pos'] or 0),
        'headline': head, 'advertiser': adv, 'displayUrl': url, 'finalUrl': final_url, 'domain': domain,
        'description': desc, 'sitelinks': sitelinks[:8], 'promos': promos, 'shipping': shipping,
        'rating': rating or ('有卖家评分' if a.get('hasSellerRating') else ''),
        'extras': [l for l in extras if l not in promos and l not in shipping],
    }


def parse_product(it, store=None, store_info=''):
    lines = [_clean(l) for l in it['lines'] if _clean(l)]
    lines = [l for l in lines if l not in ('Sponsored',)]
    prices = [m.group(0) for l in lines for m in PRICE.finditer(l)]
    title = next((l for l in lines if not PRICE.search(l) and len(l) > 12), lines[0] if lines else '')
    price_idx = next((i for i, l in enumerate(lines) if PRICE.search(l)), None)
    merchant = store or ''
    if not merchant and price_idx is not None:
        for l in lines[price_idx + 1:]:
            if not PRICE.search(l) and not re.match(r'^[\d.(]', l) and not re.search(r'(?i)(delivery|shipping|returns|^by \d|free)', l):
                merchant = l
                break
    rating = next((m.group(0).strip() for l in lines for m in [RATING.search(l)] if m), '')
    if not rating:  # 星级是图片时，文字里只有评论数，例如 (81)
        cnt = next((l for l in lines if re.fullmatch(r'\(\s?[\d.,]+[KkMm]?\s?\)', l)), '')
        rating = f'{cnt.strip("() ")} 条评价' if cnt else ''
    extras = [l for l in lines if l not in (title, merchant) and not PRICE.fullmatch(l)]
    price, orig = (prices[0] if prices else ''), (prices[1] if len(prices) > 1 else '')
    discount = ''
    num = lambda x: float(re.sub(r'[^\d.]', '', x.replace(',', '')))
    try:
        p, o = num(price), num(orig)
        if o > p > 0:
            discount = f'{round((1 - p / o) * 100)}%'
        else:
            orig = ''  # 第二个价格不比售价高，通常是单价（如 $4.20/meal），不是原价
    except ValueError:
        orig = ''
    offer = it.get('offerId', '')
    m = re.match(r'shopify_[A-Z]{2}_(\d+)_(\d+)', offer)
    return {'title': title, 'price': price, 'originalPrice': orig, 'discount': discount,
            'merchant': merchant, 'domain': it.get('domain', ''), 'rating': rating,
            'promos': [l for l in extras if re.search(r'(?i)(\boff\b|sale|save|\d+%|buy \d|deal|coupon|code)', l)],
            'shipping': [l for l in extras if re.search(r'(?i)(delivery|shipping|returns|by \d+/\d+)', l)],
            'merchantId': it.get('merchantId', ''), 'offerId': offer, 'offerDocId': it.get('offerDocId', ''),
            'platform': 'Shopify' if m else '', 'productId': m.group(1) if m else '',
            'storeInfo': store_info, 'img': it.get('img', '')}


def _open(page, url, settings, set_status, log, pno):
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=45000)
    except Exception as e:
        raise RuntimeError(f'无法打开 Google 搜索第 {pno} 页（{str(e).splitlines()[0][:80]}）。请确认已开启代理/VPN，并能在本机浏览器打开 google.com。')
    page.wait_for_timeout(2500)
    if 'consent.google' in page.url:
        for label in ('Reject all', 'Alle ablehnen', 'Tout refuser', 'Rifiuta tutto', 'Rechazar todo'):
            btn = page.query_selector(f'button:has-text("{label}")')
            if btn:
                btn.click()
                page.wait_for_timeout(2500)
                break
    if br.is_google_captcha(page):
        log(f'Google 在第 {pno} 页要求人机验证')
        if not br.wait_for_human(page, settings, set_status):
            raise RuntimeError(f'Google 在第 {pno} 页要求人机验证（检测到异常流量），未能通过。可在“设置”里勾选“遇到验证时弹出窗口”，或稍后再试、换一个代理节点。')
        page.wait_for_timeout(2000)
    # 滚动到底部，加载底部广告
    for _ in range(4):
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(500)
    page.evaluate('window.scrollTo(0, 0)')
    return page.evaluate(EXTRACT_JS)


def _save_img(src, path, settings):
    try:
        if src.startswith('data:'):
            return save_data_url(src, path)
        return download(src, path, settings.get('proxy'))
    except Exception:
        return None


def collect(page, keyword, settings, job_dir, set_status, log, pages=5):
    """逐页抓取前 pages 页搜索结果。返回 (search_ads, shopping_ads, serp_meta)。
    第 1 页打不开或人机验证没通过时抛出 RuntimeError；后面的页失败只记录原因，保留已抓到的结果。"""
    gl = settings.get('region', 'US').lower()
    base = {'q': keyword, 'hl': settings.get('hl', 'en'), 'gl': gl, 'pws': '0'}
    url = 'https://www.google.com/search?' + urllib.parse.urlencode(base)
    search, shopping, seen_ads, seen_prod = [], [], set(), set()
    loc, sections, page_notes, pages_done, organic = '', [], [], 0, []
    shot = None
    for pno in range(1, max(1, pages) + 1):
        set_status(f'正在抓取 Google 搜索结果第 {pno}/{pages} 页')
        q = dict(base, start=str((pno - 1) * 10)) if pno > 1 else base
        purl = 'https://www.google.com/search?' + urllib.parse.urlencode(q)
        try:
            raw = _open(page, purl, settings, set_status, log, pno)
        except RuntimeError as e:
            if pno == 1:
                raise
            page_notes.append(str(e))
            break
        pages_done = pno
        if pno == 1:
            loc = raw.get('loc', '')
            organic = [host_of(u) for u in raw.get('organic', []) if host_of(u)]
            p = os.path.join(job_dir, 'assets', 'serp.jpg')
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                page.screenshot(path=p, full_page=True, type='jpeg', quality=70)
                shot = p
            except Exception:
                shot = None
        n_new = 0
        for a in raw['textAds']:
            ad = parse_text_ad(a)
            key = (ad['headline'].lower(), ad['domain'])
            if key in seen_ads:
                continue
            seen_ads.add(key)
            ad['id'] = f's{len(search) + 1}'
            ad['page'] = pno
            if a.get('img') and _save_img(a['img'], os.path.join(job_dir, 'assets', 'search', f'{ad["id"]}.jpg'), settings):
                ad['image'] = f'assets/search/{ad["id"]}.jpg'
            search.append(ad)
            n_new += 1
        for sec in raw['sections']:
            if sec['label'] not in sections:
                sections.append(sec['label'])
            store, store_info = None, ''
            for it in sec['items']:
                lines = [_clean(l) for l in it['lines'] if _clean(l)]
                if it['kind'] == 'link' and not any(PRICE.search(l) for l in lines):
                    store = lines[0] if lines else None  # Sponsored stores 的商家头：店名 + 评分/退货政策
                    store_info = ' · '.join(lines[1:])
                    continue
                prod = parse_product(it, store if it['kind'] == 'link' else None, store_info if it['kind'] == 'link' else '')
                key = (prod['title'].lower(), prod['merchant'].lower(), prod['price'])
                if not prod['title'] or key in seen_prod:
                    continue
                seen_prod.add(key)
                prod['id'] = f'p{len(shopping) + 1}'
                prod['section'] = sec['label']
                prod['page'] = pno
                img = prod.pop('img', '')
                if img and _save_img(img, os.path.join(job_dir, 'assets', 'shopping', f'{prod["id"]}.jpg'), settings):
                    prod['image'] = f'assets/shopping/{prod["id"]}.jpg'
                shopping.append(prod)
                n_new += 1
        log(f'第 {pno} 页：新增 {n_new} 条广告')
        # 没有下一页就停止
        if pno < pages and not page.query_selector('a#pnnext, a[aria-label="Next page"], a[aria-label="Next"]'):
            page_notes.append(f'搜索结果只有 {pno} 页，没有更多页面')
            break
        if pno < pages:
            page.wait_for_timeout(random.randint(2500, 5000))  # 翻页间隔，降低触发人机验证的概率
    meta = {'url': url, 'location': loc, 'screenshot': 'assets/serp.jpg' if shot else None,
            'shoppingSections': sections, 'pagesRequested': pages, 'pagesDone': pages_done, 'pageNotes': page_notes,
            'organic': organic}
    return search, shopping, meta
