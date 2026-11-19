"""关键词召回的检索文本切分（utils，纯函数零依赖）。

为何存在：SQLite FTS5 的 `unicode61` tokenizer 把连续汉字当成一个词，「中央执行系统负荷」整段只有
一个 token，查询「执行系统」永远匹配不上。
所以写入和查询都先经本模块把文本变成「空格分隔的词」：

- 中文 / 日文（汉字与假名）连续段拆成重叠二元组：「中央执行」→「中央 央执 执行」；
  只有一个字的段原样保留；
- 其余字母 / 数字词（含带重音的拉丁字母、西里尔字母、韩文）转小写保留，词干与去重音交给 tokenizer
  （`porter unicode61 remove_diacritics 2`，见 `migrations/028_chunks_fts.sql`）。

写入端（分块入库）用 `search_text`，查询端用 `query_terms`（同一切分 + 去停用词）。
两端必须同一切分规则，
改规则等于改索引：已有索引不会自动重建，需要另写迁移清空 `chunks_fts` 后由启动回填重建。
"""
from __future__ import annotations

import re

__all__ = ["search_tokens", "search_text", "query_terms", "fts_match_expression"]

# 汉字（含扩展 A、兼容汉字）与日文假名。
_CJK_CHARS = "぀-ヿ㐀-䶿一-鿿豈-﫿"
_SEGMENT_RE = re.compile(rf"(?P<cjk>[{_CJK_CHARS}]+)|(?P<word>[^\W_{_CJK_CHARS}]+)")

# 一次查询最多带多少个词：长中文句子每个字都产生一个二元组，32 个只够覆盖约 30 个字。
_MAX_QUERY_TERMS = 64

# 英文功能词：只在查询端去掉，写入端照常索引（BM25 的 IDF 本来就压低它们，去掉是为了不让它们
# 把只命中虚词的分块拉进候选池）。
ENGLISH_STOPWORDS = frozenset("""
a about above after again against all am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have
having he her here hers herself him himself his how i if in into is it its itself just me more most
my myself no nor not of off on once only or other our ours ourselves out over own same she should so
some such than that the their theirs them themselves then there these they this those through to too
under until up very was we were what when where which while who whom why will with would you your
yours yourself yourselves also may might must shall within without upon via whether among
""".split())

# 中文虚词 / 提问套话：查询里按子串切掉，再对剩下的片段做二元组。只放多字短语和句末语气词——
# 单字虚词（的、是、在、和）同时是大量实词的组成部分（目的、是否、存在、总和），切掉会拆坏实词。
CHINESE_STOPWORDS = (
    "可不可以", "为什么", "是什么", "什么样", "怎么样", "有没有", "是不是", "能不能",
    "什么", "怎么", "怎样", "如何", "哪些", "哪个", "哪里", "是否", "这个", "那个", "这些", "那些",
    "这种", "那种", "我们", "你们", "他们", "它们", "可以", "能否", "以及", "或者", "因为", "所以",
    "但是", "而且", "如果", "关于", "对于", "一个", "一些", "其中", "之间", "请问", "一下", "具体",
    "吗", "呢", "吧", "啊", "呀",
)
_CHINESE_STOP_RE = re.compile(
    "|".join(sorted(map(re.escape, CHINESE_STOPWORDS), key=len, reverse=True))
)


def _bigrams(run: str) -> list[str]:
    if len(run) < 2:
        return [run] if run else []
    return [run[i:i + 2] for i in range(len(run) - 1)]


def search_tokens(text: str) -> list[str]:
    """把文本切成检索词：汉字 / 假名段拆二元组，其余词转小写。保持原文顺序，不去重。"""
    tokens: list[str] = []
    for match in _SEGMENT_RE.finditer(text):
        cjk = match.group("cjk")
        if cjk:
            tokens.extend(_bigrams(cjk))
        else:
            tokens.append(match.group("word").lower())
    return tokens


def search_text(text: str) -> str:
    """写入 FTS 的派生检索文本（`search_tokens` 用空格连接）。"""
    return " ".join(search_tokens(text))


def query_terms(query: str) -> list[str]:
    """查询词：与 `search_tokens` 同一切分，去掉中英文停用词后去重，最多 `_MAX_QUERY_TERMS` 个。

    全部是停用词的查询（如 "what is it"）退回不去停用词的切分，不让查询变成空。
    """
    kept: list[str] = []
    for match in _SEGMENT_RE.finditer(query):
        cjk = match.group("cjk")
        if cjk:
            for piece in _CHINESE_STOP_RE.split(cjk):
                kept.extend(_bigrams(piece))
        else:
            word = match.group("word").lower()
            if word not in ENGLISH_STOPWORDS:
                kept.append(word)
    terms = kept or search_tokens(query)
    return list(dict.fromkeys(terms))[:_MAX_QUERY_TERMS]


def fts_match_expression(terms: list[str]) -> str:
    """把查询词拼成 FTS5 MATCH 表达式：每个词加双引号当字面量（不会被当成 AND / NEAR / 列过滤
    等语法），词之间 OR。词里的双引号按 FTS5 规则写成两个。"""
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
