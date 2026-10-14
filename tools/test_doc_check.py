#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doc_check.py 的灵敏度测试。

用本次真实修掉的缺陷串做固定样本，确认检查器抓得到；
再用"应当保留的历史"样本，确认它不会把合法内容一律标红。
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

TOOLS = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("doc_check", TOOLS / "doc_check.py")
dc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dc)

# 必须被抓到：这些是本次真实修掉的缺陷原句
MUST_FLAG = [
    "| **L** | **作品身份（推翻原 D3）** | **全库没有任何随机生成的标识符**…",
    "| 项 | 旧记录 | 实测更正 |",
    "> ⚠️ **本节的 ULID 随机铸造方案已在同日被推翻，保留作为\"备选方案\"的实测依据。**",
    "> 起因：讨论\"OpenAlex `work_id` 能否当唯一标识符\"。",
    "## 5. 对设计文档的修订建议",
    "### 8.4 额度口径的更正（影响预算估算）",
    "此前\"控制类论文框图常用 TikZ、需要编译\"的担心，在这批样本里没有出现。",
    "> 踩过的坑：第一版把 80 位拆成「16 位计数器 + 64 位随机」…",
    "13. **额度的真实口径**（修正 §3.2 早期结论）：",
]

# 不应被抓到：合法的技术表述（曾经全是误报）
SHOULD_PASS = [
    "Markdown 属于\"给 LLM 读\"的形态，放 sidecar 的 blob 目录更合适。",
    "随后**同一 IP 的匿名额度一整天都是 429**（2026-10-14 实测）",
    "需要给人看的 handle（比如导出的文件名、对话里的指称）时按需派生",
    "触发条件：① 某条记录后来才拿到 arXiv ID / DOI；② OpenAlex 侧发生合并或拆分",
    "保留状态而不是删除记录的原因：② 可复盘\"当时为什么不要\"",
    "**SICI 等旧式后缀的官方规范本轮未查证**",
    "让 LLM 能读全文，同时让人能在 Zotero/浏览器里读同一篇的文本版。",
]


def hits(line: str) -> list[str]:
    out = []
    for pat, _why in dc.SELF_PATTERNS:
        if re.search(pat, line):
            out.append(pat)
    return out


def main() -> int:
    print("灵敏度：真实缺陷必须被命中")
    miss = 0
    for s in MUST_FLAG:
        got = hits(s)
        if not got:
            miss += 1
        print(f"  {'✓' if got else '✗ 漏检'}  {s[:64]}")

    print("\n特异度：合法表述不应被命中")
    noise = 0
    for s in SHOULD_PASS:
        got = hits(s)
        if got:
            noise += 1
        print(f"  {'✗ 误报' if got else '✓'}  {s[:64]}" + (f"   ← {got}" if got else ""))

    print(f"\n漏检 {miss} 处 / 误报 {noise} 处")
    if miss or noise:
        print("✗ 检查器需调整")
        return 1
    print("✓ 检查器灵敏度与特异度均达标")
    return 0


if __name__ == "__main__":
    sys.exit(main())
