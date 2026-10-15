"""venues：登记表解析、载体判据与 `S…` 逃生门（ADR-0060）。"""

from __future__ import annotations

import pytest

from academic_search import venues


def test_registry_resolves_shipped_names():
    ids, unknown = venues.resolve(["ICRA", "NeurIPS"])
    assert unknown == []
    assert ids == ["s4210217939", "s4363606243"]


def test_resolve_is_case_and_hyphen_insensitive():
    # U+2011 非断行连字符（RA‑L）也要与 ASCII 的 RA-L 命中同一条
    assert venues.resolve(["ra\u2011l"])[0] == venues.resolve(["RA-L"])[0]


def test_source_id_escape_hatch():
    ids, unknown = venues.resolve(["S9999999999"])
    assert ids == ["s9999999999"] and unknown == []


def test_unknown_name_is_reported_not_dropped():
    ids, unknown = venues.resolve(["ICRA", "不存在的会"])
    assert ids == ["s4210217939"]
    assert unknown == ["不存在的会"]


def test_arxiv_records_carry_the_arxiv_source():
    """arXiv 记录没有 `locations[]`；`--venue arXiv` 靠 [[source_carrier]] 命中它。"""
    ids, unknown = venues.resolve(["arXiv"])
    assert unknown == []
    assert venues.source_carrier_ids("arxiv") == ("s4306400194",)
    carriers = venues.record_carriers({"source": "arxiv", "venue_source_ids": None})
    assert venues.hits(carriers, ids) is True


def test_record_carriers_union_and_fail_closed():
    carriers = venues.record_carriers(
        {"source": "openalex", "venue_source_ids": '["S4210217939", "S1"]'}
    )
    assert carriers == {"s4210217939", "s1"}
    # 空集合与任何名单都不相交：白名单不 fail-open（ADR-0060 否决项）
    assert venues.hits(venues.record_carriers({"source": "openalex"}), ["s1"]) is False
    assert venues.hits(carriers, []) is False


def test_load_registry_from_file(tmp_path):
    path = tmp_path / "venues.toml"
    path.write_text(
        '[[venue]]\nname = "Foo"\naliases = ["F"]\nids = ["S123"]\n'
        '[[source_carrier]]\nsource = "bar"\nids = ["S456"]\n',
        encoding="utf-8",
    )
    table, carriers, names = venues.load_registry(path)
    assert table == {"foo": ("s123",), "f": ("s123",)}
    assert carriers == {"bar": ("s456",)}
    assert names == ["Foo"]


def test_load_registry_missing_file_is_empty(tmp_path):
    assert venues.load_registry(tmp_path / "nope.toml") == ({}, {}, [])


def test_shipped_registry_covers_the_profile_whitelists():
    """`config/profile.toml` 三个查询写下的会名，除 AAMAS 外都应解析得到。

    AAMAS 在 OpenAlex 里没有会议实体（只有同名期刊 `S5405189`），
    这一点记在 EX-2026-10-16-source-contract §16，不静默替换。
    """
    _, unknown = venues.resolve(["NeurIPS", "ICML", "ICLR", "ICRA", "IROS", "RA-L"])
    assert unknown == []


@pytest.mark.parametrize("name", ["", "   ", "S", "S12x"])
def test_blank_and_malformed_names(name):
    assert venues.resolve([name]) == ([], [] if not name.strip() else [name])


def test_records_registry_names_for_error_text():
    assert "ICRA" in venues.known_names()
