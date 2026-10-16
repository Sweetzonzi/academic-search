"""`sync` 的端到端：运行粒度、产物形状、`--query` 语义、留档与额度降级。

逐条对应 ADR-0066 与 docs/04-里程碑与计划.md §2 的验收判据；不联网。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
from datetime import date

import pytest

from academic_search import cli, config, report, service, store
from academic_search.sources.http import HttpBytesResponse, HttpClient

FIX = pathlib.Path(__file__).parent / "fixtures"

TWO_QUERIES = """
[[query]]
name   = "alpha"
all_of = ["attention"]

[[query]]
name   = "beta"
any_of = ["control"]
"""


class SyncClient(HttpClient):
    """真 `HttpClient` 的日志与记账 + 假响应：走 `get()` 的留档路径，但不联网。

    直接覆写 `get_bytes` 而不是 `get`，是为了让 `_record_exchange` 与
    `_record_cost` 都跑在真实代码上——留档与额度降级正是被测对象。
    """

    def __init__(self, *, remaining: str = "1.0", cost: str = "0.001"):
        super().__init__()
        self.urls: list[str] = []
        self.remaining = remaining
        self.cost_header = cost

    def get_bytes(self, url, *, headers=None, source="", raise_on_error=True, max_bytes=None):
        self.requests += 1
        self.urls.append(url)
        if "api.openalex.org/works" in url:
            head = {"x-ratelimit-remaining-usd": self.remaining,
                    "x-ratelimit-cost-usd": self.cost_header}
            body = (FIX / "openalex_work.json").read_bytes()
        elif "export.arxiv.org" in url:
            head = {}
            body = (FIX / "arxiv_feed.xml").read_bytes()
        else:
            raise AssertionError("no fixture for " + url)
        self._record_cost(head)
        return HttpBytesResponse(url, 200, body, head)


@pytest.fixture()
def root(tmp_path):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return tmp_path


def write_profile(root, text: str) -> None:
    (root / "config" / "profile.toml").write_text(text, encoding="utf-8")


def write_search_config(root, text: str) -> None:
    (root / "config" / "search.toml").write_text(text, encoding="utf-8")


def envelope_of(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    return json.loads(out[-1])


def run_sync(root, capsys, monkeypatch, *extra, client=None):
    client = client or SyncClient()
    monkeypatch.setattr(service, "make_client", lambda: client)
    code = cli.main(["--root", str(root), "sync", *extra])
    return code, envelope_of(capsys), client


def read_run_json(root, run_id: int) -> dict:
    path = report.run_dir(root, run_id) / report.RUN_JSON
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# 判据 1：一条具名查询 = 一行 run、一个目录
# --------------------------------------------------------------------------

def test_one_run_and_one_dir_per_query(root, capsys, monkeypatch):
    write_profile(root, TWO_QUERIES)
    code, env, _ = run_sync(root, capsys, monkeypatch)
    assert code == 0
    assert len(env["data"]["runs"]) == 2

    conn = store.connect(config.db_path(root))
    try:
        rows = conn.execute("SELECT run_id, query_name FROM run ORDER BY run_id").fetchall()
    finally:
        conn.close()
    assert [row["query_name"] for row in rows] == ["alpha", "beta"]
    for row in rows:
        directory = report.run_dir(root, row["run_id"])
        assert (directory / report.RUN_JSON).is_file()
        assert (directory / report.REPORT_MD).is_file()


def test_only_sync_archives_run_directories(root, capsys, monkeypatch):
    """ADR-0066 决策 6：search / lookup 只写账本行，不建目录。"""
    monkeypatch.setattr(service, "make_client", lambda: SyncClient())
    args = ["--root", str(root), "search", "--any-of", "attention", "--refresh", "true"]
    assert cli.main(args) == 0
    envelope_of(capsys)
    assert not report.runs_dir(root).exists()
    conn = store.connect(config.db_path(root))
    try:
        row = conn.execute("SELECT responses, query_name FROM run").fetchone()
    finally:
        conn.close()
    assert row["responses"] is None and row["query_name"] is None


# --------------------------------------------------------------------------
# 判据 2：run.json 单数 + 两栏；stdout 逐 run 自包含、无调用级合计
# --------------------------------------------------------------------------

def test_run_json_shape_and_stdout_subset(root, capsys, monkeypatch):
    write_profile(root, TWO_QUERIES)
    code, env, _ = run_sync(root, capsys, monkeypatch)
    assert code == 0
    data = env["data"]
    assert "counts" not in data and "cost" not in data and "network" not in data

    for entry in data["runs"]:
        assert set(entry) == {
            "run_id", "status", "query", "spec_hash", "refreshed",
            "window", "counts", "cost",
        }
        assert entry["counts"]["by_source"] and not entry["counts"]["failures"]
        assert entry["cost"]["requests"] > 0

        doc = read_run_json(root, entry["run_id"])
        assert isinstance(doc["query"], str) and isinstance(doc["spec_hash"], str)
        assert doc["command"] == "sync"
        assert set(doc["items"]) == {"new_published", "new_discovered"}
        # stdout 元素是 run.json 的逐字真子集
        assert set(entry) < set(doc)
        assert doc["window"] == entry["window"]

    # items 的条目只装可溯源最小集
    doc = read_run_json(root, data["runs"][0]["run_id"])
    every = doc["items"]["new_published"] + doc["items"]["new_discovered"]
    assert every, "fixture 应当在本次 run 里入池至少一条候选"
    for item in every:
        assert set(item) == set(report.MINIMAL_FIELDS)


def test_items_two_columns_are_disjoint_and_dated(root, capsys, monkeypatch):
    write_profile(root, TWO_QUERIES)
    code, env, _ = run_sync(root, capsys, monkeypatch)
    assert code == 0
    doc = read_run_json(root, env["data"]["runs"][0]["run_id"])
    keys_published = {i["work_key"] for i in doc["items"]["new_published"]}
    keys_discovered = {i["work_key"] for i in doc["items"]["new_discovered"]}
    assert not (keys_published & keys_discovered)
    window = doc["window"]
    for item in doc["items"]["new_published"]:
        assert window["from"] <= item["published_at"] <= window["to"]
    for item in doc["items"]["new_discovered"]:
        assert item["published_at"] is None or item["published_at"] < window["from"]


# --------------------------------------------------------------------------
# 判据 3：报告单元
# --------------------------------------------------------------------------

def test_latest_report_is_the_invocation_summary(root, capsys, monkeypatch):
    write_profile(root, TWO_QUERIES)
    code, env, _ = run_sync(root, capsys, monkeypatch)
    assert code == 0
    latest = report.latest_report_path(root).read_text(encoding="utf-8")
    for entry in env["data"]["runs"]:
        body = (report.run_dir(root, entry["run_id"]) / report.REPORT_MD).read_text(encoding="utf-8")
        assert body in latest, "每节内容应等于对应 run 的 report.md"
    assert latest.count("# run ") == 2


def test_single_query_latest_report_equals_the_run_report(root, capsys, monkeypatch):
    write_profile(root, '[[query]]\nname = "alpha"\nall_of = ["attention"]\n')
    code, env, _ = run_sync(root, capsys, monkeypatch)
    assert code == 0
    body = (report.run_dir(root, env["data"]["runs"][0]["run_id"]) / report.REPORT_MD).read_text(
        encoding="utf-8"
    )
    assert report.latest_report_path(root).read_text(encoding="utf-8") == body


# --------------------------------------------------------------------------
# 判据 4：--refresh 三值、--query 解析、首次执行的窗口
# --------------------------------------------------------------------------

def test_unknown_query_is_invalid_args(root, capsys, monkeypatch):
    write_profile(root, TWO_QUERIES)
    code, env, _ = run_sync(root, capsys, monkeypatch, "--query", "nope")
    assert code == 1
    assert env["error"]["code"] == "invalid_args"
    assert "alpha" in env["error"]["message"] and "beta" in env["error"]["message"]


@pytest.mark.parametrize(
    "profile, name",
    [
        ('[[query]]\nname = "off"\nany_of = ["x"]\nenabled = false\n', "off"),
        ('[[query]]\nname = "old"\nany_of = ["x"]\nexpires = "2000-01-01"\n', "old"),
    ],
)
def test_naming_a_disabled_or_expired_query_is_an_error(root, capsys, monkeypatch, profile, name):
    write_profile(root, profile)
    code, env, _ = run_sync(root, capsys, monkeypatch, "--query", name)
    assert code == 1
    assert env["error"]["code"] == "invalid_args"


def test_no_runnable_query_is_invalid_args(root, capsys, monkeypatch):
    write_profile(root, '[[query]]\nname = "off"\nany_of = ["x"]\nenabled = false\n')
    code, env, _ = run_sync(root, capsys, monkeypatch)
    assert code == 1
    assert env["error"]["code"] == "invalid_args"


def test_sync_without_profile_is_no_profile(root, capsys):
    assert cli.main(["--root", str(root), "sync"]) == 1
    assert envelope_of(capsys)["error"]["code"] == "no_profile"


def test_auto_skips_within_window_and_summarises(root, capsys, monkeypatch):
    write_profile(root, TWO_QUERIES)
    client = SyncClient()
    monkeypatch.setattr(service, "make_client", lambda: client)
    assert cli.main(["--root", str(root), "sync", "--refresh", "true"]) == 0
    envelope_of(capsys)
    sent_first = len(client.urls)

    assert cli.main(["--root", str(root), "sync"]) == 0
    env = envelope_of(capsys)
    assert len(client.urls) == sent_first, "auto 档在窗口内不该再发请求"
    assert env["data"]["degraded"]["reason"] == "2/2 条查询在新鲜度窗口内，跳过联网"
    assert all(entry["refreshed"] is False for entry in env["data"]["runs"])
    assert all(entry["cost"]["requests"] == 0 for entry in env["data"]["runs"])


def test_refresh_false_never_touches_the_network(root, capsys, monkeypatch):
    write_profile(root, TWO_QUERIES)
    code, env, client = run_sync(root, capsys, monkeypatch, "--refresh", "false")
    assert code == 0
    assert client.urls == []
    assert "degraded" not in env["data"], "显式 --refresh false 不是降级"


def test_first_execution_uses_since_years_then_lookback(root, capsys, monkeypatch):
    write_profile(root, '[[query]]\nname = "alpha"\nall_of = ["attention"]\nsince_years = 7\n')
    client = SyncClient()
    monkeypatch.setattr(service, "make_client", lambda: client)

    assert cli.main(["--root", str(root), "sync"]) == 0
    first = envelope_of(capsys)["data"]["runs"][0]["window"]
    span = (date.fromisoformat(first["to"]) - date.fromisoformat(first["from"])).days
    assert 7 * 365 - 2 <= span <= 7 * 365 + 2

    assert cli.main(["--root", str(root), "sync", "--refresh", "true"]) == 0
    second = envelope_of(capsys)["data"]["runs"][0]["window"]
    assert second["to"] == first["to"], "窗口上界恒为今天"
    span = (date.fromisoformat(second["to"]) - date.fromisoformat(second["from"])).days
    assert span == config.DEFAULT_LOOKBACK_DAYS


def test_two_queries_with_the_same_spec_each_use_the_ledger(root, capsys, monkeypatch):
    """同一批次里 spec_hash 相同的两条查询，后一条会命中前一条刚写的账本（ADR-0066 决策 7）。"""
    write_profile(root, TWO_QUERIES.replace('name   = "beta"', 'name   = "beta"').replace(
        'any_of = ["control"]', 'all_of = ["attention"]'
    ))
    code, env, _ = run_sync(root, capsys, monkeypatch)
    assert code == 0
    runs = env["data"]["runs"]
    assert runs[0]["spec_hash"] == runs[1]["spec_hash"]
    assert runs[0]["refreshed"] is True and runs[1]["refreshed"] is False
    assert "1/2 条查询在新鲜度窗口内" in env["data"]["degraded"]["reason"]


# --------------------------------------------------------------------------
# 判据 5：responses[] 留档与额度阈值
# --------------------------------------------------------------------------

def test_archived_responses_match_their_bytes(root, capsys, monkeypatch):
    write_profile(root, '[[query]]\nname = "alpha"\nall_of = ["attention"]\n')
    code, env, _ = run_sync(root, capsys, monkeypatch)
    assert code == 0
    run_id = env["data"]["runs"][0]["run_id"]
    doc = read_run_json(root, run_id)
    assert doc["responses"], "产生记录的那一页必须留档"
    assert [item["n"] for item in doc["responses"]] == list(range(1, len(doc["responses"]) + 1))
    for item in doc["responses"]:
        assert set(item) == {"n", "sha256", "source", "url", "page", "status"}
        assert item["page"] == 1 and item["status"] == 200
        path = report.responses_dir(root, run_id) / (str(item["n"]) + "-" + item["source"] + ".json")
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    assert {item["source"] for item in doc["responses"]} == {"openalex", "arxiv"}


def test_low_quota_stops_the_remaining_legs_and_queries(root, capsys, monkeypatch):
    write_profile(root, TWO_QUERIES)
    write_search_config(root, "[quota]\nmin_remaining_usd = 0.5\n")
    client = SyncClient(remaining="0.01")
    code, env, _ = run_sync(root, capsys, monkeypatch, client=client)
    assert code == 0
    runs = env["data"]["runs"]
    assert "余额不足" in env["data"]["degraded"]["reason"]
    # 第一条查询只跑完第一个源就被拦下，第二条一次请求都没发
    assert runs[0]["refreshed"] is True and runs[0]["counts"]["by_source"]["arxiv"] == 0
    assert runs[1]["refreshed"] is False
    assert len(client.urls) == 1, "余额不足之后不应再发请求"


def test_quota_above_remaining_is_not_a_degradation(root, capsys, monkeypatch):
    write_profile(root, TWO_QUERIES)
    write_search_config(root, "[quota]\nmin_remaining_usd = 0.05\n")
    code, env, client = run_sync(root, capsys, monkeypatch, client=SyncClient(remaining="9.0"))
    assert code == 0
    assert "degraded" not in env["data"]
    assert all(entry["refreshed"] is True for entry in env["data"]["runs"])


# --------------------------------------------------------------------------
# 判据 6：删列与迁移
# --------------------------------------------------------------------------

def test_new_database_has_no_watermark_column(root):
    conn = store.connect(config.db_path(root))
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(run)")}
        assert "last_success_watermark" not in columns
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
    finally:
        conn.close()


def test_migration_drops_the_watermark_from_a_v2_database(tmp_path):
    db = config.db_path(tmp_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(db))
    raw.executescript(
        "CREATE TABLE run (run_id INTEGER PRIMARY KEY AUTOINCREMENT, spec_hash TEXT,"
        " query_name TEXT, refreshed INTEGER NOT NULL DEFAULT 0, window_from TEXT,"
        " window_to TEXT, started_at TEXT NOT NULL, finished_at TEXT,"
        " status TEXT NOT NULL DEFAULT 'running', requests INTEGER NOT NULL DEFAULT 0,"
        " cost_usd REAL NOT NULL DEFAULT 0.0, cost_basis TEXT, counts TEXT, responses TEXT,"
        " last_success_watermark TEXT);"
    )
    raw.execute(
        "INSERT INTO run (spec_hash, started_at, last_success_watermark)"
        " VALUES ('x', '2026-01-01T00:00:00Z', '2026-01-01')"
    )
    raw.execute("PRAGMA user_version = 2")
    raw.commit()
    raw.close()

    conn = store.connect(db)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(run)")}
        assert "last_success_watermark" not in columns
        assert conn.execute("SELECT COUNT(*) AS n FROM run").fetchone()["n"] == 1
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
    finally:
        conn.close()
