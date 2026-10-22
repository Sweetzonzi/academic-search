# academic-search：面向本课题组的 LLM 文献检索与阅读辅助技能

从 arXiv、OpenAlex、Crossref 查找论文并把结果缓存下来，装进 agent 就能用：按研究领域检索 → 结果进「候选池」→ 按策略入库 → **全文文本化成 LLM 读得懂的 Markdown（含图件与锚点）** → 中文摘要与翻译一并缓存 → 支持查阅、精读问答与总结；未来扩展到引用图谱与研究脉络。

面向**本课题组**：各人研究方向不同，画像各自独立；装了 Zotero 的人用它管条目与附件，没装的人走文件库后端，工具行为一致；结果通过导出文件交接，不建共享库。

由三部分组成：**一个确定性 CLI**（干重活）+ **一个薄 SKILL.md**（告诉 agent 何时调用）+ **一个 sidecar SQLite**（存语料与派生数据），「我的库」由 `LibraryBackend` 抽象，Zotero 只是默认实现。**CLI 不接任何 LLM API**：翻译与摘要由对话中的同一模型经子 agent 产出（[ADR-0037](docs/决策/ADR-0037.md)）。

---

## 文档体系与地图

文档按**职责**分六层，`doc-id` 与文件名解耦，引用写成 `DESIGN §4.2` / `ADR-0003` / `EX-2026-10-14-textify §3.3`。规则见 [docs/00-文档体系.md](docs/00-文档体系.md)，维护约束见 [AGENTS.md](AGENTS.md)。

| 文档 | 内容 | 什么时候读 |
|---|---|---|
| [AGENTS.md](AGENTS.md) | 改文档前必读：自包含性缺陷、更新动作、校验 | 改任何 Markdown 之前 |
| [REQ 需求说明](docs/01-需求说明.md) | 定位、原始需求原文、R/N 验收判据、场景、范围与非目标 | 确认「要做什么、怎么算做完」 |
| [DESIGN 设计说明书](docs/02-设计说明书.md) | 架构总览与系统设计；决策/契约/风险/待决已外移 | 评审或实现 |
| [IFACE 接口契约](docs/03-接口契约.md) | 命令、输出信封、退出码、依赖策略；五类边界契约索引 | 写代码或接 skill 时 |
| [IFACE-DATA 数据模型](docs/接口/数据模型.md) | sidecar 的表、键、字段与全库不变量；物理 DDL 在 `src/**/schema.sql` | 写 SQL、迁移或加字段时 |
| [IFACE-SOURCE 数据源适配](docs/接口/数据源适配.md) | 逐源字段映射、源能力矩阵、规范枚举、源文本规范化、work 级聚合与硬排除 | 写某个源的客户端或改去重与过滤时 |
| [IFACE-PAYLOAD 输出载荷](docs/接口/输出载荷.md) | 结果条目、`run.json`、采集完整性、`digest` 与 `read` 的载荷，以及全部机器枚举与错误码 | 写命令输出或接 skill 时 |
| [PLAN 里程碑与计划](docs/04-里程碑与计划.md) | 里程碑、已接受待实现的决策（§2）、检索改进候选队列（§4，含优先级、范围与验收条件） | 想动手、排期或拍板 |
| [VERIFY 验证与验收](docs/05-验证与验收.md) | 判据→验证映射、测试分层（框架） | 写测试前 |
| [OPEN 待决事项](docs/06-待决事项.md) | 未拍板问题与待实测项 | 拍板前 |
| [RISK 风险登记册](docs/07-风险登记册.md) | 风险、概率、缓解与触发信号 | 评审风险 |
| [GLOSSARY 术语表](docs/术语表.md) | 全项目术语定义 | 遇到生词 |
| [决策索引](docs/决策/README.md) | 设计决策，一决策一文件（追加式） | 问「为什么这样定」 |
| [实验注册表](docs/实验/README.md) | 实验记录（追加式） | 问「数字从哪来、代价多大」 |
| [调研索引](docs/调研/README.md) | 本机与外部事实快照，带复核条件 | 核对事实依据 |
| [运维手册](docs/运维/README.md) | 可执行操作步骤（升级/授权/排障） | 执行运维动作 |
| [使用手册](docs/使用/快速开始.md) | 面向组员的安装与使用；agent 环境配置与排障见[初始化参考](skills/academic-search/references/setup.md)，画像见[配置目录](docs/使用/配置.md) | 安装、首次调用或分发前 |
| [开发导览](docs/开发/README.md) | 仓库布局、模块职责、依赖策略 | 开始实现时 |

---

## 当前状态

| 项 | 状态 |
|---|---|
| 需求 | 已记录并解读（[REQ](docs/01-需求说明.md)），**待确认**其中的范围与非目标 |
| 现状调研 | 已完成（[调研索引](docs/调研/README.md)），硬事实均有实测或官方文档支撑 |
| 设计 | [DESIGN](docs/02-设计说明书.md) 已成型；决策与状态见 [决策索引](docs/决策/README.md) |
| 本地分页 | `search` 已支持分段展示检索结果与全库清单，用法见[快速开始 §3](docs/使用/快速开始.md)，规则见 [ADR-0073](docs/决策/ADR-0073.md)，真实本地库验收见 [EX-2026-10-21-local-pagination](docs/实验/EX-2026-10-21-本地排序与分页.md) |
| 返回提示 | `search` / `lookup` 可返回 `warnings` 说明影响覆盖与结果判断的信息，字段见 [IFACE-PAYLOAD §2](docs/接口/输出载荷.md)，实跑验收见 [EX-2026-10-21-warnings](docs/实验/EX-2026-10-21-返回提示.md) |
| **阅读入口** | `read --work <work_key> --refresh auto` 获取转换后的 Markdown 与图件定位器；步骤见 [论文精读流程](skills/academic-search/SKILL.md#4-论文精读)，参数见 [CLI 参考 §11](skills/academic-search/references/cli-reference.md#11-read) |
| **文本化验证** | ✅ 已实测通过（[EX-2026-10-14-textify](docs/实验/EX-2026-10-14-文本化.md)）：8 篇 eess.SY 论文，两条路线均产出 `paper.md` + `figures/`，47 张图 0 失败、691 条交叉引用被改写 |
| 实施 | **M1 已落地、M0 部分完成**（免费 key 已配置并实测额度 `0.1` → `1`，见 [EX-2026-10-16-quota](docs/实验/EX-2026-10-16-额度.md)；增量口径 `Q1` 待测）；文本化流水线的取件-装配链已实现，`download` 已接线（取原始字节 → `bytes/` + `works/` 投影 + `artifact` + 版本判据，首次真跑见 [EX-2026-10-18-download](docs/实验/EX-2026-10-18-download.md)），`read` 已接线：arXiv 源码 / HTML → 正文、图件与章节定位器，缓存与实测见 [EX-2026-10-22-read](docs/实验/EX-2026-10-22-read.md)；M3 的 skill bundle 已在 `skills/academic-search/`，`sync` 已接线并对着真源跑过（首次真跑见 [EX-2026-10-16-sync](docs/实验/EX-2026-10-16-首次真sync.md)）。初版范围收在「LLM 检索缓存层」（**M0–M3**）：六条用户命令 + `sync` / `verify` / `profile` 三条支撑命令，不接 Zotero，引用图谱在初版外（[ADR-0032](docs/决策/ADR-0032.md)、[ADR-0046](docs/决策/ADR-0046.md)）；`export`（含 `bundle` 别名）与 A/B/C 质量分级顺延出初版，只保留撤稿与黑名单硬排除（[ADR-0051](docs/决策/ADR-0051.md)）。设计缺口已补齐：适配层、载荷契约、派生文本通道与运行留档见 [ADR-0052](docs/决策/ADR-0052.md)–[ADR-0055](docs/决策/ADR-0055.md)；实现前的五处契约口径（`sync` 摘要载荷、三个检索命令都落池、`textify` 的 LaTeX 装配与 PDF 图渲染、`digest put` 的键校验、部分失败的退出码）见 [ADR-0056](docs/决策/ADR-0056.md)。**2026-10-16 又落地七项口径**（venue 判据、两个引用阈值、`spec_hash` 口径、检索门、补桥校验，以及两处 Crossref 适配），逐条的验收记录见 [EX-2026-10-16-source-contract §16](docs/实验/EX-2026-10-16-检索口径与来源校验.md)；同日 [ADR-0066](docs/决策/ADR-0066.md) 的六项（`sync` 的运行粒度、产物形状、报告单元、`--query` 语义、`responses[]` 留档与额度阈值、删 `last_success_watermark`）也已落地，验收记录见 [EX-2026-10-16-sync §3](docs/实验/EX-2026-10-16-首次真sync.md)；**2026-10-18 落地 `download`**：首次真跑与判据对照见 [EX-2026-10-18-download](docs/实验/EX-2026-10-18-download.md)（含一处修掉的缺口——版本为空时每次取件都整份重下）；[PLAN §2](docs/04-里程碑与计划.md) 维护已接受待实现的工作 |
| 阻塞项 | 无。M0 是**省钱前置**而非 M1 的阻塞（增量已有「回看 N 天 + 本地去重」兜底）；Zotero 写入通道顺延到 M5。日常约束是 OpenAlex 每日额度（本机已配免费 key，`$1`/天；无 key 只有 `$0.10`/天且按出口 IP 共享）（[PLAN](docs/04-里程碑与计划.md)） |

---

## 五个必须先知道的事实

1. **Zotero 是「我的库」的权威，sidecar 是「领域语料 + 派生数据」的权威**，两者单向流动（promote），没有字段两处可改。「我的库」由 `LibraryBackend` 抽象，Zotero 只是默认实现（同学不装 Zotero 也能用）。**初版不接 Zotero**，「我的库」在 M5 之前是空的（[ADR-0032](docs/决策/ADR-0032.md)）。
2. **全库没有任何随机生成的标识符**：一条记录的 id 由 `(来源, 来源内 id)` 确定性派生（`record_uid`），一篇作品的键就是组内最早那条记录的 `record_uid`（`work_key`）。两台机器看同一批来源会得到同样的 id 与同样的 `works/` 路径，删库重建后也完全一致。第三方 ID 只作别名——因为实测被合并/删除的 OpenAlex Work ID 返回 404 且没有跳转。见 [ADR-0003](docs/决策/ADR-0003.md)、[EX-2026-10-14-identity](docs/实验/EX-2026-10-14-身份与去重.md)。
3. **同一篇论文的多个来源统统存下来，不做破坏性合并**：每条来源记录各占一行（`record`），由可重分组的 `work` 视图归为一篇；合并只是重定向（`merged_into`），永不改名、永不删记录。派生数据按源文本内容哈希共享，字节按 `sha256` 内容寻址。见 [ADR-0011](docs/决策/ADR-0011.md)。
4. **全文按需取**：定时 run 只采集元数据；「要原件」用 `download`，「要读正文」用 `read`。人和 agent 均优先读返回的 `works/` 正文与同目录图件，回退规则见[定位器契约](docs/接口/输出载荷.md)。产物落 `bytes/` + `works/`，不设收藏状态；正文与图件按同一转换清单绑定（[ADR-0035](docs/决策/ADR-0035.md)、[ADR-0074](docs/决策/ADR-0074.md)）。
5. **全文文本化走「原生文本优先」，且以 arXiv LaTeX 源码为主、HTML 为辅**：源码/HTML → pandoc → Markdown（图件随文落盘、章节锚点注入）。当前不解析本地 PDF 或出版社页面；源码中的 PDF 图可渲染成 PNG。注意 pandoc 的原始输出**不能直接用**，必须配一层归一化。实测见 [EX-2026-10-14-textify](docs/实验/EX-2026-10-14-文本化.md)。

---

## 目录约定（规划）

```
academic-search/
├─ README.md                  # 本文件：导航与状态
├─ AGENTS.md                  # 文档维护约束（自包含性）
├─ docs/                      # 见 docs/00-文档体系.md
│  ├─ 01-需求说明.md 02-设计说明书.md 03-接口契约.md 04-里程碑与计划.md
│  ├─ 05-验证与验收.md 06-待决事项.md 07-风险登记册.md 术语表.md
│  ├─ 决策/（ADR-NNNN.md + 索引） 实验/（EX-*.md + 注册表）
│  └─ 调研/ 运维/ 使用/ 开发/ 接口/  archive/
├─ tools/                     # 维护工具（doc_check / selfcheck / test_doc_check / survey_spike / install_skill）
├─ vendor/                    # 运行时依赖（便携 pandoc）；tools/ 只放维护脚本
├─ experiments/<里程碑>/       # 设计期实验：脚本与调研素材进版本库；样本、缓存与产物不进
├─ skills/academic-search/    # 交付物：SKILL.md + references/ + scripts/
├─ src/                       # CLI 实现（核心链路仅用标准库）
├─ bytes/<sha 前2位>/<sha256> # 内容坐标：内容寻址的字节实体，只读（不进版本库）
├─ works/<proj>/              # 作品坐标：人读投影（<年>-<首作者>-<标题>-<work_key>），指向 bytes/ 的硬链接（可重建）
├─ config/profile.toml        # 领域画像：唯一不可重建的用户数据；**整个 `config/` 都不进版本库**，缺省值由 `src` 内的 `DEFAULT_*` 兜底
├─ config/sources.toml        # 启用的检索数据库与开关
├─ config/search.toml         # 刷新阈值、增量回看天数、黑名单、摘要截断长度
├─ config/terms.toml          # 译名表（翻译用词对照）
├─ data/                      # sidecar.db、runs/<run_id>/（不进版本库）
└─ tests/                     # 含 fixtures/：脱敏后的真实响应，契约测试不联网
```
