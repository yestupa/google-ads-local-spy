"""离线自检：用假的 Claude 客户端跑一遍 AI 分析流程（不联网、不计费），检查请求构造和结果回填是否正常。

用法：.venv\\Scripts\\python tools\\selftest_analysis.py <任务ID>
"""
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import analyze, config  # noqa: E402
from app.jobs import Job, run_analysis  # noqa: E402
from app.util import load_json  # noqa: E402

calls = []


def fake_parse(**kw):
    schema = kw['output_format']
    content = kw['messages'][0]['content']
    n_img = sum(1 for c in content if c.get('type') == 'image')
    text = ' '.join(c.get('text', '') for c in content if c.get('type') == 'text')
    calls.append({'schema': schema.__name__, 'images': n_img, 'chars': len(text), 'model': kw['model'], 'fallbacks': kw.get('fallbacks')})
    ids = []
    for c in content:
        if c.get('type') == 'text':
            for key in ('"id": "', 'id": "'):
                pass
    import re
    ids = re.findall(r'"id": "([^"]+)"', text) or ['x']

    def review(i, cls=analyze.Review):
        extra = {'textInImage': 'TEXT', 'visual': 'VISUAL'} if cls is analyze.DisplayReview else {}
        return cls(id=i, angle='角度', strengths=['优点1'], improvements=['改进1'], rewrite='改写', **extra)

    if schema is analyze.ReviewBatch:
        out = analyze.ReviewBatch(reviews=[review(i) for i in ids])
    elif schema is analyze.DisplayBatch:
        out = analyze.DisplayBatch(reviews=[review(i, analyze.DisplayReview) for i in ids])
    elif schema is analyze.MyCopy:
        long = 'This Headline Is Definitely Too Long For Google'  # 故意超长，测试自动改短
        out = analyze.MyCopy(
            positioning='定位', differentiation=['差异1'],
            search=analyze.SearchCopy(headlines=['Fresh Dog Food'] * 14 + [long], descriptions=['Desc'] * 4, path1='fresh', path2='dog-food',
                                      sitelinks=[analyze.Sitelink(text='Plans', desc1='a', desc2='b')] * 4, callouts=['Free Shipping'] * 4),
            shopping=[analyze.ShoppingTitle(title='Fresh Dog Food 5 lb', note='n')],
            display=analyze.DisplayCopy(shortHeadlines=['Fresh Food'] * 5, longHeadline='Long headline', descriptions=['d'] * 5),
            video=[analyze.VideoScript(name='v', hook='h', shots=[analyze.Shot(time='0-3s', visual='v', audio='a', purpose='p')], cta='Shop')],
            notes=['n'])
    elif schema is analyze.CopyFix:
        out = analyze.CopyFix(fixes=['Fresh Food, Vet Approved'])
    elif schema is analyze.VideoReview:
        out = analyze.VideoReview(hook='钩子', structure='结构', script=[analyze.Shot(time='0-3s', visual='v', audio='a', purpose='p')],
                                  angle='角度', strengths=['s'], improvements=['i'], rewrite='r')
    else:
        out = analyze.KeywordSummary(overview='概况', signals=[analyze.Signal(title='t', body='b')], commonAngles=['a'], offers=['o'],
                                     opportunities=['op'], recommendations=['r'])
    usage = types.SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0)
    return types.SimpleNamespace(stop_reason='end_turn', parsed_output=out, usage=usage)


def main():
    jid = sys.argv[1]
    data = load_json(os.path.join(config.JOBS, jid, 'job.json'))
    job = Job(json.loads(json.dumps(data)))
    job.save = lambda: None  # 不写回磁盘
    # 固定用假的 API 引擎，绝不调用真实的 Claude Code / API（不消耗额度）
    settings = dict(config.load_settings(), api_key='sk-ant-test', engine='api', model='claude-sonnet-5-5')
    for k in ('search', 'shopping', 'display', 'video'):
        for x in job.data['results'].get(k, []):
            x.pop('analysis', None)
    orig = analyze.Analyzer.__init__

    def init(self, s):
        orig(self, s)
        self.client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(parse=fake_parse)))
    analyze.Analyzer.__init__ = init
    run_analysis(job, settings, force=True)
    r = job.data['results']
    print('analysis step:', job.data['steps']['analysis'])
    print('calls:', json.dumps(calls, ensure_ascii=False, indent=0))
    for k in ('search', 'shopping', 'display', 'video'):
        xs = r.get(k, [])
        print(k, f'{sum(1 for x in xs if x.get("analysis"))}/{len(xs)} 条有分析')
    print('summary:', bool(job.data.get('summary')))
    # 我方文案：超长标题应被自动改短
    an = analyze.Analyzer(settings)
    res = an.write_copy(job.data['keyword'], job.data, {'brand': 'Test', 'product': 'p', 'language': 'English', 'types': ['search']})
    print('mycopy headlines:', len(res['search']['headlines']), '| last:', res['search']['headlines'][-1], '| violations:', len(res['violations']))


if __name__ == '__main__':
    main()
