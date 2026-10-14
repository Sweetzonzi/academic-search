---
doc-id: REF-ASSETS
kind: reference
measured: 2026-10-07
title: 可复用资产：zotero-mcp v0.14.1 侦察结论
---

# 可复用资产：`zotero-mcp` v0.14.1 侦察结论

> 依据本地克隆 `D:\Files\Project_Paper\zotero-mcp`（HEAD `0faf1b2`，2026-10-07）。上游一升级即需复核。

依据：本地克隆 `D:\Files\Project_Paper\zotero-mcp`（HEAD `0faf1b2`，2026-10-07，工作树干净）。

**可直接复用（MIT，`src/zotero_mcp/**`）**

| 资产 | 用途 |
|---|---|
| `identifiers.py`（347 行，纯标准库，作者明确声明可外部 import） | DOI/arXiv/ISBN/标题归一化；提供 `normalize_doi`/`doi_match_key`/`arxiv_identity`/`normalize_title_for_matching`（NFKD + 标点归一 + 去首冠词，与 Zotero 自身 `duplicates.js` 对齐） |
| `local_db.py` | sqlite 只读策略：优先 `file:…?immutable=1`；`-wal` 非空时复制快照（按 size+mtime 缓存、节流） |
| `fulltext_cache.py` | 内容哈希缓存骨架：`sha256(attachment_key:mtime_ns:size)` 为键 + `index.json` + profile 不匹配即 miss + 落库即逐出 + 30 天 purge |
| `embeddings/ratelimit.py` | AIMD 双桶（TPM/RPS）+ Retry-After 限流器 |
| `cli_json.py` | 单一 JSON 信封 `{ok, command, schema, data\|error}`，`SCHEMA_VERSION` 只增不删；退出码 0/1 |
| `skills/zotero-cli/SKILL.md` | skill 三层结构范例（frontmatter / 正文 / `reference.md`），并有测试守护上下文成本关系 |
| `tools/discovery.py` | OpenAlex 客户端与"是否已入库"标注、`related` 一跳探索 |
| `docs/for-agents.md`、`docs/integration-test-plan.md`、`tests/live/` | 面向 agent 的接入说明、52 项真实库人工测试清单、sqlite vs local API vs web API 三方对照测试（默认跳过） |

**该项目的明确留白（须自研）**：① "检索→候选→打分→批量入库"编排（只有单条 add DOI/URL/ISBN）；② 翻译/摘要及其缓存（其 `tools/synthesis.py` 开头明确写"不调用 LLM，只收集与格式化"，是刻意不做）；③ 运行账本与无头采集入口（无 cron/daemon，只有启动时 `auto_update`）；④ 离线查重（其查重走 pyzotero，Zotero 未运行即不可用）；⑤ 引用图谱的本地存储与可视化（只有一跳 `related`）。

**许可证边界**（源码 + 许可证文件）：`src/**` = MIT；`plugin/**`（Zotero 内 TS 面板）= AGPL-3.0-or-later。复刻 `src/**` 保留版权声明即可；**不要把 `plugin/**` 并入 MIT 项目**。

---

