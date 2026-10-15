---
name: academic-search
description: 本地优先的文献检索缓存层。当用户问「这个方向有没有人做过」「找某篇论文」「最近有什么新论文」「某篇的 DOI / 摘要 / 引用」时使用：先在本地库里查，缺了才联网 arXiv / OpenAlex，结果一律带出处。
---

# academic-search

一句话：**本地优先的文献检索缓存层**。查询先在本地库里找，缺了才联网；联网拿到的东西全部落库，重复的检索式不重复计费。

本 skill 是**薄包装**：只有这份说明、`references/` 与一个 launcher。CLI 实现、`config/`、`data/` 都在仓库里，由 launcher 指过去。

## 1. 怎么调用

    skills/academic-search/scripts/run.cmd  <命令> [参数...]     # Windows
    skills/academic-search/scripts/run.sh   <命令> [参数...]     # POSIX

- 仓库根解析顺序：`--root` 参数 → 环境变量 `ACADEMIC_SEARCH_ROOT` → 本 bundle 的 `root.txt` → 本 bundle 的上两级目录（安装进仓库时就是这个）。
- Python 解释器：环境变量 `ACADEMIC_SEARCH_PYTHON`；未设则用 PATH 上的 `python`（POSIX 上 `python3`）。
- **stdout 是一个 JSON 信封**（`{ok, command, schema: 1, data|error}`），日志走 stderr。退出码 0 = 这次有产出、1 = 没有产出。**判成败看 `ok`，不要去解析 stderr。**

## 2. 决策树

| 用户想要 | 命令 | 关键参数 |
|---|---|---|
| 问一个具体问题、要一批论文 | `search` | `--all-of` / `--any-of` 至少给一组 |
| 看**本地库里**已经有什么（不联网、零成本） | `search` | `--refresh false` |
| 拿到确定的某一篇 | `lookup` | `--doi` / `--arxiv` / `--title` 三选一 |
| 某个方向的最新进展 + 可归档报告 | `sync` | `--query <具名查询>`（**尚未接线**，见 §6） |
| 要某篇的全文 Markdown | `textify`（**尚未接线**，见 §6） | — |
| 画像能不能用 | `profile check` | 只读；CLI 从不写配置 |

**术语要分清**：**具名查询**（named query）指 `config/profile.toml` 里的一条 `[[query]]`（名字 + 检索式 + 年份窗），**不是**「按标题查论文」——后者是 `lookup --title`。

**`search` 不接受按名字跑画像查询**（`search` 只收当场给的检索式）。要把画像里的检索式拿来用，就把它的字段原样抄到命令上——`spec_hash` 只由那几个字段决定，抄全了就是同一次检索、命中同一本 run 账本。看「某个方向最近有什么」的标准动作是 `sync --query <名>`（采集 + 报告）配合 `search --refresh false`（读清单）。**要联网就用 `sync`**：`search` 带 `--refresh auto|true` 会把那个 `spec_hash` 在账本里置为新鲜，随后的 `sync --query` 就会跳过联网、只拿这一次的结果出报告。

## 3. 三条最常用的调用

```
# 1) 当场检索：默认 auto —— 同一检索式在新鲜度窗口内就跳过联网，并在 degraded 里说明
scripts/run.cmd search --all-of "world model" --any-of drone UAV --limit 10 --refresh auto

# 2) 只查本地库：绝不联网，适合「我之前查到过什么」
scripts/run.cmd search --all-of "world model" --refresh false

# 3) 取确定的一篇：命中即返回，最省钱
scripts/run.cmd lookup --arxiv 2610.18808 --refresh true
scripts/run.cmd lookup --doi 10.1109/mcse.2011.37 --refresh false
```

检索式字段（当场给，语义与画像里的完全一致）：`--all-of`（可重复，交集）/ `--any-of`（可重复，并集）/ `--venue` / `--author` / `--since-years N`；另有 `--limit`（默认 20，计合并后的作品数）、`--source`（默认 `sources.toml` 里启用的全部）、`--sort date|citations`、`--in meta|body|both`。

`--refresh` **必须显式写出三值之一**：

| 取值 | 含义 |
|---|---|
| `auto`（默认） | 同一检索式在 `refresh.max_age_hours`（默认 24 小时）内查过就跳过联网，并在 `data.degraded.reason` 里说明 |
| `true` | 强制联网 |
| `false` | **绝不联网**，只读本地库 |

判据是**检索式**（`spec_hash`），不是全局时间：换个关键词就是另一次检索。

## 4. 读信封

- `data.items[]`：结果条目。**答用户时至少给 `title` + `published_at` +（`doi` 或 `arxiv_id`）+ `sources[]`。**
- `data.network`：本次有没有发过请求；`data.cost`：`{usd, basis, requests}`（`basis` 是计费口径）。
- `data.counts.failures[]`：部分源失败。**部分失败不算失败**：`ok` 仍为 true、退出码 0，如实汇报哪个源没成。
- `data.degraded.reason`：本次降级了（余额不足只查本地，或新鲜度跳过联网）。**降级不是错误，不要重试。**
- `data.items[].dup_suspect = true`：疑似同一篇但**未合并**，引用时必须说明。
- `data.items[].is_retracted = true`：撤稿，不要推荐。
- `ok=false` 时看 `error.code`：可重试的只有 `busy` / `source_error` / `network_error` / `rate_limited`，且**只重试一次**；其余按 `error.message` 修（`no_profile` / `profile_invalid` 去修画像，`not_found` 换标识或先 `search`，`projection_missing` 先 `textify`）。

## 5. 硬规则

1. **结论必须带出处**：没有 `doi` / `arxiv_id` / `work_key` 的断言不要输出。召回为空就说「本地库与本次联网都没有」，不要凭印象补。
2. **`--refresh` 三值写全**，不要裸写一个 `--refresh`。
3. **降级不重试、失败只重试一次**；`degraded` 出现时如实告诉用户「本次只查了本地」。
4. **不要直接改 `data/`**：落库由命令负责。配置由 agent 直接编辑 `config/profile.toml`（CLI 只读校验，不写配置）。
5. **本版本不接 Zotero**：「入库只能进专用 collection 且先 `--dry-run`」是 M5 起的规则。

## 6. 当前可用（本机实测）

| 命令 | 状态 |
|---|---|
| `search` / `lookup` / `profile show\|check\|scaffold` / `verify` / `version` | 可用 |
| `sync` / `textify` / `digest list\|put` / `read` / `export` | **尚未接线**：契约已定，实现未到。现在要新论文就用 `search --refresh true` |

- **OpenAlex 匿名档不可用**：无 key 的每日额度按出口 IP 共享且随时耗尽，实测 429。今天唯一稳的路径是 `--source arxiv`；要用 OpenAlex 先配 `OPENALEX_API_KEY`。
- 结果里每条 `sources[].source` 会告诉你它来自哪个源、扮演什么角色（`preprint` / `primary` / `published`）。

## 7. references（按需读，不要默认加载）

- `references/openalex-fields.md`：OpenAlex 的覆盖范围、计费与字段事实，以及几个会咬人的坑。
- `references/arxiv-syntax.md`：arXiv 检索语法、日期窗的实测限制、限流，以及本 CLI 的参数怎么映射过去。
