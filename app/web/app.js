/* Google 广告侦察面板：前端逻辑。导出的静态报告也用这份代码（window.STATIC_JOB 存在时为静态模式）。 */
const STATIC = window.STATIC_JOB || null;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const state = {settings: null, jobs: [], cur: null, job: null, tab: 'summary', timer: null};
const STEP_ICON = {pending: '○', running: '◐', done: '✓', failed: '✕', skipped: '–'};

/* ---------------- 界面语言：L('中文', 'English') ---------------- */
// 页面加载时就要定下语言（下面的常量会用到）：静态报告用导出时的语言；面板先用上次记住的语言，没有就看浏览器语言，
// 设置读回来以后如果不一致，再以设置为准刷新一次。
const store = {get: k => { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} }};
const browserLang = () => /^zh/i.test(navigator.language || '') ? 'zh' : 'en';
let LANG = STATIC ? (window.STATIC_LANG || STATIC.lang || 'zh') : (store.get('adspy_lang') || browserLang());
const L = (zh, en) => LANG === 'en' ? en : zh;
function applyLang() {
  document.documentElement.lang = L('zh-CN', 'en');
  document.title = L('Google 广告侦察面板', 'Google Ad Spy Panel');
  if (LANG === 'en') {
    document.querySelectorAll('[data-en]').forEach(el => { el.innerHTML = el.dataset.en; });
    document.querySelectorAll('[data-en-ph]').forEach(el => { el.placeholder = el.dataset.enPh; });
  }
  $('#btnLang').textContent = L('EN', '中文');
}
async function setLang(lang) {
  store.set('adspy_lang', lang);
  await api('/api/settings', {method: 'POST', body: {ui_lang: lang}});
  location.reload();
}
applyLang();

const STATUS_NAME = {queued: L('排队中', 'Queued'), running: L('抓取中', 'Running'), done: L('完成', 'Done'), failed: L('失败', 'Failed'), cancelled: L('已取消', 'Cancelled')};
const STEP_NAME = {browser: L('启动浏览器', 'Start browser'), serp: L('搜索结果页（搜索广告 + 购物广告）', 'Search results (search + shopping ads)'),
  sources: L('在透明度中心查找广告主', 'Find advertiser in Transparency Center'), display: L('展示广告', 'Display ads'),
  video: L('视频广告', 'Video ads'), analysis: L('AI 分析', 'AI analysis')};
const POS = p => ({'顶部': L('顶部', 'top'), '底部': L('底部', 'bottom')}[p] || p || '');
const pageNo = n => n ? L(`第${n}页 `, `p${n} `) : '';

async function api(path, opts = {}) {
  const r = await fetch(path, {method: opts.method || 'GET', headers: {'Content-Type': 'application/json'},
    body: opts.body ? JSON.stringify(opts.body) : undefined});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
const asset = p => !p ? '' : (STATIC ? p : `/files/${encodeURIComponent(state.job.id)}/${p}`);

/* ---------------- 侧栏 ---------------- */
async function loadSettings() {
  state.settings = await api('/api/settings');
  const s = state.settings;
  $('#region').innerHTML = Object.entries(s.regions).map(([k, v]) => `<option value="${k}" ${k === s.region ? 'selected' : ''}>${esc(v)}${L('（', ' (')}${k}${L('）', ')')}</option>`).join('');
  $('#serpPages').value = s.serp_pages; $('#maxDisp').value = s.max_display; $('#maxVid').value = s.max_video;
  $('#optAnalyze').checked = !!s.auto_analyze;
}
async function loadJobs() {
  state.jobs = await api('/api/jobs');
  $('#jobs').innerHTML = state.jobs.length ? state.jobs.map(j => {
    const c = j.counts || {};
    const n = (c.search || 0) + (c.shopping || 0) + (c.display || 0) + (c.video || 0);
    return `<button class="jobitem ${j.id === state.cur ? 'on' : ''}" data-id="${esc(j.id)}">
      <b>${esc(j.keyword)}</b><small>${esc(j.createdAt.replace('T', ' ').slice(5, 16))} · ${esc(j.region)} · ${L(`${n} 条素材`, `${n} ads`)}</small>
      <span class="chip ${j.status}" style="float:right">${STATUS_NAME[j.status] || j.status}</span></button>`;
  }).join('') : `<div class="muted">${L('还没有任务', 'No jobs yet')}</div>`;
  document.querySelectorAll('.jobitem').forEach(b => b.onclick = () => openJob(b.dataset.id));
}
$('#searchForm').onsubmit = async e => {
  e.preventDefault();
  const keyword = $('#kw').value.trim();
  if (!keyword) { $('#kw').focus(); return; }
  const options = {region: $('#region').value, analyze: $('#optAnalyze').checked,
    types: {search: $('#tSearch').checked, shopping: $('#tShopping').checked, display: $('#tDisplay').checked, video: $('#tVideo').checked},
    serpPages: +$('#serpPages').value, targetDomain: $('#targetDomain').value.trim(), maxDisplay: +$('#maxDisp').value, maxVideo: +$('#maxVid').value};
  try {
    const job = await api('/api/jobs', {method: 'POST', body: {keyword, options}});
    state.tab = 'summary';
    await loadJobs(); openJob(job.id);
  } catch (err) { alert(err.message); }
};

/* ---------------- 任务视图 ---------------- */
async function openJob(id) {
  state.cur = id;
  clearTimeout(state.timer);
  try { state.job = await api('/api/jobs/' + encodeURIComponent(id)); } catch (e) { $('#main').innerHTML = `<div class="card empty">${esc(e.message)}</div>`; return; }
  renderJob();
  loadJobs();
  // 运行中定时刷新；正在看视频时不刷新，免得播放器被重新加载
  const busy = ['queued', 'running'].includes(state.job.status) || ['queued', 'running'].includes(state.job.copy?.status);
  if (busy) state.timer = setTimeout(() => state.tab === 'video' ? openJobLater(id) : openJob(id), 2500);
}

function openJobLater(id) {
  clearTimeout(state.timer);
  state.timer = setTimeout(() => state.tab === 'video' ? openJobLater(id) : openJob(id), 2500);
}

function analysisBlock(a, kind) {
  if (!a) return `<div class="noan">${L('暂无 AI 分析', 'No AI analysis yet')}</div>`;
  const li = xs => (xs || []).map(x => `<li>${esc(x)}</li>`).join('');
  return `<div class="an">
    ${a.angle ? `<div><span class="muted">${L('卖点角度：', 'Angle: ')}</span><span class="angle">${esc(a.angle)}</span></div>` : ''}
    <div class="box pros"><b>${L('优点', 'Strengths')}</b><ul>${li(a.strengths)}</ul></div>
    <div class="box cons"><b>${L('可改良的空间', 'What to improve')}</b><ul>${li(a.improvements)}</ul></div>
    ${a.rewrite ? `<div class="box rew"><b>${kind === 'video' ? L('改良脚本思路', 'Improved script idea') : L('改写示例', 'Rewrite example')}</b>${esc(a.rewrite)}</div>` : ''}
  </div>`;
}

/* ---------------- 表格视图：列出能收集到的全部字段 ---------------- */
const SEARCH_COLS = [
  [L('位置', 'Position'), a => `${pageNo(a.page)}${POS(a.position)} #${a.rank || ''}`],
  [L('广告主', 'Advertiser'), a => a.advertiser], [L('域名', 'Domain'), a => a.domain], ['Display URL', a => a.displayUrl],
  ['Final URL', a => a.finalUrl, 'url'], [L('标题', 'Headline'), a => a.headline], [L('描述', 'Description'), a => a.description],
  [L('附加链接', 'Sitelinks'), a => (a.sitelinks || []).join(' | ')], [L('促销', 'Promotions'), a => (a.promos || []).join(' | ')],
  [L('运费/退货', 'Shipping/returns'), a => (a.shipping || []).join(' | ')], [L('评分', 'Rating'), a => a.rating],
  [L('其他附加信息', 'Other assets'), a => (a.extras || []).join(' | ')],
];
const SHOP_COLS = [
  [L('位置', 'Position'), p => `${pageNo(p.page)}${p.section || ''}`], [L('商品标题', 'Product title'), p => p.title],
  [L('价格', 'Price'), p => p.price], [L('原价', 'Original price'), p => p.originalPrice], [L('折扣', 'Discount'), p => p.discount],
  [L('商家', 'Merchant'), p => p.merchant], [L('域名', 'Domain'), p => p.domain],
  [L('评分', 'Rating'), p => p.rating], [L('促销', 'Promotions'), p => (p.promos || []).join(' | ')], [L('运费/退货', 'Shipping/returns'), p => (p.shipping || []).join(' | ')],
  [L('店铺信息', 'Store info'), p => p.storeInfo], [L('平台/商品ID', 'Platform/product ID'), p => [p.platform, p.productId].filter(Boolean).join(' ')],
  ['Merchant ID', p => p.merchantId], ['Offer ID', p => p.offerId], ['Final URL', () => ''],
];
const NA_NOTE = L('“—”表示页面上没有这个信息。购物广告和附加链接的 Final URL 只有点击广告后才能拿到；点击会产生真实的广告扣费和无效点击，所以工具不会去点。',
  '“—” means the page does not show this field. Final URLs of Shopping ads and sitelinks are only available by clicking the ad; clicks cost the advertiser real money and count as invalid clicks, so the tool never clicks.');
function dataTable(list, cols) {
  return `<div class="card pad"><div class="muted" style="margin-bottom:8px">${esc(NA_NOTE)}</div><div class="tablewrap"><table class="list data"><thead><tr>${
    cols.map(c => `<th>${esc(c[0])}</th>`).join('')}</tr></thead><tbody>${list.map(x => `<tr>${cols.map(c => {
      const v = c[1](x);
      if (!v) return '<td class="na">—</td>';
      return c[2] === 'url' ? `<td><a href="${esc(v)}" target="_blank" rel="noopener">${esc(v)}</a></td>` : `<td>${esc(v)}</td>`;
    }).join('')}</tr>`).join('')}</tbody></table></div></div>`;
}
function viewSwitch(kind) {
  const v = state.view?.[kind] || 'table';
  return `<div class="viewbar"><button class="tab ${v === 'table' ? 'on' : ''}" data-view="${kind}:table">${L('表格（全部字段）', 'Table (all fields)')}</button>
    <button class="tab ${v === 'cards' ? 'on' : ''}" data-view="${kind}:cards">${L('卡片 + AI 点评', 'Cards + AI review')}</button>
    ${STATIC ? '' : `<button class="btn" data-act="csv">${L('导出 CSV', 'Export CSV')}</button>`}</div>`;
}
const emptyMsg = what => `<div class="card empty">${L(`没有抓到${what[0]}（原因见上方提示）`, `No ${what[1]} collected (see the reasons above)`)}</div>`;

function renderSearch(list) {
  if (!list.length) return emptyMsg(['搜索广告', 'search ads']);
  if ((state.view?.search || 'table') === 'table') return viewSwitch('search') + dataTable(list, SEARCH_COLS);
  return viewSwitch('search') + '<div class="grid">' + list.map(a => `<div class="card ad">
    <div class="top"><span class="chip">${a.page ? L(`第 ${a.page} 页 · `, `Page ${a.page} · `) : ''}${L(`${esc(a.position)} 第 ${a.rank} 位`, `${esc(POS(a.position))} #${a.rank}`)}</span><span class="muted">${esc(a.advertiser || a.domain)}</span></div>
    <div class="url">${esc(a.displayUrl)}</div>
    ${a.finalUrl ? `<div class="muted">${L('落地页：', 'Landing page: ')}<a href="${esc(a.finalUrl)}" target="_blank" rel="noopener">${esc(a.finalUrl)}</a></div>` : ''}
    <h4>${esc(a.headline)}</h4>
    <div class="desc">${esc(a.description)}</div>
    ${a.sitelinks?.length ? `<div class="sitelinks">${a.sitelinks.map(s => `<span>${esc(s)}</span>`).join('')}</div>` : ''}
    ${(a.promos || []).map(p => `<div class="promo">${esc(p)}</div>`).join('')}
    ${a.image ? `<img class="thumb" style="max-width:140px" src="${asset(a.image)}" alt="">` : ''}
    ${analysisBlock(a.analysis)}</div>`).join('') + '</div>';
}

function renderShopping(list) {
  if (!list.length) return emptyMsg(['购物广告', 'Shopping ads']);
  if ((state.view?.shopping || 'table') === 'table') return viewSwitch('shopping') + dataTable(list, SHOP_COLS);
  return viewSwitch('shopping') + '<div class="grid">' + list.map(p => `<div class="card ad">
    <div class="prod">${p.image ? `<img src="${asset(p.image)}" alt="">` : '<div></div>'}
      <div><div class="muted">${esc(p.section || '')}</div><h4 style="color:var(--ink);font-size:14.5px">${esc(p.title)}</h4>
      <div class="price">${esc(p.price)}${p.originalPrice ? `<s>${esc(p.originalPrice)}</s>` : ''}</div>
      <div class="muted">${esc(p.merchant)}${p.domain ? ' · ' + esc(p.domain) : ''}${p.rating ? ' · ' + esc(p.rating) : ''}</div>
      ${p.storeInfo ? `<div class="muted">${esc(p.storeInfo)}</div>` : ''}
      ${(p.promos || []).map(x => `<div class="promo">${esc(x)}</div>`).join('')}
      ${(p.shipping || []).length ? `<div class="muted">${esc(p.shipping.join(' · '))}</div>` : ''}</div></div>
    ${analysisBlock(p.analysis)}</div>`).join('') + '</div>';
}

function windowBar(kind) {
  const w = state.job?.results?.windows?.[kind];
  const t = state.job?.results?.atcTarget;
  return (t || w) ? `<div class="viewbar">${t ? `<span class="chip acc">${L('目标网站：', 'Target website: ')}${esc(t.value)}</span><span class="muted">${L('来自', 'from ')}${esc(t.from)}</span>` : ''}
    ${w ? `<span class="chip acc">${L('时间段：', 'Period: ')}${esc(w)}</span>` : ''}</div>
    <div class="muted" style="margin:-4px 0 12px">${L('透明度中心不能按关键词搜素材，所以按搜索结果页上的第一个网站（或你指定的网站）去抓；只抓这个时间段内在 Google 展示过的广告，昨天没有时自动放宽到过去 7 天。',
      'The Transparency Center cannot be searched by keyword, so ads are collected for the first website on the results page (or the website you entered). Only ads shown by Google in this period are included; if there are none yesterday, the last 7 days are used.')}</div>` : '';
}

function renderDisplay(list) {
  if (!list.length) return emptyMsg(['展示广告', 'display ads']);
  return windowBar('display') + '<div class="grid">' + list.map(d => `<div class="card ad">
    <div class="top"><b>${esc(d.advertiser)}</b><a href="${esc(d.url)}" target="_blank" rel="noopener">${L('透明度中心', 'Transparency Center')}</a></div>
    <div class="muted">${L('来源：', 'Source: ')}${esc(d.source)}</div>
    <div class="muted">${L('首次', 'First')} ${esc(d.firstShown)} · ${L('最近', 'last')} ${esc(d.lastShown)} · ${L(`展示 ${esc(d.daysShown ?? '—')} 天`, `${esc(d.daysShown ?? '—')} days shown`)}${d.variants ? ' · ' + L(`${d.variants} 个变体`, `${d.variants} variants`) : ''}${d.captureMethod ? ' · ' + esc(d.captureMethod) : ''}</div>
    ${d.image ? `<img class="thumb" src="${asset(d.image)}" alt="" loading="lazy">` : `<div class="err">${esc(d.error || L('没有画面', 'No image'))}</div>`}
    ${d.analysis?.textInImage ? `<div class="muted"><b>${L('图中文字：', 'Text in image: ')}</b>${esc(d.analysis.textInImage)}</div>` : ''}
    ${d.analysis?.visual ? `<div class="muted"><b>${L('画面：', 'Visual: ')}</b>${esc(d.analysis.visual)}</div>` : ''}
    ${!d.analysis && d.texts?.length ? `<div class="muted">${esc(d.texts[0].slice(0, 200))}</div>` : ''}
    ${analysisBlock(d.analysis)}</div>`).join('') + '</div>';
}

const shotHead = () => `<thead><tr><th>${L('时间', 'Time')}</th><th>${L('画面', 'Visual')}</th><th>${L('口播/字幕', 'Voice-over/captions')}</th><th>${L('作用', 'Purpose')}</th></tr></thead>`;

function renderVideo(list) {
  if (!list.length) return emptyMsg(['视频广告', 'video ads']);
  return windowBar('video') + list.map(v => {
    const y = v.youtube || {};
    const an = v.analysis;
    const script = an?.script?.length ? `<div class="tablewrap"><table class="shots">${shotHead()}<tbody>${
      an.script.map(s => `<tr><td>${esc(s.time)}</td><td>${esc(s.visual)}</td><td>${esc(s.audio || '—')}</td><td>${esc(s.purpose)}</td></tr>`).join('')}</tbody></table></div>` : '';
    // 本地面板里直接嵌入 YouTube 播放器；导出报告和分享页不能嵌入播放器，只显示分镜图和链接
    const canPlay = !STATIC && y.videoId;
    const caps = y.captions?.length ? `<div class="caps">${y.captions.map(c => canPlay && c.t != null
      ? `<button class="capline" data-seek="${esc(v.id)}:${Math.max(0, Math.floor(c.t))}" title="${L('从这里播放', 'Play from here')}"><i>${c.t}s</i>${esc(c.text)}</button>`
      : `<div><i>${c.t ?? ''}s</i>${esc(c.text)}</div>`).join('')}</div>` : '';
    const sbImg = y.storyboard ? `<img class="thumb" src="${asset(y.storyboard)}" alt="${L('视频截图分镜', 'Video storyboard')}">` : (v.image ? `<img class="thumb" src="${asset(v.image)}" alt="">` : '');
    const media = canPlay
      ? `<div class="player"><iframe id="yt-${esc(v.id)}" src="https://www.youtube-nocookie.com/embed/${esc(y.videoId)}?rel=0&modestbranding=1&playsinline=1"
           title="${esc(y.title || L('YouTube 视频', 'YouTube video'))}" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen></iframe></div>
         ${sbImg ? `<details style="margin-top:8px"><summary>${L('截图分镜', 'Storyboard')}</summary>${sbImg}</details>` : ''}`
      : sbImg + (y.url ? `<a class="btn" style="display:inline-block;margin-top:8px;text-decoration:none" href="${esc(y.url)}" target="_blank" rel="noopener">▶ ${L('在 YouTube 播放', 'Play on YouTube')}</a>` : '');
    return `<div class="card video">
      <div>
        ${media}
        ${(y.notes || []).map(n => `<div class="muted" style="margin-top:6px">ⓘ ${esc(n)}</div>`).join('')}
        ${v.error ? `<div class="err" style="margin-top:6px">${esc(v.error)}</div>` : ''}
        ${caps ? `<div style="margin-top:10px"><b style="font-size:12.5px">${L('播放时读到的字幕', 'Captions read during playback')}${canPlay ? L('（点一行从那里开始播放）', ' (click a line to play from there)') : ''}</b>${caps}</div>` : ''}
      </div>
      <div style="display:flex;flex-direction:column;gap:8px">
        <div class="top" style="display:flex;justify-content:space-between;gap:8px"><b style="font-size:15px">${esc(y.title || L('（未找到 YouTube 视频）', '(no YouTube video found)'))}</b>
          <span>${y.url ? `<a href="${esc(y.url)}" target="_blank" rel="noopener">YouTube</a> · ` : ''}<a href="${esc(v.url)}" target="_blank" rel="noopener">${L('透明度中心', 'Transparency Center')}</a></span></div>
        <div class="muted">${esc(v.advertiser)}${L('（来源：', ' (source: ')}${esc(v.source)}${L('）', ')')}${y.author ? L(' · 频道 ', ' · channel ') + esc(y.author) : ''}${y.lengthSeconds ? ' · ' + L(`${y.lengthSeconds} 秒`, `${y.lengthSeconds}s`) : ''}${y.views ? L(' · 播放 ', ' · views ') + Number(y.views).toLocaleString() : ''} · ${L('最近展示', 'last shown')} ${esc(v.lastShown)}</div>
        ${an ? `<div><span class="muted">${L('钩子：', 'Hook: ')}</span>${esc(an.hook)}</div><div><span class="muted">${L('结构：', 'Structure: ')}</span>${esc(an.structure)}</div>${script}` : ''}
        ${analysisBlock(an, 'video')}
      </div></div>`;
  }).join('');
}

function renderSummary(job) {
  const r = job.results || {};
  const s = job.summary;
  const prices = (r.shopping || []).map(p => parseFloat(String(p.price).replace(/[^\d.]/g, ''))).filter(x => !isNaN(x)).sort((a, b) => a - b);
  const advs = new Set([...(r.search || []).map(a => a.advertiser || a.domain), ...(r.shopping || []).map(p => p.merchant)].filter(Boolean));
  const stat = `<div class="stat">
    <div class="sec-search"><b>${(r.search || []).length}</b><span>${L('搜索广告', 'Search ads')}</span></div><div class="sec-shopping"><b>${(r.shopping || []).length}</b><span>${L('购物广告', 'Shopping ads')}</span></div>
    <div class="sec-display"><b>${(r.display || []).filter(x => x.image).length}</b><span>${L('展示广告', 'Display ads')}</span></div><div class="sec-video"><b>${(r.video || []).filter(x => x.youtube).length}</b><span>${L('视频广告', 'Video ads')}</span></div>
    <div class="sec-sources"><b>${advs.size}</b><span>${L('搜索结果页广告商家', 'Advertisers on the results pages')}${prices.length ? `<br>${L('价格', 'Prices')} ${prices[0]}–${prices[prices.length - 1]}` : ''}</span></div></div>`;
  const shot = r.serpMeta?.screenshot;
  if (!s) {
    return stat + `<div class="card empty">${L('还没有 AI 总结。', 'No AI summary yet. ')}${job.steps?.analysis?.msg ? esc(job.steps.analysis.msg) : ''}</div>` +
      (shot ? `<div class="card pad" style="margin-top:14px"><b>${L('搜索结果页截图', 'Results page screenshot')}</b><img class="thumb" style="margin-top:8px" src="${asset(shot)}" alt=""></div>` : '');
  }
  const li = xs => (xs || []).map(x => `<li>${esc(x)}</li>`).join('');
  return stat + `<div class="card pad" style="margin-bottom:14px"><h3 style="margin:0 0 6px">${L('竞争概况', 'Overview')}</h3><div>${esc(s.overview)}</div></div>
    <div class="summary">
      <div class="card"><h3>${L('关键发现', 'Key findings')}</h3>${(s.signals || []).map(x => `<div class="signal"><b>${esc(x.title)}</b>${esc(x.body)}</div>`).join('')}</div>
      <div style="display:flex;flex-direction:column;gap:14px">
        <div class="card"><h3>${L('给你的素材建议', 'Creative recommendations')}</h3><ul>${li(s.recommendations)}</ul></div>
        <div class="card"><h3>${L('空白机会', 'Gaps and opportunities')}</h3><ul>${li(s.opportunities)}</ul></div>
        <div class="card"><h3>${L('常用卖点', 'Common angles')}</h3><ul>${li(s.commonAngles)}</ul></div>
        <div class="card"><h3>${L('优惠与价格策略', 'Offers and pricing')}</h3><ul>${li(s.offers)}</ul></div>
      </div></div>
    ${shot ? `<div class="card pad" style="margin-top:14px"><details><summary style="cursor:pointer;font-weight:700">${L('搜索结果页截图', 'Results page screenshot')}</summary><img class="thumb" style="margin-top:8px" src="${asset(shot)}" alt=""></details></div>` : ''}`;
}

/* ---------------- 我方文案 ---------------- */
// Google Ads 计字符：中日韩等全角字符算 2
const adLen = s => [...(s || '')].reduce((n, c) => n + (/[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]/.test(c) ? 2 : 1), 0);
const lenTag = (s, lim) => { const n = adLen(s); return `<span class="len ${n > lim ? 'over' : ''}">${n}/${lim}</span>`; };
const COPY_TYPES = [['search', L('搜索广告', 'Search ads')], ['shopping', L('购物广告标题', 'Shopping titles')], ['display', L('展示广告', 'Display ads')], ['video', L('视频脚本', 'Video scripts')]];
const LANGS = ['English', 'Español', 'Deutsch', 'Français', 'Italiano', 'Português', '日本語', '中文'];

function copyForm(brief) {
  const b = brief || {};
  const types = b.types || ['search', 'shopping', 'display', 'video'];
  const f = (id, label, ph, area) => `<div class="field"><label for="cb_${id}">${label}</label>${area
    ? `<textarea id="cb_${id}" rows="3" placeholder="${esc(ph)}">${esc(b[id] || '')}</textarea>`
    : `<input type="text" id="cb_${id}" placeholder="${esc(ph)}" value="${esc(b[id] || '')}">`}</div>`;
  return `<div class="card pad copyform">
    <h3 style="margin:0 0 4px">${L('填写我方信息，AI 参考上面抓到的竞对素材写文案', 'Describe your brand; AI writes your copy using the competitor ads above')}</h3>
    <div class="hint">${L('只会使用你填写的事实（价格、优惠、认证、数据），不会编造。带 * 的必填。', 'Only the facts you enter (prices, offers, certifications, numbers) are used; nothing is invented. * = required.')}</div>
    <div class="two">${f('brand', L('品牌名 *', 'Brand *'), L('例如 PawFresh', 'e.g. PawFresh'))}${f('website', L('网站', 'Website'), L('例如 pawfresh.com', 'e.g. pawfresh.com'))}</div>
    ${f('product', L('产品和核心卖点 *', 'Product and key selling points *'), L('例如 人用级新鲜狗粮，兽医营养师配方，按狗狗体重定制份量，冷链配送', 'e.g. human-grade fresh dog food, vet-nutritionist recipes, portions by weight, delivered frozen'), true)}
    <div class="two">${f('offer', L('价格和优惠', 'Price and offer'), L('例如 首单 60% off，满 $49 包邮，$3.99/餐起', 'e.g. 60% off first box, free shipping over $49, from $3.99/meal'))}${f('audience', L('目标人群', 'Audience'), L('例如 关心狗狗健康、愿意为品质买单的城市养宠人', 'e.g. urban dog owners who care about health and pay for quality'))}</div>
    ${f('proof', L('可以用的证明（认证、评分、数据、媒体报道）', 'Proof you can use (certifications, ratings, numbers, press)'), L('例如 Trustpilot 4.8 分（2,300 条评价），AAFCO 标准', 'e.g. Trustpilot 4.8 (2,300 reviews), meets AAFCO standards'), true)}
    <div class="two"><div class="field"><label for="cb_language">${L('广告语言', 'Ad language')}</label><select id="cb_language">${LANGS.map(l => `<option ${l === (b.language || 'English') ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
      <div class="field"><label>${L('要生成的类型', 'What to write')}</label><div class="checks">${COPY_TYPES.map(([k, t]) => `<label><input type="checkbox" class="cb_type" value="${k}" ${types.includes(k) ? 'checked' : ''}>${t}</label>`).join('')}</div></div></div>
    <button class="btn primary" data-act="copywrite">${L('生成我方文案', 'Write my ad copy')}</button>
  </div>`;
}

function copyResult(entry) {
  const r = entry.result || {}, s = r.search || {}, d = r.display || {};
  const li = xs => (xs || []).map(x => `<li>${esc(x)}</li>`).join('');
  const lines = (xs, lim) => (xs || []).map(t => `<div class="cline"><span>${esc(t)}</span>${lenTag(t, lim)}</div>`).join('');
  const block = (title, body, copyText) => body ? `<div class="card pad cblock"><div class="ctitle"><b>${title}</b>${copyText != null && !STATIC
    ? `<button class="btn" data-copy="${esc(copyText)}">${L('复制', 'Copy')}</button>` : ''}</div>${body}</div>` : '';
  const shots = v => `<div class="tablewrap"><table class="shots">${shotHead()}<tbody>${
    (v.shots || []).map(x => `<tr><td>${esc(x.time)}</td><td>${esc(x.visual)}</td><td>${esc(x.audio || '—')}</td><td>${esc(x.purpose)}</td></tr>`).join('')}</tbody></table></div>`;
  const over = (r.violations || []).length;
  return `<div class="card pad" style="margin-bottom:14px"><div class="muted">${esc(entry.createdAt.replace('T', ' '))} · ${L('品牌', 'Brand')} ${esc(entry.brief.brand)} · ${L('语言', 'Language')} ${esc(entry.brief.language || 'English')}
      ${over ? ` · <span class="err">${L(`${over} 条超出字符上限（已标红）`, `${over} over the character limit (in red)`)}</span>` : ' · ' + L('全部在 Google 字符上限内', 'all within Google’s character limits')}</div>
    <h3 style="margin:8px 0 4px">${L('定位', 'Positioning')}</h3><div>${esc(r.positioning)}</div>
    <h3 style="margin:12px 0 4px">${L('差异化要点', 'How you stand out')}</h3><ul style="margin:0;padding-left:18px">${li(r.differentiation)}</ul>
    ${!STATIC && (s.headlines || []).length ? `<div class="viewbar" style="margin:12px 0 0"><button class="btn" data-act="copycsv">${L('导出搜索广告（Google Ads Editor 格式 CSV）', 'Export search ads (Google Ads Editor CSV)')}</button></div>` : ''}</div>
  <div class="grid">
    ${block(L(`搜索广告 · 标题（${(s.headlines || []).length}/15）`, `Search ads · headlines (${(s.headlines || []).length}/15)`), lines(s.headlines, 30), (s.headlines || []).join('\n'))}
    ${block(L('搜索广告 · 描述', 'Search ads · descriptions'), lines(s.descriptions, 90), (s.descriptions || []).join('\n'))}
    ${block(L('显示路径', 'Display path'), s.path1 || s.path2 ? `<div class="cline"><span>/${esc(s.path1)}/${esc(s.path2)}</span>${lenTag(s.path1, 15)} ${lenTag(s.path2, 15)}</div>` : '', `${s.path1}\n${s.path2}`)}
    ${block(L('附加链接', 'Sitelinks'), (s.sitelinks || []).map(x => `<div class="cline"><span><b>${esc(x.text)}</b><br><small>${esc(x.desc1)}<br>${esc(x.desc2)}</small></span><span>${lenTag(x.text, 25)}<br>${lenTag(x.desc1, 35)}<br>${lenTag(x.desc2, 35)}</span></div>`).join(''),
      (s.sitelinks || []).map(x => [x.text, x.desc1, x.desc2].join('\t')).join('\n'))}
    ${block(L('宣传信息', 'Callouts'), lines(s.callouts, 25), (s.callouts || []).join('\n'))}
    ${block(L('购物广告标题', 'Shopping titles'), (r.shopping || []).map(x => `<div class="cline"><span>${esc(x.title)}<br><small class="muted">${esc(x.note)}</small></span>${lenTag(x.title, 150)}</div>`).join(''), (r.shopping || []).map(x => x.title).join('\n'))}
    ${block(L('展示广告 · 短标题', 'Display ads · short headlines'), lines(d.shortHeadlines, 30), (d.shortHeadlines || []).join('\n'))}
    ${block(L('展示广告 · 长标题和描述', 'Display ads · long headline and descriptions'), d.longHeadline ? `<div class="cline"><span><b>${esc(d.longHeadline)}</b></span>${lenTag(d.longHeadline, 90)}</div>` + lines(d.descriptions, 90) : '',
      [d.longHeadline, ...(d.descriptions || [])].filter(Boolean).join('\n'))}
  </div>
  ${(r.video || []).map(v => `<div class="card pad" style="margin-top:14px"><b>${L('视频脚本：', 'Video script: ')}${esc(v.name)}</b><div class="muted" style="margin:6px 0">${L('钩子：', 'Hook: ')}${esc(v.hook)} · ${L('结尾 CTA：', 'Closing CTA: ')}${esc(v.cta)}</div>${shots(v)}</div>`).join('')}
  ${(r.notes || []).length ? `<div class="notices" style="margin-top:14px"><b>${L('投放和合规提醒', 'Launch and compliance notes')}</b><ul>${li(r.notes)}</ul></div>` : ''}`;
}

function renderMyCopy(job) {
  const c = job.copy || {};
  const hist = c.history || [];
  if (STATIC) return hist.length ? copyResult(hist[0]) : `<div class="card empty">${L('这份报告没有生成我方文案', 'This report has no generated copy')}</div>`;
  const pick = Math.min(state.copyIdx || 0, Math.max(0, hist.length - 1));
  const status = c.status ? `<div class="notices" style="background:${c.status === 'failed' ? 'var(--coral-soft)' : 'var(--acc-soft)'};border-color:var(--line)">
      <b style="color:${c.status === 'failed' ? 'var(--coral)' : 'var(--acc)'}">${{queued: L('排队中', 'Queued'), running: L('生成中', 'Writing'), done: L('已完成', 'Done'), failed: L('失败', 'Failed')}[c.status] || c.status}</b>${L('：', ': ')}${esc(c.msg || '')}</div>` : '';
  return copyForm(c.brief || state.lastBrief) + status +
    (hist.length > 1 ? `<div class="viewbar" style="margin-top:14px"><span class="muted">${L('历史版本：', 'Earlier versions: ')}</span>${hist.map((h, i) =>
      `<button class="tab ${i === pick ? 'on' : ''}" data-copyidx="${i}">${esc(h.createdAt.slice(5, 16).replace('T', ' '))}</button>`).join('')}</div>` : '') +
    (hist.length ? `<div style="margin-top:14px">${copyResult(hist[pick])}</div>` : '');
}

function renderSources(list) {
  if (!list?.length) return `<div class="card empty">${L('没有在透明度中心找到目标网站的广告（原因见上方提示）', 'No ads found for the target website in the Transparency Center (see the reasons above)')}</div>`;
  return `<div class="card pad tablewrap"><div class="muted" style="margin-bottom:8px">${L('透明度中心不能按关键词搜素材，展示广告和视频广告都来自下面这个网站。', 'The Transparency Center cannot be searched by keyword; display and video ads all come from the website below.')}</div><table class="list"><thead><tr><th>${L('为什么选它', 'Why this one')}</th><th>${L('网站', 'Website')}</th><th>${L('透明度中心里的广告主名称', 'Advertiser name in the Transparency Center')}</th></tr></thead><tbody>${
    list.map(s => `<tr><td>${esc(s.from)}</td><td><b>${esc(s.label)}</b></td><td>${esc((s.advertisers || []).join(L('、', ', ')) || '—')}</td></tr>`).join('')}</tbody></table></div>`;
}

/* 每个板块的标题条：图标 + 名称 + 一句说明（颜色由 .sec-* 决定） */
const ICON = {
  summary: '<path d="M4 19V10M10 19V5M16 19v-7M22 19H2"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
  shopping: '<path d="M6 7h12l-1 13H7L6 7Z"/><path d="M9 7a3 3 0 0 1 6 0"/>',
  display: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-9 9"/>',
  video: '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="m10 9 5 3-5 3V9Z"/>',
  sources: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  mycopy: '<path d="M4 20h4L19 9l-4-4L4 16v4Z"/><path d="m14 6 4 4"/>',
  log: '<path d="M5 6h14M5 12h14M5 18h9"/>',
};
const SEC = {
  summary: [L('总结', 'Summary'), L('竞争格局、关键发现和给你的素材建议', 'Competitive landscape, key findings and creative recommendations')],
  search: [L('搜索广告', 'Search ads'), L('Google 搜索结果页上的文字广告，按页码和位置排列', 'Text ads on Google results pages, by page and position')],
  shopping: [L('购物广告', 'Shopping ads'), L('Sponsored products / stores：商品图、价格、商家和促销', 'Sponsored products / stores: images, prices, merchants and promotions')],
  display: [L('展示广告', 'Display ads'), L('来自 Google 广告透明度中心的图片和响应式广告', 'Image and responsive ads from the Google Ads Transparency Center')],
  video: [L('视频广告', 'Video ads'), L('YouTube 视频广告：播放、截图分镜和口播脚本', 'YouTube video ads: player, storyboard and voice-over script')],
  sources: [L('目标网站', 'Target website'), L('透明度中心按哪个网站抓取了展示和视频广告', 'Which website the display and video ads were collected for')],
  mycopy: [L('我方文案', 'My copy'), L('参考竞对素材，为你的品牌生成可直接上架的广告文案', 'Ready-to-use ad copy for your brand, based on competitor ads')],
  log: [L('运行日志', 'Log'), L('抓取和分析过程的详细记录', 'Detailed record of collecting and analysis')],
};
function secHead(k) {
  const [t, d] = SEC[k] || ['', ''];
  return `<div class="sechead"><div class="sico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON[k] || ''}</svg></div>
    <div><h2>${t}</h2><p>${d}</p></div></div>`;
}

function renderJob() {
  const job = state.job;
  const r = job.results || {};
  const steps = job.steps || {};
  const running = ['queued', 'running'].includes(job.status);
  const tabs = [['summary', SEC.summary[0]], ['search', `${SEC.search[0]} (${(r.search || []).length})`], ['shopping', `${SEC.shopping[0]} (${(r.shopping || []).length})`],
    ['display', `${SEC.display[0]} (${(r.display || []).length})`], ['video', `${SEC.video[0]} (${(r.video || []).length})`], ['sources', SEC.sources[0]],
    ['mycopy', SEC.mycopy[0]]];
  if (!STATIC) tabs.push(['log', SEC.log[0]]);
  const notices = job.notices || [];
  let body = '';
  if (state.tab === 'search') body = renderSearch(r.search || []);
  else if (state.tab === 'shopping') body = renderShopping(r.shopping || []);
  else if (state.tab === 'display') body = renderDisplay(r.display || []);
  else if (state.tab === 'video') body = renderVideo(r.video || []);
  else if (state.tab === 'sources') body = renderSources(r.sources);
  else if (state.tab === 'mycopy') body = renderMyCopy(job);
  else if (state.tab === 'log') body = `<div class="log">${esc((job.log || []).join('\n'))}</div>`;
  else body = renderSummary(job);
  $('#main').innerHTML = `<div class="card head">
      <div><h1>${esc(job.keyword)}</h1>
        <div class="meta">${esc(job.createdAt.replace('T', ' '))} · ${L('地区', 'Region')} ${esc(job.options.region)} · <span class="chip ${job.status}">${STATUS_NAME[job.status] || job.status}</span>
        ${running && job.current ? ' · ' + esc(job.current) : ''}${job.usage ? ' · ' + L(`AI 调用 ${job.usage.calls} 次`, `${job.usage.calls} AI calls`) : ''}</div></div>
      <div class="btns">
        ${running ? `<button class="btn danger" data-act="cancel">${L('取消', 'Cancel')}</button>` : `<button class="btn primary" data-act="analyze">${L('AI 分析', 'AI analysis')}</button><button class="btn" data-act="rerun">${L('重新抓取', 'Run again')}</button>`}
        <button class="btn" data-act="export" ${running ? 'disabled' : ''}>${L('导出报告', 'Export report')}</button><button class="btn" data-act="open">${L('打开文件夹', 'Open folder')}</button>
        ${running ? '' : `<button class="btn danger" data-act="delete">${L('删除', 'Delete')}</button>`}
      </div>
      <div class="steps" style="width:100%">${Object.entries(steps).map(([k, s]) => `<div class="step ${s.status}" data-step="${k}"><b><span class="ico">${STEP_ICON[s.status] || ''}</span>${esc(STEP_NAME[k] || s.label)}</b><div class="msg">${esc(s.msg || '')}</div></div>`).join('')}</div>
      ${notices.length ? `<div class="notices" style="width:100%"><b>${L('抓不到的内容和原因', 'What could not be collected, and why')}</b><ul>${notices.map(n => `<li class="${n.level}"><b style="color:inherit">${esc(n.scope)}${L('：', ': ')}</b>${esc(n.message)}</li>`).join('')}</ul></div>` : ''}
    </div>
    <div class="tabs">${tabs.map(([k, t]) => `<button class="tab ${state.tab === k ? 'on' : ''}" data-tab="${k}">${esc(t)}</button>`).join('')}</div>
    <div class="sec sec-${state.tab}">${secHead(state.tab)}${body}</div>`;
  document.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => { state.tab = b.dataset.tab; renderJob(); });
  document.querySelectorAll('[data-copyidx]').forEach(b => b.onclick = () => { state.copyIdx = +b.dataset.copyidx; renderJob(); });
  document.querySelectorAll('[data-copy]').forEach(b => b.onclick = async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = L('已复制', 'Copied'); }
    catch (e) { const t = document.createElement('textarea'); t.value = b.dataset.copy; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove(); b.textContent = L('已复制', 'Copied'); }
    setTimeout(() => { b.textContent = L('复制', 'Copy'); }, 1500);
  });
  document.querySelectorAll('[data-seek]').forEach(b => b.onclick = () => {
    const [vid, t] = b.dataset.seek.split(':');
    const f = document.getElementById('yt-' + vid);
    if (f) f.src = f.src.replace(/&start=\d+|&autoplay=1/g, '') + `&start=${t}&autoplay=1`;
  });
  document.querySelectorAll('[data-view]').forEach(b => b.onclick = () => {
    const [k, v] = b.dataset.view.split(':'); state.view = Object.assign({}, state.view, {[k]: v}); renderJob();
  });
  document.querySelectorAll('[data-act]').forEach(b => b.onclick = () => jobAction(b.dataset.act));
}

function engineReady() {
  const s = state.settings || {};
  if (s.engine === 'none') { alert(L('设置里的分析引擎是“不做 AI 分析”，请先选择 Claude Code 或 Claude API', 'AI analysis is turned off in Settings. Choose Claude Code or Claude API first.')); openSettings(); return false; }
  if (s.engine === 'api' && !s.has_api_key) { alert(L('分析引擎是 Claude API，请先填写 API key；或者改用 Claude Code（用订阅，不需要 key）', 'The engine is Claude API: add an API key first, or switch to Claude Code (uses your subscription, no key).')); openSettings(); return false; }
  return true;
}

async function jobAction(act) {
  const id = state.job.id;
  if (act === 'delete' && !confirm(L('删除这个任务和它下载的所有素材？', 'Delete this job and all the files it downloaded?'))) return;
  if (act === 'copywrite') {
    const v = id => (document.getElementById('cb_' + id)?.value || '').trim();
    const body = {brand: v('brand'), website: v('website'), product: v('product'), offer: v('offer'), audience: v('audience'),
      proof: v('proof'), language: v('language'), types: [...document.querySelectorAll('.cb_type:checked')].map(x => x.value)};
    if (!body.brand || !body.product) { alert(L('请至少填写品牌名和产品/卖点', 'Please fill in at least the brand and the product/selling points')); return; }
    if (!engineReady()) return;
    try { await api(`/api/jobs/${encodeURIComponent(id)}/copywrite`, {method: 'POST', body}); state.lastBrief = body; state.copyIdx = 0; openJob(id); }
    catch (e) { alert(e.message); }
    return;
  }
  if (act === 'copycsv') {
    try { const r = await api(`/api/jobs/${encodeURIComponent(id)}/copycsv`, {method: 'POST'}); alert(L('已导出：', 'Exported: ') + r.path + L('\n可以在 Google Ads Editor 里通过“导入 → 从文件导入”使用（先补上广告系列和广告组名称）。', '\nImport it in Google Ads Editor via Import → From file (fill in the campaign and ad group names first).')); }
    catch (e) { alert(e.message); }
    return;
  }
  if (act === 'analyze' && !engineReady()) return;
  try {
    const r = await api(`/api/jobs/${encodeURIComponent(id)}/${act}`, {method: 'POST'});
    if (act === 'delete') { state.cur = null; state.job = null; await loadJobs(); welcome(); return; }
    if (act === 'rerun') { state.tab = 'summary'; await loadJobs(); openJob(r.id); return; }
    if (act === 'export') alert(L('已导出：', 'Exported: ') + r.path + L('\n把整个任务文件夹打包，就能发给别人用浏览器打开。', '\nZip the whole job folder to share it; it opens in any browser.'));
    if (act === 'csv') { alert(L('已导出：\n', 'Exported:\n') + r.files.join('\n') + L('\n可以直接用 Excel 打开。', '\nOpens directly in Excel.')); return; }
    openJob(id);
  } catch (e) { alert(e.message); }
}

/* ---------------- 欢迎页 / 环境检查 / 设置 ---------------- */
async function welcome() {
  $('#main').innerHTML = `<div class="card welcome"><h2>${L('使用步骤', 'How to use')}</h2><ol>${L(`
    <li>打开代理/VPN，并切到你要研究的国家（广告按 IP 所在地投放）。</li>
    <li>点右上角“设置”，选择 AI 分析引擎：<b>Claude Code</b>（用你的 Claude 订阅，需先在终端运行 <code>claude</code> 并 <code>/login</code>）或 <b>Claude API</b>（填 API key）。不分析也能抓取。</li>
    <li>在左侧输入产品词或品牌词，点“开始抓取”。一次大约 3–10 分钟。</li>
    <li>抓不到的内容会在任务页的“抓不到的内容和原因”里说明。</li>`, `
    <li>Make sure you browse from the country you want to research (use a proxy/VPN if needed): ads are served by IP location.</li>
    <li>Open “Settings” (top right) and choose the AI engine: <b>Claude Code</b> (uses your Claude subscription; run <code>claude</code> and <code>/login</code> in a terminal first) or <b>Claude API</b> (API key). Collecting works without AI too.</li>
    <li>Enter a product or brand keyword on the left and click “Start”. A run takes about 3–10 minutes.</li>
    <li>Anything that could not be collected is explained under “What could not be collected, and why” on the job page.</li>`)}</ol>
    <h2 style="margin-top:22px">${L('环境检查', 'Setup check')}</h2><div id="checkBox" class="muted">${L('检查中…', 'Checking…')}</div></div>`;
  runCheck();
}
async function runCheck() {
  try {
    const rows = await api('/api/check?refresh=1');
    const box = $('#checkBox');
    if (box) box.outerHTML = `<div class="check">${rows.map(r => `<span class="${r.ok ? 'ok' : 'no'}">${r.ok ? '✓' : '✕'} ${esc(r.name)}</span><span>${esc(r.detail)}</span>`).join('')}</div>`;
  } catch (e) { const box = $('#checkBox'); if (box) box.textContent = L('检查失败：', 'Check failed: ') + e.message; }
}
function syncEngineBoxes() {
  const e = $('#sEngine').value;
  $('#ccBox').style.display = e === 'claude_code' ? '' : 'none';
  $('#apiBox').style.display = e === 'api' ? '' : 'none';
}
function openSettings() {
  const s = state.settings;
  $('#sLang').value = LANG;
  $('#sEngine').innerHTML = Object.entries(s.engines).map(([k, v]) => `<option value="${k}" ${k === s.engine ? 'selected' : ''}>${esc(v)}</option>`).join('');
  $('#sCcModel').innerHTML = Object.entries(s.cc_models).map(([k, v]) => `<option value="${k}" ${k === s.cc_model ? 'selected' : ''}>${esc(v)}</option>`).join('');
  $('#sEngine').onchange = syncEngineBoxes;
  syncEngineBoxes();
  $('#sKey').value = s.api_key || '';
  $('#sModel').innerHTML = Object.entries(s.models).map(([k, v]) => `<option value="${k}" ${k === s.model ? 'selected' : ''}>${esc(v)}</option>`).join('');
  $('#sEffort').value = s.effort; $('#sBrowser').value = s.browser; $('#sProxy').value = s.proxy || '';
  $('#sManual').checked = !!s.manual_captcha; $('#sShow').checked = !!s.show_browser; $('#sVsec').value = s.video_seconds;
  $('#settingsModal').classList.add('on');
}
$('#btnSettings').onclick = openSettings;
$('#btnLang').onclick = () => setLang(LANG === 'en' ? 'zh' : 'en');
$('#sCancel').onclick = () => $('#settingsModal').classList.remove('on');
$('#sSave').onclick = async () => {
  const lang = $('#sLang').value;
  await api('/api/settings', {method: 'POST', body: {engine: $('#sEngine').value, cc_model: $('#sCcModel').value,
    api_key: $('#sKey').value.trim(), model: $('#sModel').value, effort: $('#sEffort').value,
    browser: $('#sBrowser').value, proxy: $('#sProxy').value.trim(), manual_captcha: $('#sManual').checked, show_browser: $('#sShow').checked,
    video_seconds: +$('#sVsec').value}});
  if (lang !== LANG) return setLang(lang);
  await loadSettings();
  $('#settingsModal').classList.remove('on');
};
$('#btnCheck').onclick = () => { state.cur = null; clearTimeout(state.timer); loadJobs(); welcome(); };

/* ---------------- 署名水印（作者要求保留，请勿删除或修改） ---------------- */
const WATERMARK_HTML = 'made by <a href="https://yestupa.com" target="_blank" rel="noopener">yestupa.com</a>/<a href="https://yestupa.net" target="_blank" rel="noopener">yestupa.net</a> with ❤️';
const WATERMARK_TEXT = 'made by yestupa.com/yestupa.net with ❤️';
function ensureWatermark() {
  let f = document.getElementById('yestupa-wm');
  if (!f || f.textContent.replace(/\s+/g, ' ').trim() !== WATERMARK_TEXT || getComputedStyle(f).display === 'none' || getComputedStyle(f).visibility === 'hidden') {
    if (f) f.remove();
    f = document.createElement('footer');
    f.id = 'yestupa-wm';
    f.className = 'wm';
    f.innerHTML = WATERMARK_HTML;
    document.body.appendChild(f);
  }
}
ensureWatermark();
new MutationObserver(() => ensureWatermark()).observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class', 'hidden']});
setInterval(ensureWatermark, 3000);

/* ---------------- 启动 ---------------- */
if (STATIC) {
  document.body.classList.add('static');
  state.job = STATIC;
  document.title = STATIC.keyword + L(' · Google 广告侦察报告', ' · Google Ad Spy report');
  renderJob();
} else {
  api('/api/brief').then(b => { state.lastBrief = b; }).catch(() => {});
  loadSettings().then(async () => {
    // 设置里的语言为准；第一次使用（还没设置过）时，把按浏览器猜的语言存进设置，后台提示也用这个语言
    const saved = state.settings.ui_lang;
    if (!saved) await api('/api/settings', {method: 'POST', body: {ui_lang: LANG}}).catch(() => {});
    else if (saved !== LANG) { store.set('adspy_lang', saved); location.reload(); return; }
    await loadJobs();
    state.jobs[0] ? openJob(state.jobs[0].id) : welcome();
  });
}
