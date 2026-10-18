"""信封与退出码的契约测试（VERIFY §2 要求的三套件之一）。"""

from __future__ import annotations

import io

import pytest

from academic_search import envelope


def test_success_shape():
    env = envelope.success("profile.show", {"query": []})
    assert env == {
        "ok": True,
        "command": "profile.show",
        "schema": envelope.SCHEMA_VERSION,
        "data": {"query": []},
    }


def test_success_without_data_omits_key():
    env = envelope.success("verify")
    assert "data" not in env
    assert env["ok"] is True


def test_failure_shape():
    env = envelope.failure("search", envelope.NOT_FOUND, "未命中")
    assert env["ok"] is False
    assert "data" not in env
    assert env["error"] == {"message": "未命中", "code": "not_found"}


def test_schema_version_is_one():
    assert envelope.SCHEMA_VERSION == 1


def test_error_codes_match_contract():
    assert envelope.ERROR_CODES == {
        "busy",
        "projection_missing",
        "no_profile",
        "profile_invalid",
        "invalid_args",
        "not_found",
        "source_error",
        "network_error",
        "rate_limited",
        "io_error",
        "data_not_writable",
        "internal_error",
    }


def test_retryable_subset():
    assert envelope.RETRYABLE <= envelope.ERROR_CODES
    assert "projection_missing" not in envelope.RETRYABLE
    assert "busy" in envelope.RETRYABLE


def test_unknown_code_rejected():
    with pytest.raises(ValueError):
        envelope.failure("search", "made_up", "x")
    with pytest.raises(ValueError):
        envelope.EnvelopeError("made_up", "x")


def test_dump_writes_single_line_json():
    buf = io.StringIO()
    envelope.dump(envelope.success("digest.list", {"items": []}), buf)
    text = buf.getvalue()
    assert text.endswith("\n")
    assert text.count("\n") == 1
    assert '"schema": 1' in text


def test_network_cost_degraded_live_in_data():
    # IFACE-PAYLOAD §1 的通用字段装在 data 里，不是顶层（见 envelope 模块 docstring）。
    env = envelope.success(
        "search",
        {"items": [], "network": False, "cost": {"usd": 0.0, "basis": "无请求", "requests": 0}},
    )
    assert "network" not in env
    assert env["data"]["network"] is False
