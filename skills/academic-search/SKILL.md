---
name: academic-search
description: 本地优先的文献检索缓存层。本 skill 用于先在本地库里查、缺了才联网 arXiv / OpenAlex、结果一律带出处的检索场景。当用户问「这个方向有没有人做过」「找某篇论文」「最近有什么新论文」「这个方向的经典 / 高被引论文」「某个会议或期刊上有哪些相关工作」「本地库里现在有哪些论文」「某篇的 DOI / 摘要 / 引用」时应使用它。
agent_created: true
---

# academic-search

一句话：**本地优先的文献检索缓存层**。查询先在本地库里找，缺了才联网；联网拿到的东西全部落库，重复的检索式不重复计费。

本 skill 是**薄包装**：只有这份说明、`references/` 与一个 launcher。CLI 实现、`config/`、`data/` 都在仓库里，由 launcher 指过去。命令与载荷的契约在仓库 `docs/03-接口契约.md` 与 `docs/接口/输出载荷.md`；本文自包含，凡标「实测」的都是在本机跑出来的。

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
| 某个方向的最新进展 | 现阶段只有 `search --refresh true`（`sync` 未接线） |
| 某个方向的**经典 / 高被引**论文 | `search --min-citations N`（或 `--min-fwci X` 跨领域比）；这两个是**取数条件**，会多花一次请求，但带回的是「被引最高的一批」 |
| **只在某个会议 / 期刊上**找 | `search --venue ICRA` 这样给名字（会议与期刊都可以，如 `IEEE T-RO` / `JAAMAS`）；清单在 `src/academic_search/venues.toml`（15 条），**解析不到会报错**。给了它会跳过 arXiv 腿 |
| `config/profile.toml` 能不能用 | `profile check` |

**失败分支（比选对命令更容易出错的地方）：**

- 本地没有 → 先怀疑检索式太窄，别急着联网；确认后 `search --refresh true`。
- 联网也空 → **如实说「本地库与本次联网都没有」**，不要凭印象补。
- 拿到 `degraded` → 本次只查了本地。**不要重试**，如实告诉用户。
- 用户只给了一句描述、没有 DOI / arXiv ID → 用 `search` 找到条目再 `lookup`，不要拿整句描述去 `lookup --title`（那是短语匹配，不是模糊搜索）。

参数、载荷与坑见 `references/cli-reference.md`。

## 3. 命令总览

| 命令 | 作用 | 本机状态（实测 2026-10-16） |
|---|---|---|
| `search` | 当场给的检索式 → 查库（可选刷新）→ 结果列表 | 可用 |
| `lookup` | 按 DOI / arXiv ID / 标题取确定的一篇 | 可用 |
| `profile show` / `check` / `scaffold` | 读 `config/profile.toml` | 可用 |
| `verify` | 抽样重算 `bytes/` 的 sha256 | 可用 |
| `version` | 版本与 `schema` 版本 | 可用 |
| `sync` / `textify` / `read` / `digest list\|put` / `export` / `bundle` / `promote` / `graph` | 契约已定、实现未到 | **未接线**：argparse 不认识这些子命令（退出码 2、无信封）。现在要新论文用 `search --refresh true` |

**术语**：**具名查询**指 `config/profile.toml` 里的一条 `[[query]]`（名字 + 检索式 + 年份窗），**不是**「按标题查论文」——后者是 `lookup --title`。`search` 不接受按名字跑具名查询；要把那条拿来用，就把它的字段原样抄到命令上——`spec_hash` 只由会改变远端请求的那几个参数决定（`all_of` / `any_of` / `venues` / `authors` / `since_years` / `--source` / `--sort` / 两个阈值），抄全了就是同一次检索、命中同一本 run 账本。

## 4. 没有 `config/profile.toml` 时怎么办

`profile show` 返回 `error.code=no_profile` 就进这个流程：

1. **先出草稿，不要发问卷**——从本地库已有的主题、当前对话里用户说过的话、必要时一次公开检索试探，拼出初稿。
2. 把初稿逐条摆给用户确认增删：方向名、`all_of` / `any_of`、venue 白名单、回看窗口。
3. 写 `config/profile.toml` → `profile check` → 把解析结果回显给用户。

**CLI 不写配置**：新建与修改都由你直接写文件，**改完必跑 `profile check`**。用户随时可以要求修改，改文件重跑即可，没有别的通道。字段表与示例见 `references/cli-reference.md` §7。

## 5. 读信封与退出码

- `data.items[]`：结果条目。**答用户时至少给 `title` + `published_at` +（`doi` 或 `arxiv_id`）+ `sources[]`。**
- `data.items[].abstract`：**按需出现**——`lookup` 固定带完整摘要；`search` 只有开了 `--abstract` 才带截断版（长度走 `config/search.toml`）。要引用摘要原文或照它翻译前，先读 `references/cli-reference.md` §1 与 §6：**它可能来自预印本稿，或者是 OpenAlex 倒排索引的还原产物**，载荷不带标记。
- `data.network`：本次有没有发过请求；`data.cost`：`{usd, basis, requests}`（`basis` 是计费口径）。
- `data.counts.failures[]`：部分源失败。**部分失败不算失败**：`ok` 仍为 true、退出码 0，如实汇报哪个源没成。
- `data.degraded.reason`：本次降级了（余额不足只查本地，或新鲜度跳过联网）。**降级不是错误，不要重试。**
- `data.items[].dup_suspect = true`：疑似同一篇但**未合并**，引用时必须说明。
- `data.items[].is_retracted = true`：撤稿，不要推荐。
- **退出码**：`0` = 有产出，`1` = 没有产出（细分看 `error.code`）。**参数写错是例外**：argparse 直接退出，退出码 2、stderr 打 usage、没有 JSON 信封——别把「非 0 就一定有信封」当前提。
- `ok=false` 时看 `error.code`：可重试的只有 `busy` / `source_error` / `network_error` / `rate_limited`，且**只重试一次**；其余按 `error.message` 修（完整错误码表见 `references/cli-reference.md` §8）。

## 6. 硬规则

1. **结论必须带出处**：没有 `doi` / `arxiv_id` / `work_key` 的断言不要输出。召回为空就说「本地库与本次联网都没有」，不要凭印象补。
2. **`--refresh` 三值写全**，不要裸写一个 `--refresh`。
3. **降级不重试、失败只重试一次**；`degraded` 出现时如实告诉用户「本次只查了本地」。
4. **不要直接改 `data/`**：落库由命令负责。
5. **本版本不接 Zotero**。
6. **「经典」不等于「被引高」**：`--min-citations` 与 `--sort citations` 排的是**全局**被引，实测头部有跨领域命中（`small-world models` 这类分词假命中也会有）。给用户「这个方向的经典」前必须**逐条复核主题相关性**，并说明这批是按被引排的、不是按相关性排的；`--min-fwci` 还会整批排除没有 `fwci` 的最新论文。细则见 `references/cli-reference.md` §1。

## 7. 本机限制与未接线（实测 2026-10-16）

- **OpenAlex 匿名档不可用**：无 key 的每日额度按出口 IP 共享且随时耗尽，实测 429。今天唯一稳的路径是 `--source arxiv`；要用 OpenAlex 先配 `OPENALEX_API_KEY`。
- 结果里每条 `sources[].source` 会告诉你它来自哪个源、扮演什么角色（`preprint` / `primary` / `published`）。
- 未接线：`sync` / `textify` / `read` / `digest list|put` / `export` / `bundle`；`promote`、`graph` 按契约顺延到 M4 / M5。

## 8. references（按需读，不要默认加载）

- `references/cli-reference.md`：**要某个命令的参数、坑、载荷字段或错误码时读**。含 `config/profile.toml` 的字段表与示例。
- `references/openalex-fields.md`：OpenAlex 的覆盖范围、计费与字段事实，以及几个会咬人的坑。
- `references/arxiv-syntax.md`：arXiv 检索语法、日期窗的实测限制、限流，以及本 CLI 的参数怎么映射过去。
