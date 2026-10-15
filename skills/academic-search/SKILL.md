---
name: academic-search
description: 本地优先的文献检索缓存层。当用户问「这个方向有没有人做过」「找某篇论文」「最近有什么新论文」「本地库里现在有哪些论文」「某篇的 DOI / 摘要 / 引用」时使用：先在本地库里查，缺了才联网 arXiv / OpenAlex，结果一律带出处。
---

# academic-search

一句话：**本地优先的文献检索缓存层**。查询先在本地库里找，缺了才联网；联网拿到的东西全部落库，重复的检索式不重复计费。

本 skill 是**薄包装**：只有这份说明、`references/` 与一个 launcher。CLI 实现、`config/`、`data/` 都在仓库里，由 launcher 指过去。命令与载荷的**契约**在仓库 `docs/03-接口契约.md` 与 `docs/接口/输出载荷.md`；本文是给 agent 的用法索引，凡标「实测」的都是在本机跑出来的。

## 1. 调用入口

    skills/academic-search/scripts/run.cmd  <命令> [参数...]     # Windows
    skills/academic-search/scripts/run.sh   <命令> [参数...]     # POSIX

- 仓库根解析顺序：`--root` 参数 → 环境变量 `ACADEMIC_SEARCH_ROOT` → 本 bundle 的 `root.txt` → 本 bundle 的上两级目录（安装进仓库时就是这个）。
- `--root` 是**全局选项，必须写在子命令前面**（`--root <路径> search ...`）；写在子命令后面 argparse 不认，退出码 2、无信封（实测）。
- Python 解释器：环境变量 `ACADEMIC_SEARCH_PYTHON`；未设则用 PATH 上的 `python`（POSIX 上 `python3`）。
- **stdout 是一个 JSON 信封**（`{ok, command, schema: 1, data|error}`），日志走 stderr。退出码 0 = 这次有产出、1 = 没有产出。**判成败看 `ok`，不要去解析 stderr。**

## 2. 命令总览

| 命令 | 作用 | 本机状态（实测 2026-10-16） |
|---|---|---|
| `search` | 当场给的检索式 → 查库（可选刷新）→ 结果列表 | 可用 |
| `lookup` | 按 DOI / arXiv ID / 标题取确定的一篇 | 可用 |
| `profile show` / `check` / `scaffold` | 画像的只读通道 | 可用 |
| `verify` | 抽样重算 `bytes/` 的 sha256 | 可用 |
| `version` | 版本与 `schema` 版本 | 可用 |
| `sync` / `textify` / `read` / `digest list\|put` / `export` / `bundle` / `promote` / `graph` | 契约已定、实现未到 | **未接线**：argparse 不认识这些子命令（退出码 2、无信封）。现在要新论文用 `search --refresh true` |

## 3. 场景速查

| 用户想要 | 命令 | 关键参数 |
|---|---|---|
| 问一个具体问题、要一批论文 | `search` | `--all-of` / `--any-of` 给一组（都省略 = 列全部本地库，见 §4） |
| 看**本地库里**已经有什么（不联网、零成本） | `search` | 不给检索式 + `--refresh false` |
| 拿到确定的某一篇 | `lookup` | `--doi` / `--arxiv` / `--title` 三选一 |
| 某个方向的最新进展 + 可归档报告 | `sync` | `--query <具名查询>`（**尚未接线**，见 §11） |
| 要某篇的全文 Markdown | `textify`（**尚未接线**，见 §11） | — |
| 画像能不能用 | `profile check` | 只读；CLI 从不写配置 |

**术语要分清**：**具名查询**（named query）指 `config/profile.toml` 里的一条 `[[query]]`（名字 + 检索式 + 年份窗），**不是**「按标题查论文」——后者是 `lookup --title`。

**`search` 不接受按名字跑画像查询**（`search` 只收当场给的检索式）。要把画像里的检索式拿来用，就把它的字段原样抄到命令上——`spec_hash` 只由那几个字段决定，抄全了就是同一次检索、命中同一本 run 账本。看「某个方向最近有什么」的契约动作是 `sync --query <名>`（采集 + 报告）配合 `search --refresh false`（读清单），但 `sync` 未接线，**现阶段联网只有 `search --refresh true` 这一条路**。

## 4. `search`：全部参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--all-of 词` | — | 可重复；组内 **AND**（交集） |
| `--any-of 词` | — | 可重复；组内 **OR**（并集） |
| `--venue 名` | — | 可重复；本地按 venue 过滤。arXiv 记录没有 venue，这条对它们不生效 |
| `--author 名` | — | 可重复 |
| `--since-years N` | — | 等价于 `published_at >= 今天 − N×365 天`（实测）；`0` 只留「今天之后」，通常为空 |
| `--limit N` | `20` | 计**合并去重后的 work 数**；`0` = 空列表 |
| `--source 源` | `sources.toml` 里启用的（当前 `openalex`、`arxiv`） | 可重复；只在启用集内收窄。**不在集合里的名字被静默丢掉**——实测 `--source crossref`、`--source nosuch` 都等于不过滤 |
| `--refresh auto\|true\|false` | `auto` | **三值必须显式写全**；见 §8 |
| `--max-age 小时` | `search.toml` 的 `24` | 只对 `--refresh auto` 生效 |
| `--in meta\|body\|both` | `meta` | `body` / `both` 扫 `works/`；当前没有投影，效果等于 `meta` |
| `--sort date\|citations` | `date` | `date` = `published_at` 降序；`citations` = `cited_by_count` 降序，无值的排后 |

**用法清单（每条都实测过）：**

```powershell
# a) 列本地库全部：不给任何检索式 + --refresh false（零联网、零成本）
scripts/run.cmd search --refresh false --limit 200

# b) 按检索式查本地（同一检索式在新鲜度窗口内也跳过联网）
scripts/run.cmd search --all-of "world model" --refresh false

# c) 默认档：先本地、必要时联网，跳过刷新时在 degraded 里说明
scripts/run.cmd search --any-of drone --limit 10 --refresh auto

# d) 强制联网取新（OpenAlex 匿名档会 429，稳的是 --source arxiv）
scripts/run.cmd search --all-of "world model" --any-of drone --source arxiv --limit 10 --refresh true

# e) 排序（引用数降序）
scripts/run.cmd search --any-of "multi-agent" --sort citations --limit 20 --refresh false
```

**坑（实测）：**

- **不给检索式 = 列全部本地库**：空检索式跳过 FTS 过滤，返回本地所有 `live` work（条数受 `--limit` 限制）。**这一档只配 `--refresh false`**；配 `auto` / `true` 等于拿空检索式去联网。清单粒度与分页见仓库 `docs/决策/ADR-0057.md`：只保证可溯源最小集、摘要不进载荷、不做分页。
- **不要给空关键词**：`--all-of ""` 实测 `ok=false`、`error.code=internal_error`（FTS5 语法错误）。要列全部就别带这个词。
- `--limit` 数的是 **work**，不是 record：一篇由 arXiv + OpenAlex 两条记录合成的只算 1。
- **`--refresh false` 也会写一条 run 台账**（`refreshed=0`、`requests=0`、`counts={}`）；`lookup` 的离线命中不写。两者都不发网络请求。

## 5. `lookup`：取确定的一篇

| 参数 | 默认 | 说明 |
|---|---|---|
| `--doi` / `--arxiv` / `--title` | — | **必须给一个**（一个都不给 → `ok=false`、`error.code=invalid_args`）；同时给多个时优先级 `--doi` > `--arxiv` > `--title`（实测） |
| `--limit N` | `20` | 只影响 `--title` 的候选池大小 |
| `--refresh auto\|true\|false` | `auto` | 同上；**只有本地未命中且给的是 `--doi` / `--arxiv` 时才联网**，`--title` 不联网 |

```powershell
scripts/run.cmd lookup --arxiv 2610.18808 --refresh true    # 命中即返回，最省钱
scripts/run.cmd lookup --doi 10.1109/mcse.2011.37 --refresh false
scripts/run.cmd lookup --title "Skytopia Monocular Drone Navigation" --refresh false
```

- `--title` 走 FTS5 **短语**匹配（标题 + 摘要），取第一条命中，不是模糊排序。
- 本地未命中且 `--refresh false` → `ok=false`、`error.code=not_found`（退出码 1）。换标识，或先 `search` 联网取。
- `--doi` 入参会归一化，粘 `https://doi.org/...` 形式照样命中。

## 6. `profile`：画像的只读通道

| 子命令 | 作用 | 载荷 |
|---|---|---|
| `profile show` | 回显 `config/profile.toml` 里的查询 | `data.queries[]` |
| `profile check` | 解析、校验、查重 | 成功 `data.queries[]`；失败 `error.code=profile_invalid`，`error.message` 逐条带行号 |
| `profile scaffold` | 吐一份带注释的模板 | `data.template` |

- **CLI 从不写配置**：新建与修改画像由 agent 直接编辑 `config/profile.toml`，改完跑 `profile check`。
- 查询字段（`name` / `all_of` / `any_of` / `venues` / `authors` / `since_years` / `expires` / `enabled`）的家在仓库 `docs/使用/配置.md`。**每条 query 至少给 `all_of` 或 `any_of` 一组**，这是画像的硬校验，实测 `profile check` 会报错——与 §4 的 `search` 空检索式不是一回事。

## 7. `verify` 与 `version`

- `verify [--sample N]`：抽样重算 `bytes/` 里文件的 `sha256` 并与文件名比对。`--sample` 默认 20，**`0` = 全量**；只读、不修复；发现不匹配即 `error.code=io_error`、退出码 1 并列出文件。当前 `data/` 下没有 `bytes/`，所以它是空跑（`mismatches: []`）。
- `version`：`data = {version, schema}`，自检用。

## 8. `--refresh` 三值

`--refresh` **必须显式写出三值之一**：

| 取值 | 含义 |
|---|---|
| `auto`（默认） | 同一检索式在 `refresh.max_age_hours`（默认 24 小时）内查过就跳过联网，并在 `data.degraded.reason` 里说明 |
| `true` | 强制联网 |
| `false` | **绝不联网**，只读本地库 |

判据是**检索式**（`spec_hash`），不是全局时间：换个关键词就是另一次检索。只有 `refreshed=1` 且 `status=ok` 的 run 才算「新鲜」，所以离线查询不会把 `auto` 锁死。

## 9. 读信封与退出码

- `data.items[]`：结果条目。**答用户时至少给 `title` + `published_at` +（`doi` 或 `arxiv_id`）+ `sources[]`。**
- `data.network`：本次有没有发过请求；`data.cost`：`{usd, basis, requests}`（`basis` 是计费口径）。
- `data.counts.failures[]`：部分源失败。**部分失败不算失败**：`ok` 仍为 true、退出码 0，如实汇报哪个源没成。
- `data.degraded.reason`：本次降级了（余额不足只查本地，或新鲜度跳过联网）。**降级不是错误，不要重试。**
- `data.items[].dup_suspect = true`：疑似同一篇但**未合并**，引用时必须说明。
- `data.items[].is_retracted = true`：撤稿，不要推荐。
- **退出码**：`0` = 有产出，`1` = 没有产出（细分看 `error.code`）。**参数写错是例外**：argparse 直接退出，退出码 2、stderr 打 usage、没有 JSON 信封——别把「非 0 就一定有信封」当前提。
- `ok=false` 时看 `error.code`：可重试的只有 `busy` / `source_error` / `network_error` / `rate_limited`，且**只重试一次**；其余按 `error.message` 修（`no_profile` / `profile_invalid` 去修画像，`not_found` 换标识或先 `search`，`projection_missing` 先 `textify`，`io_error` 查路径与权限）。

## 10. 硬规则

1. **结论必须带出处**：没有 `doi` / `arxiv_id` / `work_key` 的断言不要输出。召回为空就说「本地库与本次联网都没有」，不要凭印象补。
2. **`--refresh` 三值写全**，不要裸写一个 `--refresh`。
3. **降级不重试、失败只重试一次**；`degraded` 出现时如实告诉用户「本次只查了本地」。
4. **不要直接改 `data/`**：落库由命令负责。配置由 agent 直接编辑 `config/profile.toml`（CLI 只读校验，不写配置）。
5. **本版本不接 Zotero**：「入库只能进专用 collection 且先 `--dry-run`」是 M5 起的规则。

## 11. 本机限制与未接线（实测 2026-10-16）

- **OpenAlex 匿名档不可用**：无 key 的每日额度按出口 IP 共享且随时耗尽，实测 429。今天唯一稳的路径是 `--source arxiv`；要用 OpenAlex 先配 `OPENALEX_API_KEY`。
- 结果里每条 `sources[].source` 会告诉你它来自哪个源、扮演什么角色（`preprint` / `primary` / `published`）。
- 未接线：`sync` / `textify` / `read` / `digest list|put` / `export` / `bundle`（见 §2）；`promote`、`graph`、`--no-note` 按契约顺延到 M4 / M5。

## 12. references（按需读，不要默认加载）

- `references/openalex-fields.md`：OpenAlex 的覆盖范围、计费与字段事实，以及几个会咬人的坑。
- `references/arxiv-syntax.md`：arXiv 检索语法、日期窗的实测限制、限流，以及本 CLI 的参数怎么映射过去。
