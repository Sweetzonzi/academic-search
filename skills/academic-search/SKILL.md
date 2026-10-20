---
name: academic-search
description: 从 arXiv、OpenAlex 与本地缓存检索学术论文，用于领域入门、经典与前沿调研、按主题或作者及载体找文献，以及按 DOI、arXiv ID 或标题查单篇；结果带可核查出处。
metadata:
  agent_created: "true"
---

# academic-search

通过 CLI 取回论文元数据并缓存，再根据用户的研究问题筛选、核查和组织文献。单篇查询直接选命令；领域入门或经典与前沿调研使用 §3 的探索流程。

CLI 的检索结果均来自本地库，联网负责刷新语料。每篇以 `work_key` 标识，`doi` / `arxiv_id` 和 `sources[]` 提供出处；列表可能同时包含本次取回与既有缓存条目。

## 1. 调用入口

    skills/academic-search/scripts/run.cmd  <命令> [参数...]     # Windows
    skills/academic-search/scripts/run.sh   <命令> [参数...]     # POSIX

- 上述路径用于仓库内调用；安装后的副本用该 skill 目录下的 `scripts/`，不要假定当前目录就是仓库根。参数与示例按需读 [CLI 参考](references/cli-reference.md) 对应命令小节。
- 仓库根解析顺序：`--root` → `ACADEMIC_SEARCH_ROOT` → bundle 的 `root.txt` → bundle 的上两级目录。`--root` 是全局选项，必须放在子命令前：`--root <路径> search ...`。
- 显式写全 `--refresh auto|true|false`：通常用 `auto` 复用新鲜缓存，要求强制更新时用 `true`，仅查本地用 `false`。`search` 的 `auto` 按检索式新鲜度判断；`lookup` 的 `auto` 按标识是否已命中判断。对 DOI / arXiv ID，`true` 均先刷新再查本地。
- 不直接修改 `data/`；采集、去重与落库交给 CLI。CLI 不调用 LLM，文献筛选与解释由当前 agent 完成。

## 2. 按任务选择入口

| 用户想要 | 命令 |
|---|---|
| 一次性主题检索、某方向的最新进展 | `search --all-of ...` / `--any-of ...`；不需要画像 |
| 领域入门、经典与前沿 | 按 §3 拆分查询，再用 `search` 与 `lookup` 筛选核查 |
| 本地已有文献 | `search --refresh false`；不给检索式时列本地全部，**空检索式只配 `false`** |
| 确定的单篇 | `lookup --doi ...` / `--arxiv ...`；标题已知且库内检索时可用 `--title` |
| 指定会议或期刊 | `search --venue ICRA` 等；名字须能解析，详见 CLI 参考 §1 |
| 执行已保存的具名查询并生成报告 | `sync` / `sync --query 名`；画像处理见 §4，调度由宿主负责 |
| 下载原件 | `download --work <work_key> --source auto\|pdf\|all`；仅支持 arXiv，只取件、不转换 |
| 配置与维护 | `profile show\|check\|scaffold`、`verify`、`version`；按需读 CLI 参考对应小节 |

`lookup --title` 只做本地短语匹配，任何刷新档都不联网，也不做模糊排序。只有主题描述或标题不确定时，先 `search`，再用结果中的标识取单篇。

## 3. 领域探索：经典与前沿

**先形成问题框架。** 从用户描述提取应用场景、研究层级、任务、约束和排除范围。信息足以启动时，说明合理假设并开始检索；只有会改变研究范围的缺失信息才需要询问。例如无人机决策研究可关注任务归属、观测位置、通信与重规划，并据用户要求排除以姿态稳定和轨迹跟踪为主要贡献的工作。

**按研究分支组织查询。** 根据问题分开找通用理论与方法、应用桥接工作、近期进展，按任务规模选择需要的分支。基础方法不必含应用名；不要把“多智能体”直接收缩成 MARL，或把某个热门模型当作全部方法。`--any-of` 放同义词或候选短语，`--all-of` 表示必要条件；CLI 只提供这两组组合，复杂概念组合拆成多条查询。短词与缩写搭配领域词，避免跨领域同名命中。

| 检索目标 | 取数与筛选策略 |
|---|---|
| 经典与基础 | 优先用 OpenAlex 的 `--sort citations` 召回，再复核问题奠基、方法代表性或基准贡献。被引是全局计数，不是主题相关性；`--min-citations` / `--min-fwci` 按需要使用，不预设通用门槛，也不把低被引当成排除理由 |
| 近期代表工作 | 按用户时间范围给 `--since-years`，结合细分问题检索近期已发表工作；不把单次日期排序的头部视为整个时间窗口的代表 |
| 最新预印本 | 用 arXiv 补查，通常不设被引或 FWCI 下限；OpenAlex 补充正式载体与被引信息。根据领域覆盖选择互补来源，报告实际查询范围 |

给载体白名单或任一被引阈值会跳过 arXiv 采集；载体判据只认来源记录的指派，可能漏掉只有预印本记录的经典。`--min-citations` 单独使用仍按日期取数，要按被引召回需加 `--sort citations`；空 `fwci` 不通过阈值过滤。细则见 CLI 参考 §1。

**每批结果后决定下一步。** 先检查摘要与主题相关性、时间分布、版本重复和文献类型，再针对缺口追加查询，避免一次铺开所有分支。噪声多时收窄必要概念或拆分子问题；结果少时先看 §5 的本地漏斗和失败信息，再判断是否缺同义词、来源覆盖或缓存语料。代码发布、数据集、学位论文和综述可作为不同用途的资料，不能仅因有 DOI 就当成已发表研究论文。

**把召回边界纳入判断。** 每次远端取数仅一页、最多 100 条；`--limit` 只限制展示，增大它不能抓后续页。`counts.local.truncated=false` 只表示本地候选预算未耗尽，不证明远端完整。宽查询集中于最近几周时，用细分主题或其他来源补查，并检查近期代表工作是否缺失；不能声称已穷尽指定年份。CLI 未提供任意起止日期、排除词或分页选项，不编造参数。

**核查候选与结束检索。** 对准备推荐且摘要信息不足的条目用 `lookup` 读完整摘要，按 §6 处理日期、发表状态和证据。当主要分支已有与任务相符的代表文献、关键缺口已补查或说明、追加查询主要带来重复或低相关条目时收束；明确的预算或来源限制也应作为停止依据并报告。不要为凑数量持续扩张列表。

## 4. 长期查询与画像

只有需要保存常用检索式或执行具名查询时才使用 `config/profile.toml`。一次性检索不因缺画像而中断，也不据此改写用户的研究方向配置。

需要画像且 `profile show` 返回 `no_profile` 时，从用户目标和已验证的查询起草方向名、`all_of` / `any_of`、可选载体与回看窗口；优先给可用草稿，缺少关键范围时再询问。按用户已授权的配置范围新建或修改文件，**改完必跑 `profile check`**，回显实际解析结果。CLI 不写配置；字段与模板见 CLI 参考 §9。

`sync` 与 `search` 按相同检索式共用新鲜度账，不因换命令自动重新取数。`sync` 的清单在 `data/runs/<run_id>/run.json`，人读同目录 `report.md` 或 `data/latest-report.md`；信封只返回各 `runs[]` 的状态与成本，细节见 CLI 参考 §4。

## 5. 结果诊断与失败处理

stdout 是 JSON 信封 `{ok, command, schema, data|error}`，日志走 stderr。优先看 `ok` 和载荷；退出码 `0` / `1` 对应有产出 / 无产出。参数解析或启动器失败可能没有信封，先修参数或环境，不能套用源失败重试。

- **本地未命中**：只说明现有语料未命中。用户需要外部文献且允许联网时，用 `search --refresh auto` 取数；已经联网仍空时，说明本次来源与检索式未找到匹配，不能推断“无人研究”，也不凭记忆补成已查证结果。
- **列表少于预期**：读 `data.counts.local`；`truncated` 表示候选预算耗尽，`venue_filtered_out` 表示文本命中但载体不符，`matched` 与 `returned` 区分匹配数和展示数。`data.items` 查的是本地库，条数不等于本次新增数。
- **部分源失败**：`counts.failures[]` 非空时仍可能 `ok=true`，使用成功结果并说明缺失来源，不能把失败或跳过的来源报告成已完成检索。
- **`degraded`**：读 `reason` 并结合 `network`、`cost.requests`；`sync` 看各 `runs[].refreshed`、状态与成本。新鲜度命中是正常复用，只需说明本次未刷新；额度不足可能整次跳过，也可能已经完成部分来源。不要统一说“只查本地”，也不要因该标记重复请求或绕过额度限制。
- **`ok=false`**：仅 `busy`、`source_error`、`network_error`、`rate_limited` 在原因允许恢复时最多重试一次；缺依赖、错误参数等确定性问题先修原因。`data_not_writable` 需换可写根目录，重复执行无用。错误码细节见 CLI 参考 §10。

## 6. 交付与证据

- **每篇有出处和推荐理由**：给标题、`published_at`（有日期差异时注明口径）、可点击的 DOI / arXiv 链接与来源名；两种外部标识都缺时保留 `work_key`。依据 `sources[]` 说明记录来源，不把已有 arXiv 成员误报成本次已执行 arXiv 检索。完整 `sources[]` 可留在结构化清单，不必把 JSON 原样堆进正文。
- **领域导读回答为什么读、怎么读**：按问题或方法解释经典的基础作用、前沿的研究问题、与用户场景的关系及阅读顺序。应用迁移与选题建议明确标为推断，不能把检索未命中当成创新性证明。
- **证据深度与断言一致**：`search --abstract` 是摘要片段，`lookup` 带完整摘要但可能为空；缺摘要不等于源失败。摘要可能来自预印本或 OpenAlex 倒排索引还原，逐字引用或翻译前核对原始页面。依据摘要概括时如实标注；性能比较、方法细节或创新性判断需要相应原文证据，不能暗示已精读或复现。
- **核对身份、日期与发表状态**：DOI 与题名不一致时先核查；`published_at`、arXiv 上传日和会议年份可能不同。涉及“最新”“首次提出”或录用状态时回到出版商、会议或 arXiv 页面确认。带 arXiv DOI 不等于已接收，会议投稿不等于录用。
- **版本与排除**：`dup_suspect=true` 时说明疑似重复，避免把同篇不同版本当成独立贡献或合计被引；`is_retracted=true` 的论文不推荐。来源之外的网页发现可作为补充线索，用可用标识经 CLI 核查，尚未入库的线索单独注明。
- **交代实际范围与限制**：说明检索日期、来源、时间范围和重要缺口；按需要汇报 `cost` 并保留 `basis` 的估算 / 实测含义。简短查询直接回答，只有用户需要留档或结果较多时才另存报告。

## 7. 环境要求与未接线

- **Python 与可写目录**：需要 Python `>=3.11`，可用 `ACADEMIC_SEARCH_PYTHON` 指定真实解释器，启动器会探测并回退。库采用 SQLite WAL，离线读取也需要可写根目录。首次使用或环境改变时，可用 `search --refresh false --limit 1` 自检；`version` / `profile` 成功不证明库可写。
- **凭据**：OpenAlex 使用环境变量 `OPENALEX_API_KEY`，只检查是否存在，不打印或写入凭据；arXiv 不需要 key。鉴权与额度细节按需读 [OpenAlex 参考](references/openalex-fields.md) §2。
- **能力边界**：`search`、`lookup`、`download`、`sync`、`profile`、`verify`、`version` 已接线；`read`、`digest`、`export` / `bundle`、`promote`、`graph` 尚未接线，不能通过调用这些命令声称完成精读、摘要缓存或导出。初版不接 Zotero。
- **转换依赖**：检索与原件下载无需 pandoc。转换链按 `ACADEMIC_SEARCH_PANDOC`（可执行文件）→ 仓库 `vendor/pandoc/pandoc.exe`（POSIX 为 `pandoc`）→ `PATH` 查找；可从 [pandoc 官方发布页](https://github.com/jgm/pandoc/releases/latest) 下载对应平台压缩包，解出可执行文件放入上述位置。转换链存在不代表 `read` 已接线。

## 8. 按需参考

- [CLI 参考](references/cli-reference.md)：具体命令的参数与示例、载荷、错误码和画像字段；执行不熟悉的命令前读对应小节。
- [OpenAlex 参考](references/openalex-fields.md)：需要判断覆盖、被引、身份、日期与计费时读。
- [arXiv 参考](references/arxiv-syntax.md)：选择 arXiv 来源、调整检索语法、处理日期窗与限流时读；该源关键词查摘要，不能假定只出现在标题中的词也会命中。
