"""venues：登记表解析、载体判据与 `S…` 逃生门（ADR-0060）。"""

from __future__ import annotations

import pathlib
import tomllib

import pytest

from academic_search import venues

REGISTRY_PATH = pathlib.Path(venues.__file__).with_name(venues.REGISTRY_NAME)


def test_registry_resolves_shipped_names():
    ids, unknown = venues.resolve(["ICRA", "NeurIPS"])
    assert unknown == []
    assert ids == ["s4210217939", "s4363606243"]


def test_resolve_is_case_and_hyphen_insensitive():
    # U+2011 非断行连字符（RA‑L）也要与 ASCII 的 RA-L 命中同一条
    assert venues.resolve(["ra\u2011l"])[0] == venues.resolve(["RA-L"])[0]


def test_aliases_and_full_names_resolve_to_the_same_id():
    """别名与全称都取自实测的 `display_name`：论文上印哪个写法都能写进配置。"""
    for group in (
        ["RA-L", "IEEE RA-L", "IEEE Robotics and Automation Letters",
         "Robotics and Automation Letters"],
        ["ICRA", "IEEE ICRA", "International Conference on Robotics and Automation"],
        ["NeurIPS", "NIPS", "neural information processing systems"],
        ["JAAMAS", "Autonomous Agents and Multi-Agent Systems"],
        ["Artificial Intelligence", "AIJ"],
        ["arXiv", "arXiv (Cornell University)"],
    ):
        ids = [venues.resolve([name])[0] for name in group]
        assert all(item == ids[0] and item for item in ids), group


def test_registry_has_no_key_collisions():
    """别名撞车会让后一条**静默覆盖**前一条——登记表必须保持键不重叠。"""
    raw = tomllib.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    seen: dict[str, str] = {}
    for entry in raw.get("venue", []):
        for name in [entry["name"]] + list(entry.get("aliases") or []):
            key = venues.venue_key(name)
            assert key not in seen, (name, seen.get(key))
            seen[key] = entry["name"]


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
    """`config/profile.toml` 三个查询写下的每一个会名都应解析得到。

    `AAMAS` 的会议实体载体名被上游写成 `Adaptive Agents and Multi-Agents Systems`，
    按会议真名与缩写搜都是 0 条；能登记它靠 `locations.source.id` 反查，
    这一点记在 EX-2026-10-16-source-contract §16(a)(f)，不静默替换成期刊。
    """
    _, unknown = venues.resolve(
        ["NeurIPS", "ICML", "ICLR", "ICRA", "IROS", "RA-L",
         "AAMAS", "JAAMAS", "JAIR", "Artificial Intelligence"]
    )
    assert unknown == []


def test_aamas_conference_and_journal_are_distinct_entities():
    """别名撞车会让会议名静默落到期刊上——这两条必须是两个 id。"""
    conference, _ = venues.resolve(["AAMAS"])
    journal, _ = venues.resolve(["JAAMAS"])
    assert conference == ["s4306417597"]
    assert journal == ["s5405189"]
    assert conference != journal


def test_aamas_full_name_spellings_do_not_silently_cross_entities():
    """会议真名与期刊名只差 `Multiagent` / `Multi-Agent` 的拼写，两者不能互相顶替。

    会议真名（连写）不登记 → 报错，这是 fail-closed 的一侧；
    带连字符的写法是期刊 `S5405189` 的实际载体名，落到期刊是正确的。
    """
    assert venues.resolve(["Autonomous Agents and Multiagent Systems"]) == (
        [],
        ["Autonomous Agents and Multiagent Systems"],
    )
    assert venues.resolve(["Autonomous Agents and Multi-Agent Systems"])[0] == ["s5405189"]


@pytest.mark.parametrize("name", ["", "   ", "S", "S12x"])
def test_blank_and_malformed_names(name):
    assert venues.resolve([name]) == ([], [] if not name.strip() else [name])


def test_records_registry_names_for_error_text():
    assert "ICRA" in venues.known_names()
