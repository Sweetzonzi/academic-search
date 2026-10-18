"""IFACE-STORE §1 的身份派生向量（契约测试必须逐条断言）。"""

from __future__ import annotations

import pytest

from academic_search import identity


def test_normalize_arxiv_strips_version_and_case():
    assert identity.normalize_source_id("arxiv", "1501.00001v2") == "1501.00001"
    assert identity.normalize_source_id("arxiv", "1501.00001V10") == "1501.00001"
    assert identity.normalize_source_id("arxiv", "Math/0601001") == "math/0601001"


def test_normalize_doi_and_openalex_case_insensitive():
    assert identity.normalize_source_id("doi", "10.1000/ABC") == "10.1000/abc"
    assert identity.normalize_source_id("openalex", "W123") == "w123"


def test_normalize_is_idempotent():
    for src, value in [("arxiv", "1501.00001v2"), ("doi", "10.1/X"), ("openalex", "W1")]:
        once = identity.normalize_source_id(src, value)
        assert identity.normalize_source_id(src, once) == once


def test_normalize_rejects_empty():
    with pytest.raises(ValueError):
        identity.normalize_source_id("arxiv", "  ")


def test_record_uid_is_20_lowercase_base32():
    uid = identity.record_uid("arxiv", "1501.00001v2")
    assert len(uid) == 20
    assert uid == uid.lower()
    assert set(uid) <= set("abcdefghijklmnopqrstuvwxyz234567")


def test_record_uid_version_and_case_insensitive():
    a = identity.record_uid("arxiv", "1501.00001v1")
    b = identity.record_uid("arxiv", "1501.00001v2")
    c = identity.record_uid("arxiv", "1501.00001")
    assert a == b == c


def test_record_uid_source_is_part_of_identity():
    assert identity.record_uid("arxiv", "x") != identity.record_uid("doi", "x")


# 金值向量：任何公式改动都会改这些值，属于破坏性变更（IFACE-STORE §1）。
GOLDEN = [
    ("openalex", "W2963403868", "rrt74tuybkn6ffkhljvd"),
    ("openalex", "W2100000000", "redpcjqnlce27chg5emw"),
    ("arxiv", "1501.00001v2", "ed3nmsifcbdwbn436i5a"),
    ("arxiv", "math/0601001", "2z5trvs5xfvbt5g7t7of"),
    ("arxiv", "2610.18758", "vzxjb55h26ylr3rnhozw"),
    ("doi", "10.1145/3290605.3300233", "4pn5tkeweqnrpyrlcoyf"),
    ("doi", "10.1000/xyz", "hlth2cevnqf6bvg7mrfj"),
]


@pytest.mark.parametrize("source,source_id,expected", GOLDEN)
def test_record_uid_golden(source, source_id, expected):
    assert identity.record_uid(source, source_id) == expected


def test_title_key_strips_punct_article_and_case():
    assert identity.title_key("The State-of-the-Art Method!") == "state of the art method"
    assert identity.title_key("  A   Tale of Two Cities ") == "tale of two cities"


def test_title_key_strips_markup():
    assert identity.title_key("Deep <sup>&</sup> Nets") == identity.title_key("Deep Nets")


def test_title_key_keeps_cjk():
    assert identity.title_key("世界模型 World Model") == "世界模型 world model"


def test_first_author_surname_forms():
    assert identity.first_author_surname(["Jane Doe", "John Roe"]) == "doe"
    assert identity.first_author_surname(["Doe, Jane"]) == "doe"
    assert identity.first_author_surname([]) == ""
    assert identity.first_author_surname(None) == ""

#: 20 位小写 base32，与 record_uid 的取值域相同。
KEY = "ed3nmsifcbdwbn436i5a"


def test_projection_slug_shape():
    slug = identity.projection_slug(
        KEY, title="World Models", authors=["Jane Doe"], published_at="2026-10-01",
    )
    assert slug == "2026-Doe-World-Models-" + KEY


def test_projection_slug_omits_the_missing_segments():
    assert identity.projection_slug(KEY, title="T") == "T-" + KEY
    assert identity.projection_slug(KEY, title="T", authors=[]) == "T-" + KEY


def test_projection_slug_falls_back_to_untitled():
    assert identity.projection_slug(KEY, title="  ...  ") == "untitled-" + KEY
    assert identity.projection_slug(KEY, title=None) == "untitled-" + KEY


def test_projection_slug_cleans_windows_forbidden_characters():
    slug = identity.projection_slug(KEY, title='A/B: "C" <D>|?*', published_at="2026-01-02")
    assert slug == "2026-A-B-C-D-" + KEY
    assert not set(slug) & set('<>:"/\\|?*')


def test_projection_slug_keeps_case_and_cjk():
    assert identity.projection_slug(
        KEY, title="世界模型 World Model", authors=["张三"], published_at="2026-10-01",
    ) == "2026-张三-世界模型-World-Model-" + KEY
    # "Doe, Jane" 这种形态取逗号前那一段；"Longsurname" 截 12 字符
    assert identity.projection_slug(
        KEY, title="T", authors=["Doe, Jane"], published_at="2026-10-01",
    ) == "2026-Doe-T-" + KEY
    assert identity.projection_slug(
        KEY, title="T", authors=["A Verylongsurname"], published_at="2026-10-01",
    ) == "2026-Verylongsurn-T-" + KEY


def test_projection_slug_caps_the_total_length():
    title = " ".join(["word"] * 40)
    slug = identity.projection_slug(
        KEY, title=title, authors=["Jane Doe"], published_at="2026-10-01",
    )
    assert len(slug) <= identity.PROJECTION_MAX
    assert slug.endswith("-" + KEY)
    assert "--" not in slug  # 截断按词边界，不留空段
    assert slug.startswith("2026-Doe-word-word-")


def test_work_key_of_reads_only_the_tail():
    assert identity.work_key_of("2026-Doe-T-" + KEY) == KEY
    assert identity.work_key_of(KEY) == KEY
    assert identity.work_key_of(KEY.upper()) is None  # 尾段必须是小写 base32
    assert identity.work_key_of("notes") is None  # 手工目录：跳过，不报错
    assert identity.work_key_of("2026-Doe-T") is None
    assert identity.work_key_of("") is None
