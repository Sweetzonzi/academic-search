"""config：四份固定文件的解析、校验与出错行。"""

from __future__ import annotations

from pathlib import Path

from academic_search import config


def _write(root: Path, name: str, text: str) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / name).write_text(text, encoding="utf-8")


def test_no_profile(tmp_path):
    result = config.check_profile(tmp_path)
    assert result["ok"] is False
    assert result["error"]["code"] == "no_profile"


def test_scaffold_is_valid(tmp_path):
    _write(tmp_path, config.PROFILE, config.scaffold_profile())
    result = config.check_profile(tmp_path)
    assert result["errors"] == []
    assert result["ok"] is True
    assert result["queries"][0]["name"] == "world-model-uav"


def test_duplicate_name_reports_line(tmp_path):
    _write(
        tmp_path,
        config.PROFILE,
        "[[query]]\n"
        'name = "dup"\n'
        'any_of = ["a"]\n'
        "\n"
        "[[query]]\n"
        'name = "dup"\n'
        'any_of = ["b"]\n',
    )
    result = config.check_profile(tmp_path)
    assert result["ok"] is False
    dup = [e for e in result["errors"] if "重复" in e["message"]]
    assert dup and dup[0]["line"] == 6


def test_missing_name_and_keywords(tmp_path):
    _write(tmp_path, config.PROFILE, "[[query]]\nany_of = [\"a\"]\n")
    result = config.check_profile(tmp_path)
    assert any("缺 name" in e["message"] for e in result["errors"])


def test_missing_all_any(tmp_path):
    _write(tmp_path, config.PROFILE, "[[query]]\nname = \"q\"\n")
    result = config.check_profile(tmp_path)
    assert any("all_of 或 any_of" in e["message"] for e in result["errors"])


def test_bad_expires(tmp_path):
    _write(tmp_path, config.PROFILE, "[[query]]\nname = \"q\"\nany_of = [\"a\"]\nexpires = \"2026/12/31\"\n")
    result = config.check_profile(tmp_path)
    assert any("expires" in e["message"] for e in result["errors"])


def test_bad_since_years(tmp_path):
    _write(tmp_path, config.PROFILE, "[[query]]\nname = \"q\"\nany_of = [\"a\"]\nsince_years = \"3\"\n")
    result = config.check_profile(tmp_path)
    assert any("since_years" in e["message"] for e in result["errors"])


def test_toml_syntax_error_reports_line(tmp_path):
    _write(tmp_path, config.PROFILE, "[[query]\nname = \"q\"\n")
    result = config.check_profile(tmp_path)
    assert result["ok"] is False
    assert result["errors"][0]["line"] == 1


def test_load_profile_expiry(tmp_path):
    _write(tmp_path, config.PROFILE, "[[query]]\nname = \"q\"\nany_of = [\"a\"]\nexpires = \"2000-01-01\"\n")
    profile, errors = config.load_profile(tmp_path)
    assert errors == []
    assert profile is not None
    assert profile.by_name("q").expired() is True


def test_load_search_defaults(tmp_path):
    cfg = config.load_search(tmp_path)
    assert cfg.max_age_hours == 24
    assert cfg.lookback_days == 30
    assert cfg.abstract_chars == 800
    assert cfg.min_grade == "B"
    assert cfg.venue_blacklist == []
    assert cfg.mailto is None


def test_abstract_chars_falls_back_without_config(tmp_path):
    # config/ 不进版本库：缺键时必须回到代码里的缺省值（ADR-0058 决策 3）。
    assert config.DEFAULT_ABSTRACT_CHARS == 800
    assert config.load_search(tmp_path).abstract_chars == config.DEFAULT_ABSTRACT_CHARS


def test_load_search_overrides(tmp_path):
    _write(
        tmp_path,
        config.SEARCH,
        "[refresh]\nmax_age_hours = 6\n[sync]\nlookback_days = 7\n"
        "[quality]\nmin_grade = \"A\"\nvenue_blacklist = [\"Predatory Press\"]\n"
        "[abstract]\nchars = 100\n"
        "[contact]\nmailto = \"me@example.com\"\n",
    )
    cfg = config.load_search(tmp_path)
    assert cfg.max_age_hours == 6
    assert cfg.lookback_days == 7
    assert cfg.min_grade == "A"
    assert cfg.venue_blacklist == ["Predatory Press"]
    assert cfg.abstract_chars == 100
    assert cfg.mailto == "me@example.com"


def test_spec_hash_covers_only_remote_request_params():
    """`spec_hash` 的判据只有一条：它改不改变远端请求（ADR-0062 决策 1–3）。"""
    base = {"all_of": ["world model"], "sources": ["openalex"], "sort": "date"}
    same = config.spec_hash(**base)
    # 归一化：大小写、顺序、前后空白都不该改变 hash（ADR-0043）
    assert same == config.spec_hash(all_of=[" World Model "], sources=["OpenAlex"], sort="date")
    for changed in (
        {"sort": "citations"},
        {"sources": ["arxiv"]},
        {"min_citations": 100},
        {"min_fwci": 2.0},
        {"from_date": "2024-01-01"},
        {"venues": ["ICRA"]},
    ):
        assert config.spec_hash(**dict(base, **changed)) != same, changed


def test_spec_hash_ignores_resolved_dates_and_display_flags():
    """`--limit` / `--abstract` / `--in` 不进 hash；算出来的日期也不进（ADR-0062 决策 4）。"""
    base = dict(all_of=["a"], sources=["openalex"], sort="date")
    assert config.spec_hash(**base) == config.spec_hash(**dict(base, min_citations=None))
    # 只有**显式给**的窗口才进 hash：与「解析后的具体日期」是两件事
    assert config.spec_hash(**base) == config.spec_hash(**dict(base, from_date=None))
    assert config.spec_hash(**dict(base, from_date="2024-01-01")) != config.spec_hash(
        **dict(base, from_date="2024-01-02")
    )


def test_canonical_spec_is_stable_json():
    value = config.canonical_spec(all_of=["b", "a"], sort="date")
    assert value == config.canonical_spec(all_of=["A", "b"], sort="date")
    assert '"all_of":["a","b"]' in value


def test_load_sources_defaults_and_override(tmp_path):
    enabled = config.load_sources(tmp_path)
    assert enabled["openalex"] is True
    assert enabled["semantic_scholar"] is False
    assert config.active_sources(enabled, command="search") == ["openalex", "arxiv"]
    _write(tmp_path, config.SOURCES, "[openalex]\nenabled = false\n[arxiv]\nenabled = true\n")
    enabled = config.load_sources(tmp_path)
    assert enabled["openalex"] is False
    assert config.active_sources(enabled, command="search") == ["arxiv"]


def test_search_sources_exclude_crossref(tmp_path):
    # crossref enabled 但它是 lookup 兜底，不是采集源（IFACE §1）。
    _write(tmp_path, config.SOURCES, "[crossref]\nenabled = true\n")
    enabled = config.load_sources(tmp_path)
    assert "crossref" not in config.active_sources(enabled, command="search")


def test_terms_version_changes(tmp_path):
    _write(tmp_path, config.TERMS, "[[term]]\nen = \"world model\"\nzh = \"世界模型\"\n")
    table, version = config.load_terms(tmp_path)
    assert table["world model"] == "世界模型"
    first = version
    _write(tmp_path, config.TERMS, "[[term]]\nen = \"world model\"\nzh = \"世界模型X\"\n")
    _, second = config.load_terms(tmp_path)
    assert first != second


def test_terms_missing_is_empty(tmp_path):
    table, version = config.load_terms(tmp_path)
    assert table == {} and version == ""


def test_db_path(tmp_path):
    assert config.db_path(tmp_path) == tmp_path / "data" / "sidecar.db"
