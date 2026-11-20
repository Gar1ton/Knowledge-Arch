"""domain 层入库文件命名风格的契约测试（纯函数，无 I/O）。"""
from __future__ import annotations

from pathlib import PurePath

import pytest

from kacore.domain.file_style import (
    TEMPLATE_DASH,
    TEMPLATE_PAREN,
    FileStyle,
    infer_style,
    render_authors,
    render_dir,
    render_stem,
    safe_segment,
    unique_name,
)


@pytest.mark.parametrize(
    ("surnames", "expected"),
    [
        (["DeYoung"], "DeYoung"),
        (["Mischel", "Shoda"], "Mischel and Shoda"),
        (["Feng", "Li", "Wang"], "Feng et al."),
        ([], "Anon."),
    ],
)
def test_render_authors_follows_users_files(surnames: list[str], expected: str) -> None:
    assert render_authors(surnames) == expected


def test_render_stem_matches_existing_file_in_users_library() -> None:
    # 用户库里的真实文件名：Feng et al. (2026) - PERSONA, Dynamic and Compositional ….pdf
    stem = render_stem(
        FileStyle(), ["Feng", "Zhang", "Liu"], "2026",
        "PERSONA: Dynamic and Compositional Inference-Time Personality Control "
        "via Activation Vector Algebra",
    )
    assert stem == ("Feng et al. (2026) - PERSONA, Dynamic and Compositional Inference-Time "
                    "Personality Control via Activation Vector Algebra")


def test_render_stem_without_year_and_dash_template() -> None:
    no_year = render_stem(FileStyle(), ["Hui"], "", "Art and Cosmotechnics")
    assert no_year == "Hui (n.d.) - Art and Cosmotechnics"
    dash = FileStyle(name_template=TEMPLATE_DASH)
    assert render_stem(dash, ["Pink"], "2016", "Digital ethnography") == (
        "Pink - 2016 - Digital ethnography")


def test_render_stem_truncates_title_only() -> None:
    stem = render_stem(FileStyle(max_stem=60), ["Lin", "Xu", "Ahmed"], "2026", "word " * 40)
    assert stem.startswith("Lin et al. (2026) - word")
    assert len(stem) <= 60


def test_safe_segment_removes_windows_forbidden_characters() -> None:
    assert safe_segment('What is "AI"? A/B test: <draft>*') == "What is AI A-B test, draft"
    assert safe_segment("CON") == "_CON"
    assert safe_segment("trailing dots...") == "trailing dots"


def test_render_dir_uses_collection_path() -> None:
    style = FileStyle()
    path = ["[26.2] LIB - Year1, Literature Review", "Theories and Philosophy"]
    assert render_dir(style, path) == PurePath(*path)
    assert render_dir(style, []) == PurePath()


def test_unique_name_adds_number_like_zotmoov() -> None:
    taken = {"a.pdf", "a 1.pdf"}
    assert unique_name("a", ".pdf", taken) == "a 2.pdf"
    assert unique_name("b", ".pdf", taken) == "b.pdf"


def test_template_must_contain_title() -> None:
    with pytest.raises(ValueError):
        FileStyle(name_template="{authors} ({year})")


def test_infer_style_counts_formats_and_author_forms() -> None:
    names = [
        "Wei et al. (2026) - Beyond Static Personas.pdf",
        "Mischel and Shoda (1995) - A Cognitive-Affective System Theory of Personality.pdf",
        "DeYoung (2015) - Cybernetic Big Five Theory.pdf",
        "Feng - 2026 - PERSONA Dynamic and Compositional.pdf",
        "01 - Chapter 1 Introduction.pdf",
    ]
    report = infer_style(names)
    assert (report.total, report.paren, report.dash, report.other) == (5, 3, 1, 1)
    assert (report.authors_one, report.authors_two, report.authors_et_al) == (1, 1, 1)
    assert report.chosen.name_template == TEMPLATE_PAREN


def test_infer_style_picks_dash_when_it_dominates() -> None:
    report = infer_style(["A - 2020 - X.pdf", "B - 2021 - Y.pdf", "C (2022) - Z.pdf"])
    assert report.chosen.name_template == TEMPLATE_DASH
