"""界面语言（中文 / English）。

每个线程有自己的当前语言：网页请求按“设置”里的 ui_lang，抓取任务按创建任务时的语言。
文字直接写成 L('中文', 'English')，方便对照修改。
"""
import threading

_t = threading.local()


def set_lang(lang):
    _t.lang = 'en' if lang == 'en' else 'zh'


def lang():
    return getattr(_t, 'lang', 'zh')


def L(zh, en):
    return en if lang() == 'en' else zh
