---
doc-id: "ADR-INDEX"
kind: "decision"
title: "设计决策索引"
---

# 设计决策索引

> 追加式：每条决策一份文件，编号 `ADR-NNNN`，**一经接受不再改写**；被取代时在旧文件标 `状态：superseded by ADR-XXXX`，不删正文。设计文档与实验记录只链接编号，不复述理由。
## 设计决策

| 编号 | 标题 | 简述 | 状态 |
|---|---|---|---|
| [ADR-0001](ADR-0001.md) | 三层存储：Zotero + sidecar + blob 目录 | Zotero 装条目资产，sidecar 装工作台与派生数据，blob 装文本与图件 | accepted |
| [ADR-0002](ADR-0002.md) | 确定性 CLI + 薄 SKILL.md | 分页/限流/去重/幂等交给代码，LLM 只做查询扩展与总结；不用常驻 MCP | accepted |
| [ADR-0003](ADR-0003.md) | 作品身份：确定性派生 | `record_uid` 由 `(source, source_id)` 派生，`work_key` 取首领记录，第三方 ID 只作别名 | accepted |
| [ADR-0004](ADR-0004.md) | 中文摘要写回用 child note | 不改 Abstract 字段、不塞 Extra | accepted |
| [ADR-0005](ADR-0005.md) | 调度器中立 | 采集命令无头、幂等、可观测；用哪个调度器由使用者定 | accepted |
| [ADR-0006](ADR-0006.md) | 默认不下载 PDF | 按需获取，且只取 OA 版本 | accepted |
| [ADR-0007](ADR-0007.md) | 检索入口唯一 | `search` / `lookup --scope auto`，内部固定「本地 → 候选池 → 联网」三段 | accepted |
| [ADR-0008](ADR-0008.md) | 权威归属 | Zotero 管「我的库」，sidecar 管「领域语料 + 派生数据」，单向 `promote` | accepted |
| [ADR-0009](ADR-0009.md) | 「我的库」抽象为 LibraryBackend | Zotero / File / None 三种实现，Zotero 只是默认 | accepted |
| [ADR-0010](ADR-0010.md) | 全文文本化 | 原生文本优先 + 随附便携 pandoc + 图随文保存 + 锚点注入 | accepted |
| [ADR-0011](ADR-0011.md) | 多来源并存 | 每条来源各存一行，派生按内容哈希共享，合并只重定向不删记录 | accepted |

## 立项与策略决策

| 编号 | 标题 | 简述 | 状态 |
|---|---|---|---|
| [ADR-0012](ADR-0012.md) | 课题组协作模型 | 同一套工具、各人画像独立；不建共享库，靠导出交接 | accepted |
| [ADR-0013](ADR-0013.md) | 无 Zotero 也能跑 | 做 `LibraryBackend` 抽象 | accepted |
| [ADR-0014](ADR-0014.md) | 启用 arXiv LaTeX 源码路线 | `/e-print/<id>` → pandoc → Markdown | accepted |
| [ADR-0015](ADR-0015.md) | 图件随文保存 | 图件落 `blobs/<work_key>/figures/`，字节实体进 `store/<sha256>` | accepted |
| [ADR-0016](ADR-0016.md) | 转换器依赖 | 随工具附便携 pandoc，版本进缓存键 | accepted |

## 文本化与身份 spike 决策

| 编号 | 标题 | 简述 | 状态 |
|---|---|---|---|
| [ADR-0017](ADR-0017.md) | pandoc 输出方言 | 固定 `commonmark_x+tex_math_dollars+pipe_tables+footnotes+implicit_figures` | accepted |
| [ADR-0018](ADR-0018.md) | 归一化层 | 公式环境拆壳、图件 HTML→Markdown、PDF 图转 PNG、锚点注入、交叉引用解析 | accepted |
| [ADR-0019](ADR-0019.md) | 转换器分发与依赖 | 用 `pypandoc_binary`（40.9 MB 下载 / 221 MB 磁盘） | accepted |
| [ADR-0020](ADR-0020.md) | TikZ 分支 | 降级为罕见回退，不进关键路径 | accepted |
| [ADR-0021](ADR-0021.md) | 两条路线的关系 | LaTeX 源码为主、arXiv HTML 为辅（回退 + 补表号表题 + 取 SVG） | accepted |
| [ADR-0022](ADR-0022.md) | arXiv HTML 可用性判定 | 必须 GET `/html/<id>` 探活，不信 Atom feed 的声明 | accepted |
| [ADR-0023](ADR-0023.md) | 作品身份：确定性 id | 全库不铸随机 id；身份可复现 | accepted |
| [ADR-0024](ADR-0024.md) | 多来源并存策略 | 统统存为记录、各自保留；派生按 `content_hash` 共享 | accepted |
| [ADR-0025](ADR-0025.md) | 去重判据 | L0 强标识 > L1 显式桥 > L2 title+作者+年份 > L3 人工；`title_key` 不进唯一约束 | accepted |
| [ADR-0026](ADR-0026.md) | 分组维护 | 常走 OpenAlex 单实体 GET（免费）取桥；自行保留 `merged_into` 映射 | accepted |

