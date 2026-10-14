---
doc-id: "DEV"
kind: "normative"
status: "draft"
title: "开发导览"
---


# 开发导览

> 面向实现者与 coding agent：仓库布局、模块职责、依赖策略。**实现细节优先写在代码旁**；这里只收跨模块、必须人人遵守的部分。

## 1. 复用与自研策略

**直接复用**（清单与 file:line 见 [REF-ASSETS](../调研/可复用资产-zotero-mcp.md)）：`identifiers.py`（去重第一层）、`local_db.py`（只读策略）、`fulltext_cache.py`（缓存骨架）、`ratelimit.py`（限流器）、`cli_json.py`（JSON 契约）、`skills/zotero-cli/SKILL.md`（skill 结构范例）、`tools/discovery.py`（OpenAlex 客户端）。


## 2. 仓库布局

## 目录约定（规划）

```
academic-search/
├─ README.md                  # 本文件：导航与状态
├─ AGENTS.md                  # 文档维护约束（自包含性）
├─ docs/                      # 需求 / 调研 / 设计 / 计划 / 实验记录
├─ tools/                     # 维护工具（doc_check / selfcheck / test_doc_check）
├─ skills/academic-search/    # 交付物：SKILL.md + references/ + scripts/
├─ src/                       # CLI 实现（核心链路仅用标准库）
├─ store/                     # 内容寻址的字节存储（<sha 前2位>/<sha256>，不进版本库）
├─ blobs/<work_key>/          # 给人看的产物投影（可重建）
├─ data/                      # sidecar.db、reports/、spike 产物（不进版本库）
└─ tests/
```