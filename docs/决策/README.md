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
| [ADR-0001](ADR-0001.md) | 三层存储：Zotero + sidecar + 字节层 | Zotero 装条目资产，sidecar 装工作台与派生数据，字节层装文本与图件 | accepted |
| [ADR-0002](ADR-0002.md) | 确定性 CLI + 薄 SKILL.md | 分页/限流/去重/幂等交给代码，LLM 只做查询扩展与总结；不用常驻 MCP | accepted |
| [ADR-0003](ADR-0003.md) | 作品身份：确定性派生 | `record_uid` 由 `(source, source_id)` 派生，`work_key` 取首领记录，第三方 ID 只作别名 | accepted |
| [ADR-0004](ADR-0004.md) | 中文摘要写回用 child note | 不改 Abstract 字段、不塞 Extra | accepted |
| [ADR-0005](ADR-0005.md) | 调度器中立 | 采集命令无头、幂等、可观测；用哪个调度器由使用者定 | accepted |
| [ADR-0006](ADR-0006.md) | 默认不下载 PDF | 按需获取，且只取 OA 版本 | accepted |
| [ADR-0007](ADR-0007.md) | 检索入口唯一 | `search` / `lookup`，内部固定「本地 → 候选池 → 联网」三段 | accepted |
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
| [ADR-0015](ADR-0015.md) | 图件随文保存 | 图件落 `works/<work_key>/figures/`，字节实体进 `bytes/<sha256>` | accepted |
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

## 工作台与范围决策

| 编号 | 标题 | 简述 | 状态 |
|---|---|---|---|
| [ADR-0027](ADR-0027.md) | 专题的组织方式 | 专题只由画像里的具名查询承载，不建独立实体（候选池 tag 已由 [ADR-0050](ADR-0050.md) 删除） | accepted |
| [ADR-0028](ADR-0028.md) | 报告分「新发表 / 新发现」 | 首次入池语义收敛到 `run_id`；发表时间不进幂等键 | accepted |
| [ADR-0029](ADR-0029.md) | 全文字节进 `bytes/` | sidecar 只存指针；v1 的 FTS5 不覆盖正文 | accepted |
| [ADR-0030](ADR-0030.md) | 确认按动作分级 | 写 Zotero 需确认，翻译与文本化全自动 | accepted |
| [ADR-0031](ADR-0031.md) | 综述取材在内、撰写与聚合产物在 v1 外 | v1 只做领域覆盖清单 | accepted |
| [ADR-0032](ADR-0032.md) | 初版范围 | 初版是「LLM 检索缓存层」，不接 Zotero；桥接整体顺延到 M5 | accepted |
| [ADR-0033](ADR-0033.md) | 字节层两个坐标 | `bytes/`（内容）与 `works/`（作品）；`works/` 目录名用 `work_key` | accepted |
| [ADR-0034](ADR-0034.md) | 投影用硬链接 | `works/` 硬链接到 `bytes/`，`bytes/` 里的文件设只读 | accepted |
| [ADR-0035](ADR-0035.md) | 全文按需取 | 定时 run 不取全文；「要这篇」= `textify` 产物落盘，不落状态 | accepted |
| [ADR-0036](ADR-0036.md) | 正文检索走文件扫描 | `--in body` 扫 `works/`；FTS5 只管英文标题与摘要原文 | accepted |
| [ADR-0037](ADR-0037.md) | CLI 不调 LLM | 翻译与摘要走 `digest list/put`，由对话模型经子 agent 产出 | accepted |
| [ADR-0038](ADR-0038.md) | 调度不进工具 | `sync` 只保证无头、幂等、报告结构化；频率由用户配 | accepted |
| [ADR-0039](ADR-0039.md) | 画像 agent 写、CLI 只读 | `profile show/check/scaffold`；未配置时先出草稿再确认 | accepted |
| [ADR-0040](ADR-0040.md) | 配置集中在 `config/` | 一份配置一个固定文件名；画像只有一份；密钥不进配置 | accepted |
| [ADR-0041](ADR-0041.md) | 临时检索 | `--all-of`／`--any-of` 当场给检索式；结果照样落候选池（具名查询入口已由 [ADR-0049](ADR-0049.md) 移出 `search`） | accepted |
| [ADR-0042](ADR-0042.md) | v1 只支持英文检索 | FTS5 用默认分词器；中文翻译不进索引，只给人读 | accepted |
| [ADR-0043](ADR-0043.md) | 检索只有一条路径 | 刷新（可选）→ 查库 → 返回；`--refresh auto\|true\|false`，判据按 `spec_hash` | accepted |
| [ADR-0044](ADR-0044.md) | 候选池不设状态机 | 行存在即入过池；`dismissed_at` / `dismissed_until` 两个可空时间戳；质量分级在读取端过滤 | accepted |
| [ADR-0045](ADR-0045.md) | 面向阅读的派生文本进 `bytes/` | 译文与中文摘要的文本进 `bytes/`，sidecar 只留缓存键与指针；FTS5 只索引英文原文 | accepted |
| [ADR-0046](ADR-0046.md) | 初版范围收在 M0–M3 | 引用图谱（M4 的 `graph`）在初版之外；`graph` 保留命令形状、实现后移 | accepted |
| [ADR-0047](ADR-0047.md) | 命令面收敛 | 不设 `dedupe` / `stage`（去重与落池内化）；初版不提供人工分组入口 | accepted |
| [ADR-0048](ADR-0048.md) | 初版只自动执行 L0/L1 | L2 弱判据登记为待确认（`dup_suspect`）并在结果里标注，不自动归组 | accepted |
| [ADR-0049](ADR-0049.md) | 检索式来源分工 | `search` 只跑当场给的检索式；`sync` 独占画像里的具名查询并产出报告 | accepted |
| [ADR-0050](ADR-0050.md) | 删除 topic 标签 | 候选池不记方向归属；按方向列清单与综述取材走 `search` 查库 | accepted |
| [ADR-0051](ADR-0051.md) | 初版范围再收敛 | 命令面 = 六条用户命令 + 三条支撑命令（`sync` / `verify` / `profile`）；`export` 与 A/B/C 分级顺延，初版只做纯规则硬排除；不做预算闸门，改为成本可见 + 余额降级 | accepted |
| [ADR-0052](ADR-0052.md) | 数据源适配层 | 逐源映射进 IFACE-SOURCE；日期列改为 `published_at` + `updated_at`；规范 type 六值（硬排除 = paratext ∪ 黑名单）；work 级聚合逐列写死；批量补桥 | accepted |
| [ADR-0053](ADR-0053.md) | 输出载荷与读取定位 | 载荷契约独立成 IFACE-PAYLOAD；`read` 改为定位器（bytes 路径 + 图件映射 + 锚点行号）；排序按 `published_at`，score 初版不写 | accepted |
| [ADR-0054](ADR-0054.md) | 派生文本的通道与键 | `digest put` 改批量 JSON；kind 含粒度；新增译名表 `config/terms.toml` 并把 `glossary_version` 更名 `terms_version`；content_hash 细则写死 | accepted |
| [ADR-0055](ADR-0055.md) | 运行留档与运行环境 | 留档合并为 `data/runs/<run_id>/`；宿主只装薄 bundle；`vendor/` 装 pandoc；回看 30 天；锁冲突即 `busy`；库内 UTC、报告本地时区 | accepted |
| [ADR-0056](ADR-0056.md) | 契约补口 | `sync` 的 `data` 是 `run.json` 真子集；三个检索命令都落候选池、`query_name` 匿名即 NULL；主 `.tex` 按 `\documentclass` 判定、PDF 图 `pdftocairo`→PyMuPDF 降级且渲染器进 `converter_version`；`digest put` 不收回传 hash；部分失败不改变退出码 | accepted |
| [ADR-0057](ADR-0057.md) | 检索载荷的粒度 | `search` / `lookup` 条目只保证可溯源最小集，摘要与正文不进载荷；不做分页/计数，全库规模由 `--limit` 兜住 | partially superseded by ADR-0058 |
| [ADR-0058](ADR-0058.md) | 摘要按需带出 | `lookup` 固定带完整摘要；`search` 新增 `--abstract`（默认关闭）带截断版，长度由 `search.toml` 的 `abstract.chars` 控制；多来源摘要按「版本层级 → 文本保真度」两级取值 | accepted |

