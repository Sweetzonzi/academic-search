"""pytest 全局配置：把 src/ 放进 import 路径，并提供「本机真工具」的定位。"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _find_real_pandoc() -> str:
    """本机可用的真实 pandoc；都没有返回空串。

    只服务「真工具」用例，不进生产路径（生产那边是 `textify.find_pandoc()`：
    `vendor/pandoc/` → PATH）。按可信度排序：

    1. `ACADEMIC_SEARCH_PANDOC` —— 显式指定，跨机可配；
    2. PATH 上的 `pandoc`；
    3. 当前解释器自带的 `pypandoc`（`pip install pypandoc_binary` 会带一个）；
    4. DSH 内置 runtime 自带的副本——用 `$DSH_HOME` 定位、按通配符找运行时候选，
       **不把任何用户目录写进仓库**。
    """
    candidates = [os.environ.get("ACADEMIC_SEARCH_PANDOC") or "", shutil.which("pandoc") or ""]
    try:
        import pypandoc  # type: ignore

        candidates.append(pypandoc.get_pandoc_path())
    except Exception:  # 没装 pypandoc 是常态，不是错误
        pass
    dsh_home = os.environ.get("DSH_HOME")
    if dsh_home:
        pattern = "dsh-runtimes/*/dependencies/python/**/pypandoc/files/pandoc*"
        candidates += [str(path) for path in sorted(pathlib.Path(dsh_home).glob(pattern))]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


@pytest.fixture(scope="session")
def real_pandoc() -> str:
    """真 pandoc 的路径；本机没有就跳过用它的用例（不静默漏掉）。"""
    path = _find_real_pandoc()
    if not path:
        pytest.skip("本机没有可用的 pandoc")
    return path


@pytest.fixture(autouse=True)
def offline_openalex_key(request, monkeypatch):
    """离线用例只用假 key，防止失败断言输出用户真实凭据。"""
    if request.node.get_closest_marker("live") is None:
        monkeypatch.setenv("OPENALEX_API_KEY", "offline-test-key")
