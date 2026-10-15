"""对外信封、SCHEMA_VERSION 与错误码。

契约：docs/03-接口契约.md §1、docs/接口/输出载荷.md §1/§6。
data 里装什么由各命令决定；本模块只保证外壳恒定：

    {ok: true,  command, schema, data}
    {ok: false, command, schema, error: {message, code}}

network / cost / degraded 是 **data 内的通用字段**（IFACE-PAYLOAD §1 的
「通用字段」表），不是顶层字段——顶层信封在四份文档里一律写作
{ok, command, schema, data|error}（ADR-0053、ADR-0056）。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Mapping, TextIO

#: 对外 schema 版本；只增不删（docs/接口/输出载荷.md §8）。
SCHEMA_VERSION = 1

#: error.code 全集，家见 docs/接口/输出载荷.md §6。退出码只有 0 / 1。
BUSY = "busy"
PROJECTION_MISSING = "projection_missing"
NO_PROFILE = "no_profile"
PROFILE_INVALID = "profile_invalid"
INVALID_ARGS = "invalid_args"
NOT_FOUND = "not_found"
SOURCE_ERROR = "source_error"
NETWORK_ERROR = "network_error"
RATE_LIMITED = "rate_limited"
IO_ERROR = "io_error"
INTERNAL_ERROR = "internal_error"

ERROR_CODES = frozenset(
    {
        BUSY,
        PROJECTION_MISSING,
        NO_PROFILE,
        PROFILE_INVALID,
        INVALID_ARGS,
        NOT_FOUND,
        SOURCE_ERROR,
        NETWORK_ERROR,
        RATE_LIMITED,
        IO_ERROR,
        INTERNAL_ERROR,
    }
)

#: 该错误重试同一命令是否有意义（IFACE-PAYLOAD §6 的「重试同一命令」列）。
RETRYABLE = frozenset({BUSY, SOURCE_ERROR, NETWORK_ERROR, RATE_LIMITED})


class EnvelopeError(Exception):
    """带 error.code 的受控失败；cli 捕获后转成失败信封。"""

    def __init__(self, code: str, message: str):
        if code not in ERROR_CODES:
            raise ValueError("未知 error.code: " + repr(code))
        super().__init__(message)
        self.code = code
        self.message = message


def success(command: str, data: Mapping[str, Any] | None = None) -> dict:
    """成功信封；data 缺席时省略该键。"""
    env: dict = {"ok": True, "command": command, "schema": SCHEMA_VERSION}
    if data is not None:
        env["data"] = dict(data)
    return env


def failure(command: str, code: str, message: str) -> dict:
    """失败信封；失败时 data 缺席。"""
    if code not in ERROR_CODES:
        raise ValueError("未知 error.code: " + repr(code))
    return {
        "ok": False,
        "command": command,
        "schema": SCHEMA_VERSION,
        "error": {"message": message, "code": code},
    }


def dump(envelope: Mapping[str, Any], stream: TextIO | None = None) -> None:
    """把信封写成一行 JSON 到 stdout（ensure_ascii=False）。"""
    out = stream if stream is not None else sys.stdout
    out.write(json.dumps(envelope, ensure_ascii=False, sort_keys=False))
    out.write("\n")
    out.flush()
