---
doc-id: "DEV"
kind: "normative"
status: "draft"
title: "开发导览"
---


# 开发导览

> 面向实现者与 coding agent：仓库布局、模块职责、依赖策略。**实现细节优先写在代码旁**；这里只收跨模块、必须人人遵守的部分。

## 1. 复用与自研策略

**直接复用**（清单与 file:line 见 [REF-ASSETS](../调研/可复用资产-zotero-mcp.md)）：`identifiers.py`（去重第一层）、`local_db.py`（只读策略）、`fulltext_cache.py`（缓存骨架）、`ratelimit.py`（限流器）、`cli_json.py`（JSON 契约）、`skills/zotero-cli/SKILL.md`（skill 结构范例）、`discovery.py`（OpenAlex 客户端，在该项目的 `tools/` 下）。


## 2. 仓库布局

**实现前先读两份契约**：[IFACE-SOURCE](../接口/数据源适配.md)（逐源映射、源能力矩阵、work 级聚合、硬排除、画像到查询的映射）与 [IFACE-PAYLOAD](../接口/输出载荷.md)（载荷、枚举与错误码）。

## 目录约定（规划）

```
academic-search/
├─ README.md                  # 本文件：导航与状态
├─ AGENTS.md                  # 文档维护约束（自包含性）
├─ docs/                      # 需求 / 调研 / 设计 / 计划 / 实验记录
├─ tools/                     # 维护工具（doc_check / selfcheck / test_doc_check）
├─ vendor/                    # 运行时依赖（便携 pandoc）
├─ skills/academic-search/    # 交付物：SKILL.md + references/ + launcher（薄 bundle，见 IFACE-SKILL）
├─ src/                       # CLI 实现（核心链路仅用标准库）
├─ bytes/<sha 前2位>/<sha256> # 内容坐标：内容寻址的字节实体（不进版本库）
├─ works/<proj>/              # 作品坐标：人读投影，硬链接（可重建）
├─ config/                    # 用户配置：profile.toml、sources.toml、search.toml、terms.toml（不进版本库）
├─ data/                      # sidecar.db、runs/<run_id>/（不进版本库）
└─ tests/                     # 含 fixtures/：脱敏真实响应
```