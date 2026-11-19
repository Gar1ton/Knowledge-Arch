"""关键词召回的派生检索文本（`kacore/utils/search_text.py`）：切分、停用词、MATCH 表达式。"""
from __future__ import annotations

from kacore.utils.search_text import (
    fts_match_expression,
    query_terms,
    search_text,
    search_tokens,
)


def test_cjk_runs_become_overlapping_bigrams() -> None:
    assert search_tokens("中央执行") == ["中央", "央执", "执行"]
    assert search_tokens("中") == ["中"]
    # 日文汉字与假名同属一段。
    assert search_tokens("検索する") == ["検索", "索す", "する"]


def test_latin_words_are_lowercased_and_split_from_cjk() -> None:
    assert search_tokens("T55具体说了") == ["t55", "具体", "体说", "说了"]
    assert search_text("Multi-head Attention, 2017") == "multi head attention 2017"


def test_query_terms_drop_english_stopwords() -> None:
    assert query_terms("what is the main limitation of the model in this paper") == [
        "main", "limitation", "model", "paper",
    ]


def test_query_terms_drop_chinese_function_phrases_before_bigrams() -> None:
    terms = query_terms("中央执行系统负荷越高数字广度会怎样")
    assert "怎样" not in terms and "会怎" not in terms
    assert {"中央", "执行", "系统", "负荷", "数字", "广度"} <= set(terms)
    # 单字虚词不切：「是否」之外的「是」仍是实词的一部分。
    assert "存在" in query_terms("存在哪些问题")


def test_all_stopword_query_falls_back_to_plain_terms() -> None:
    assert query_terms("what is it") == ["what", "is", "it"]
    assert query_terms("   ") == []
    assert query_terms("?!()") == []


def test_match_expression_quotes_every_term() -> None:
    assert fts_match_expression(["near", "and"]) == '"near" OR "and"'
    assert fts_match_expression(['a"b']) == '"a""b"'
