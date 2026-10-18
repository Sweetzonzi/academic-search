---
name: academic-search
description: 从 arXiv 与 OpenAlex 或本地缓存查找学术论文，可按主题、作者、会议或期刊、年份、被引下限批量检索，可按 DOI / arXiv ID / 标题抽取单篇，结果一律带出处。用户问「这个方向有没有人做过」「最近有什么新论文」「这个方向的经典 / 高被引论文」「某个会议或期刊上有哪些相关工作」「本地库里现在有哪些论文」「某篇的 DOI / 摘要 / 引用」时用它。
agent_created: true
---

# academic-search

一句话：从 arXiv 与 OpenAlex 查找论文并把结果缓存到本地库，按主题找一批、按标识取一篇、看已经缓存了哪些。结果一律带出处。

**它是什么**：一个命令行工具，把外部来源的论文元数据取回本地库，再由本地库提供检索。可做的事分五类：

- **按主题批量检索**：给定关键词，可以限定作者、会议或期刊、年份，也可以只保留被引量高于某个阈值的经典文献。
- **按标识取单篇**：DOI、arXiv ID 或标题任选其一，返回该篇的元数据与完整摘要。
- **把某篇的原件取到本地**：按 `work_key` 下载 LaTeX 源码包 / arXiv HTML / PDF，存进本地库并留一份可读的投影。
- **列出本地已有条目**：不联网，把库里现成的内容列出来。
- **读取与校验配置**：`config/profile.toml` 保存常用检索式，`profile` 子命令读取、校验或生成模板；另有产物抽样校验与版本查询。

**怎么用**：入口是 `scripts/run.cmd`（Windows）与 `scripts/run.sh`（POSIX），见 §1。调用形式是「一条命令 + 若干选项」，stdout 是一个 JSON 信封（`{ok, command, schema, data|error}`），日志走 stderr；退出码 `0` = 有产出、`1` = 没有产出。选项、载荷字段与错误码见 `references/cli-reference.md`，命令清单与各命令的接线状态见 §3。

**三条贯穿全局的性质**

1. **结果一律来自本地库**：联网只负责把新结果写进库，检索本身不直接返回网络响应。因此同一检索式在新鲜度窗口内不重复取数，重复提问不产生额外开销。
2. **每条结果都带出处**：`doi` / `arxiv_id` / `work_key`，另有 `sources[]` 说明该条由哪几条来源记录合成、各自扮演什么角色。
3. **联网是可选的**：默认只在库内数据过期时才联网，也可以要求强制刷新或完全离线。

**尚未实现的部分**：不接 Zotero，不调用任何 LLM；`read` / `digest` / `export` 等子命令尚未接线（见 §7）。文中标「实测」的结论是实际运行验证过的，不是从上游文档推断的。

## 1. 调用入口

    skills/academic-search/scripts/run.cmd  <命令> [参数...]     # Windows
    skills/academic-search/scripts/run.sh   <命令> [参数...]     # POSIX

- 仓库根解析顺序：`--root` 参数 → 环境变量 `ACADEMIC_SEARCH_ROOT` → 本 bundle 的 `root.txt` → 本 bundle 的上两级目录（安装进仓库时就是这个）。
- `--root` 是**全局选项，必须写在子命令前面**（`--root <路径> search ...`）；写在子命令后面 argparse 不认，退出码 2、无信封（实测）。
- **stdout 是一个 JSON 信封**（`{ok, command, schema: 1, data|error}`），日志走 stderr。退出码 0 = 这次有产出、1 = 没有产出。**判成败看 `ok`，不要去解析 stderr。**

## 2. 决策树

| 用户想要 | 命令 |
|---|---|
| 问一个具体问题、要一批论文 | `search --all-of` / `--any-of` 给一组词 |
| 看**本地库里**已经有什么（不联网、零成本） | `search` 不给检索式 + `--refresh false` |
| 拿到确定的某一篇 | `lookup --doi` / `--arxiv` / `--title` 三选一 |
| 某个方向的最新进展 | `sync`（按 `config/profile.toml` 里的具名查询跑，产出报告文件）；一次性的临时检索用 `search --refresh true` |
| 某个方向**最新**的高被引论文 | `search --min-citations N`（或 `--min-fwci X` 跨领域比）。这两个是**取数条件**，会多花一次请求，默认按日期取数，所以带回的是「最新的、被引超过阈值的一批」 |
| 某个方向的**经典**（全局被引头部） | 在上面基础上再加 `--sort citations`。它排的是**全局**被引、不是相关性，头部会有跨领域命中，必须逐条复核主题（§6 第 6 条） |
| **只在某个会议 / 期刊上**找 | `search --venue ICRA` 这样给名字（会议与期刊都可以，如 `IEEE T-RO` / `JAAMAS` / `AAMAS`）；清单在 `src/academic_search/venues.toml`（16 条），**解析不到会报错**。给了它会跳过 arXiv 腿。**它只匹配已入库记录、只认 OpenAlex 的载体指派**：经典论文常只有预印本记录，会被滤掉（`counts.local.venue_filtered_out` 会数出来）；要经典还要叠加 `--sort citations`，见 `references/cli-reference.md` §1 的坑 |
| 要把某篇的**原文**拿到本地（PDF / LaTeX 源码 / arXiv HTML） | `download --work <work_key> --source auto\|pdf\|all`，见 §3 |
| `config/profile.toml` 能不能用 | `profile check` |

**两条命令的取数顺序不同，不要用一句话概括**：`search` 是「按新鲜度决定要不要联网刷新 → 查本地库」，所以 `--refresh false` 也能返回库里已有的东西；`lookup` 是「先查本地库，没命中才联网」。两条命令的结果**都来自本地库**。

**但 `--refresh true` 在两条命令上是同一件事：先刷新，再查本地。** `lookup` 即使本地已命中也会刷一次——单实体 GET 免费，而它能补上「只以预印本记录入库、被引还是空」的条目。默认档要不要刷才是两者唯一的分歧：`search` 看检索式的新鲜度，`lookup` 只看本地有没有命中。

**失败分支（比选对命令更容易出错的地方）：**

- 本地没有 → 先怀疑检索式太窄，别急着联网；确认后 `search --refresh true`。
- 联网也空 → **如实说「本地库与本次联网都没有」**，不要凭印象补。
- 拿到 `degraded` → 本次只查了本地。**不要重试**，如实告诉用户。
- 用户只给了一句描述、没有 DOI / arXiv ID → 用 `search` 找到条目再 `lookup`，不要拿整句描述去 `lookup --title`（那是短语匹配，不是模糊搜索）。

参数、载荷与坑见 `references/cli-reference.md`。

## 3. 命令总览

| 命令 | 作用 | 状态 |
|---|---|---|
| `search` | 当场给的检索式 → 查库（可选刷新）→ 结果列表 | 可用 |
| `lookup` | 按 DOI / arXiv ID / 标题取确定的一篇 | 可用 |
| `profile show` / `check` / `scaffold` | 读 `config/profile.toml` | 可用 |
| `verify` | 抽样重算 `bytes/` 的 sha256 | 可用 |
| `version` | 版本与 `schema` 版本 | 可用 |
| `sync` | 跑 `config/profile.toml` 里的具名查询 → 联网采集 → 落候选池 → 写报告 | 可用；参数与产物见 `references/cli-reference.md` §4 |
| `download` | 取原始字节（LaTeX 源码包 / arXiv HTML / PDF）→ `bytes/` + `works/<proj>/source.*` | 可用；参数与载荷见 `references/cli-reference.md` §3 |
| `read` / `digest list\|put` / `export` / `bundle` / `promote` / `graph` | 尚未实现 | **未接线**：argparse 不认识这些子命令（退出码 2、无信封）。要新论文用 `search --refresh true`；要原件用 `download` |

**注意**：按名字跑用 `sync`（`search` 只接受当场给的检索式）。两者**共用同一本账本**，判据是检索式字段而不是命令名——字段一致就是同一次检索，会命中同一条新鲜度记录、不重复联网；反过来，某条查询刚被一次字段等价的 `search` 刷过，`sync` 也会跳过它并在 `degraded` 里说明。

## 4. 没有 `config/profile.toml` 时怎么办

`profile show` 返回 `error.code=no_profile` 就进这个流程：

1. **先出草稿，不要发问卷**——从本地库已有的主题、当前对话里用户说过的话、必要时一次公开检索试探，拼出初稿。
2. 把初稿逐条摆给用户确认增删：方向名、`all_of` / `any_of`、venue 白名单、回看窗口。
3. 写 `config/profile.toml` → `profile check` → 把解析结果回显给用户。

**CLI 不写配置**：新建与修改都由你直接写文件，**改完必跑 `profile check`**。用户随时可以要求修改，改文件重跑即可，没有别的通道。字段表与示例见 `references/cli-reference.md` §9。

## 5. 读信封与退出码

- `data.items[]`：结果条目。**答用户时至少给 `title` + `published_at` +（`doi` 或 `arxiv_id`）+ `sources[]`。**
- `data.items[].abstract`：**按需出现**——`lookup` 固定带完整摘要；`search` 只有开了 `--abstract` 才带截断版（长度走 `config/search.toml`）。要引用摘要原文或照它翻译前，先读 `references/cli-reference.md` §1 与 §8：**它可能来自预印本稿，或者是 OpenAlex 倒排索引的还原产物**，载荷不带标记。
- `data.network`：本次有没有发过请求；`data.cost`：`{usd, basis, requests}`（`basis` 是计费口径）。
- `data.counts.failures[]`：部分源失败。**部分失败不算失败**：`ok` 仍为 true、退出码 0，如实汇报哪个源没成。
- `search` 的 `data.counts.local`：本地漏斗 `{considered, matched, returned, truncated, venue_filtered_out}`。返回条数少于 `--limit`、或带 `--venue` 时结果很少，先看它——`truncated` 是候选预算用尽（可能还有没看的匹配），`venue_filtered_out` 是文本命中但载体不含白名单的条数。
- `data.degraded.reason`：本次降级了（余额不足只查本地、新鲜度跳过联网、或 `--title` 配 `--refresh true` 时只能查本地）。**降级不是错误，不要重试。**
- **`sync` 的信封不一样**：`data.runs[]` 逐 run 自包含（没有调用级 `counts` / `cost` 合计），而且**不返回结果清单**——清单在 `data/runs/<run_id>/run.json` 里，人读同目录的 `report.md` 与 `data/latest-report.md`。要点见 `references/cli-reference.md` §4。
- `data.items[].dup_suspect = true`：疑似同一篇但**未合并**，引用时必须说明。
- `data.items[].is_retracted = true`：撤稿，不要推荐。
- **退出码**：`0` = 有产出，`1` = 没有产出（细分看 `error.code`）。**参数写错是例外**：argparse 直接退出，退出码 2、stderr 打 usage、没有 JSON 信封——别把「非 0 就一定有信封」当前提。
- `ok=false` 时看 `error.code`：可重试的只有 `busy` / `source_error` / `network_error` / `rate_limited`，且**只重试一次**；其余按 `error.message` 修（完整错误码表见 `references/cli-reference.md` §10）。
- `error.code=data_not_writable`：数据目录不可写，**换目录或改 `ACADEMIC_SEARCH_ROOT`**，重试无用。`version` / `profile` 不碰库会照常成功，所以它们通过**不代表**能检索——环境自检以一次 `search --refresh false` 为准。

## 6. 硬规则

1. **结论必须带出处**：没有 `doi` / `arxiv_id` / `work_key` 的断言不要输出。召回为空就说「本地库与本次联网都没有」，不要凭印象补。
2. **`--refresh` 三值写全**，不要裸写一个 `--refresh`。
3. **降级不重试、失败只重试一次**；`degraded` 出现时如实告诉用户「本次只查了本地」。
4. **不要直接改 `data/`**：落库由命令负责。
5. **本版本不接 Zotero**。
6. **「经典」不等于「被引高」**：`--min-citations` 与 `--sort citations` 排的是**全局**被引，头部会出现跨领域命中，也可能是分词造成的假命中（`small-world models` 含子串 `world model`）。给用户「这个方向的经典」前必须**逐条复核主题相关性**，并说明这批是按被引排的、不是按相关性排的；`--min-fwci` 还会整批排除没有 `fwci` 的最新论文。细则见 `references/cli-reference.md` §1。
7. **短词（`dreamer` / `planet` / `world`）要配领域词**：短词 + `--sort citations` 的头部是跨领域高被引（实测 `dreamer` 的前两条是 EEG 数据集与情感识别网络），单用 `--min-citations` 也一样。给短词时加一条 `--all-of` 收窄，或换成多词短语。**不要**用「只匹配标题」当解药：同名词照样在标题里，而真正相关的那篇标题反而没有它。

## 7. 环境要求与未接线

- **需要一个可用的 Python**（`>=3.11`，用到标准库 `tomllib`）。启动器按候选顺序找解释器并**逐个探测**：先看 `ACADEMIC_SEARCH_PYTHON`，不可用则回退；Windows 再试 `python`、`py`，POSIX 再试 `python3`、`python`。探测方式是执行 `import sys`，失败就换下一个；**同一个名字在 PATH 里有多个命中时会逐个试**（应用商店的 `python.exe` 存根常排在真解释器前面）。全部失败才退出码 1、**不产出 JSON 信封**。这属于环境问题、不是源失败。
- **仓库根必须可写**：`data/`、`bytes/`、`works/` 都要能写。sidecar 用 WAL，**读也要建 `-wal` / `-shm`**，所以只读挂载上连 `search --refresh false` 都会报 `data_not_writable`（不是只有刷新会失败）。修法：把仓库放到可写位置，或把 `ACADEMIC_SEARCH_ROOT` 指到可写目录。`version` / `profile` 不碰库，会照常成功，**不能当环境自检**——自检用一次 `search --refresh false`。
- **OpenAlex 需要 API key**：匿名档的每日额度按出口 IP 共享、很容易耗尽（额度用尽时返回 429）。要用 OpenAlex，先把 key 放进环境变量 `OPENALEX_API_KEY`；只用 `--source arxiv` 则不需要任何凭据。
- **转换链需要一个 pandoc**：把 LaTeX 源码或 arXiv HTML 转成 Markdown（唯一消费者 `read` 尚未接线，见本节末）。可执行文件单件约 220 MB（官方压缩包约 40 MB），**不进版本库**，故按下面顺序找：环境变量 `ACADEMIC_SEARCH_PANDOC`（指向可执行文件本身）→ 仓库根的 `vendor/pandoc/pandoc.exe`（POSIX 上是 `pandoc`）→ `PATH`。装法：从官方下载页 `https://github.com/jgm/pandoc/releases/latest` 取 `pandoc-<版本>-windows-x86_64.zip` 或 `pandoc-<版本>-linux-amd64.tar.gz`（静态链接，解出即用），把可执行文件放进 `vendor/pandoc/`；或用包管理器（`winget install --source winget --exact --id JohnMacFarlane.Pandoc`、`choco install pandoc`、`brew install pandoc`）。**换 pandoc 版本会让已转好的正文按过期处理**，要重转一次；缺它时报 `source_error`、不产出正文。
- 结果里每条 `sources[].source` 说明该项来自哪个源、扮演什么角色（`preprint` / `primary` / `published`）。
- 未接线：`read` / `digest list|put` / `export` / `bundle`；`promote` / `graph` 同样尚未实现。
- `download` 只从 **arXiv** 取原件：「要原文」对非 arXiv 记录会报 `source_error`。

## 8. references（按需读，不要默认加载）

- `references/cli-reference.md`：**要某个命令的参数、坑、载荷字段或错误码时读**。含 `config/profile.toml` 的字段表与示例。
- `references/openalex-fields.md`：OpenAlex 的覆盖范围、计费与字段事实，以及几个会咬人的坑。
- `references/arxiv-syntax.md`：arXiv 检索语法、日期窗的限制、限流，以及本 CLI 的参数怎么映射过去。
