"""IFACE-SOURCE §6 的三个哈希向量（契约测试必须逐条断言）。"""

from __future__ import annotations

from academic_search import contenthash


def test_v1_markup_stripped():
    assert contenthash.content_hash("<jats:p>Hello   world</jats:p>", "abstract") == (
        contenthash.content_hash("Hello world", "abstract")
    )


def test_v2_hyphen_normalized():
    # U+2011 non-breaking hyphen
    assert contenthash.content_hash("state\u2011of\u2011the\u2011art method", "abstract") == (
        contenthash.content_hash("state-of-the-art method", "abstract")
    )


def test_v3_whitespace_and_case_insensitive():
    assert contenthash.content_hash("Fig. 1 shows\u2026\n\nResults", "abstract") == (
        contenthash.content_hash("Fig. 1 shows\u2026 Results", "abstract")
    )


def test_body_kind_preserves_case():
    assert contenthash.content_hash("Results", "extracted_text") != (
        contenthash.content_hash("results", "extracted_text")
    )


def test_title_kind_case_insensitive():
    assert contenthash.normalize_source_text("A Title", "title") == "a title"


def test_nbsp_and_curly_quotes():
    assert contenthash.normalize_source_text("a\u00a0b", "title") == "a b"
    assert contenthash.normalize_source_text("\u201cquoted\u201d", "title") == '"quoted"'


def test_nfkc_folds():
    assert contenthash.normalize_source_text("Ｆｉｇ 1", "title") == "fig 1"


def test_hash_is_hex_sha256():
    h = contenthash.content_hash("x", "title")
    assert len(h) == 64
    int(h, 16)
