# CLI 参考

> 按需加载：**要某个命令的参数、坑、载荷或错误码时才读本文**。SKILL.md 只给决策路径与硬规则。
> 参数签名与默认值的家在 `src/academic_search/cli.py`；本文列的是当前值，二者不一致时以代码为准。
> 凡标「实测」的都是本机跑出来的（2026-10-16）。

---

## 1. `search`

| 参数 | 默认 | 说明 |
|---|---|---|
| `--all-of 词` | — | 可重复；组内 **AND**（交集） |
| `--any-of 词` | — | 可重复；组内 **OR**（并集） |
| `--venue 名` | — | 可重复。**带上它通常会让结果为空**：OpenAlex 那一腿的过滤器名实测 400（部分失败不改变 `ok`），arXiv 那一腿的记录没有 venue、会被本地过滤排除。在 venue 判据落地前，不要用这个参数筛载体 |
| `--author 名` | — | 可重复 |
| `--since-years N` | — | 等价于 `published_at >= 今天 − N×365 天`（实测）；`0` 只留「今天之后」，通常为空 |
| `--limit N` | `20` | 计**合并去重后的 work 数**；`0` = 空列表 |
| `--source 源` | `sources.toml` 里启用的（当前 `openalex`、`arxiv`） | 可重复；只在启用集内收窄。**不在集合里的名字被静默丢掉**——实测 `--source crossref`、`--source nosuch` 都等于不过滤 |
| `--refresh auto\|true\|false` | `auto` | **三值必须显式写全**；见 §4 |
| `--max-age 小时` | `search.toml` 的 `24` | 只对 `--refresh auto` 生效 |
| `--in meta\|body\|both` | `meta` | `body` / `both` 扫 `works/`；当前没有投影，效果等于 `meta` |
| `--sort date\|citations` | `date` | `date` = `published_at` 降序；`citations` = `cited_by_count` 降序，无值的排后 |
| `--abstract` | 关闭（布尔开关） | 打开时每条附上摘要，截断到 `config/search.toml` 的 `abstract.chars`（默认 800）个字符。**不进 `spec_hash`**，所以不影响「同一检索式命中同一本 run 账本」。`lookup` 不受它约束——`lookup` 固定带完整摘要 |

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

# f) 清单带上摘要片段，便于判断相关性（截断长度由 config/search.toml 的 abstract.chars 控制）
scripts/run.cmd search --all-of "world model" --any-of drone --abstract --limit 10 --refresh false
```

**坑（实测）：**

- **不给检索式 = 列本地库全部**：空检索式跳过 FTS 过滤，返回本地所有 `live` work（条数受 `--limit` 限制）。**这一档只配 `--refresh false`**；配 `auto` / `true` 等于拿空检索式去联网。
- **不要给空关键词**：`--all-of ""` 实测 `ok=false`、`error.code=internal_error`（FTS5 语法错误）。要列全部就别带这个词。
- `--limit` 数的是 **work**，不是 record：一篇由 arXiv + OpenAlex 两条记录合成的只算 1。
- **`--refresh false` 也会写一条 run 台账**（`refreshed=0`、`requests=0`、`counts={}`）；`lookup` 的离线命中不写。两者都不发网络请求。

---

## 2. `lookup`

| 参数 | 默认 | 说明 |
|---|---|---|
| `--doi` / `--arxiv` / `--title` | — | **必须给一个**（一个都不给 → `ok=false`、`error.code=invalid_args`）；同时给多个时优先级 `--doi` > `--arxiv` > `--title`（实测） |
| `--limit N` | `20` | 只影响 `--title` 的候选池大小 |
| `--refresh auto\|true\|false` | `auto` | 见 §4；**只有本地未命中且给的是 `--doi` / `--arxiv` 时才联网**，`--title` 不联网 |

```powershell
scripts/run.cmd lookup --arxiv 2610.18808 --refresh true    # 命中即返回，最省钱
scripts/run.cmd lookup --doi 10.1109/mcse.2011.37 --refresh false
scripts/run.cmd lookup --title "Skytopia Monocular Drone Navigation" --refresh false
```

- **`lookup` 固定带完整摘要**（`abstract` 字段，不截断、不需开关）：它本来就是「看确定的这一篇」，体积不构成顾虑，而判断一篇值不值得读正好需要摘要。`search` 找到候选后转 `lookup` 读摘要，是推荐的动线。
- `--title` 走 FTS5 **短语**匹配（标题 + 摘要），取第一条命中，**不是模糊排序**。拼不准就改用 `search`。
- 本地未命中且 `--refresh false` → `ok=false`、`error.code=not_found`（退出码 1）。换标识，或先 `search` 联网取。
- `--doi` 入参会归一化，粘 `https://doi.org/...` 形式照样命中。

---

## 3. `profile`

| 子命令 | 作用 | 载荷 |
|---|---|---|
| `profile show` | 回显 `config/profile.toml` 里的查询 | `data.queries[]` |
| `profile check` | 解析、校验、查重 | 成功 `data.queries[]`；失败 `error.code=profile_invalid`，`error.message` 逐条带行号 |
| `profile scaffold` | 吐一份带注释的模板 | `data.template` |

- **CLI 从不写配置**：新建与修改由 agent 直接编辑 `config/profile.toml`，改完跑 `profile check`。
- **每条 query 至少给 `all_of` 或 `any_of` 一组**，这是硬校验，实测 `profile check` 会报错——与 §1 的「空检索式 = 列全部」不是一回事。
- 没有 `config/profile.toml` 时 `profile show` 返回 `error.code=no_profile`。创建流程见 SKILL.md §4，字段见 §7。

---

## 4. `--refresh` 三值

`--refresh` **必须显式写出三值之一**：

| 取值 | 含义 |
|---|---|
| `auto`（默认） | 同一检索式在 `refresh.max_age_hours`（默认 24 小时）内查过就跳过联网，并在 `data.degraded.reason` 里说明 |
| `true` | 强制联网 |
| `false` | **绝不联网**，只读本地库 |

判据是**检索式**（`spec_hash`），不是全局时间：换个关键词就是另一次检索。只有 `refreshed=1` 且 `status=ok` 的 run 才算「新鲜」，所以离线查询不会把 `auto` 锁死。

---

## 5. `verify` 与 `version`

- `verify [--sample N]`：抽样重算 `bytes/` 里文件的 `sha256` 并与文件名比对。`--sample` 默认 20，**`0` = 全量**；只读、不修复；发现不匹配即 `error.code=io_error`、退出码 1 并列出文件。当前 `data/` 下没有 `bytes/`，所以它是空跑（`mismatches: []`）。
- `version`：`data = {version, schema}`，自检用。

---

## 6. 信封里的载荷

`data.items[]` 的字段：

| 字段 | 说明 |
|---|---|
| `work_key` | 作品的稳定键 |
| `title` / `authors[]` / `venue` / `language` | 书目字段 |
| `published_at` | 组内最早的主日期（「新论文」语义） |
| `type` | 规范值：`article` / `preprint` / `chapter` / `thesis` / `paratext` / `other` |
| `doi` / `arxiv_id` / `openalex_id` | 可空 |
| `cited_by_count` | 只有 OpenAlex 记录有值，arXiv-only 的条目为 `null` |
| `sources[]` | 每条记录一行：`{source, source_id, first_seen, role}`，用于「这篇来自哪几条记录」 |
| `is_retracted` | 撤稿，不要推荐 |
| `dup_suspect` | 疑似同一篇但**未合并**，引用时必须说明 |

`data` 除 `items` 外：`network`（本次是否发过请求）、`cost`（`{usd, basis, requests}`）；**本次联网刷新过**时另有 `counts`。离线查库时不出现。

**摘要按需出现，不是每次都有**：`lookup` 固定带**完整**摘要；`search` 要打开 `--abstract` 才带，且是截断到 `abstract.chars` 的片段；不开时条目里**没有这个键**（不是 `null`），解析时按可缺席处理。

**摘要不一定是可逐字引用的原文**——这是不提供来源标记的代价，必须心里有数：多篇成员的摘要不一致时按两级取值，先看**版本层级**（`published` > `preprint`），再看**文本保真度**（`arxiv` 原生 `<summary>` > `crossref` 原生 > `openalex` 由倒排索引还原）。因此：

- 一篇只有预印本记录时，拿到的是**预印本稿**，可能与正式版不同（实测：9 组配对里 2 组实质变化，最坏 Jaccard 0.53，见 `docs/实验/EX-2026-10-14-身份与去重.md` §6）；
- 经 OpenAlex 的摘要是 **`abstract_inverted_index` 的还原产物**（OpenAlex 因法律原因不提供纯文本摘要），词序与标点会有失真。

**要引用摘要原文或照它翻译时，先回到 arXiv 摘要页或出版商页面核对。** 载荷里没有标记告诉你这条路有没有走过。

---

## 7. `config/profile.toml`

一个关注方向就是一条 `[[query]]`。

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | 查询名，**本文件内唯一**；也是 `sync --query` 的名字 |
| `any_of` | 与 `all_of` 至少给一组 | 关键词并集，同义词放这里 |
| `all_of` | 同上 | 关键词交集；与 `any_of` 同时生效 |
| `venues` | 可选 | venue 白名单 |
| `authors` | 可选 | 作者白名单 |
| `since_years` | 可选 | 回看窗口，默认 3 年 |
| `expires` | 可选 | 到期日期；只有临时专题才写 |

**没有 `schedule` 字段**——调度不进工具。

```toml
# config/profile.toml

[[query]]
name        = "world-model-uav"                        # 也是 sync --query 的名字
all_of      = ["world model"]                          # 交集：两个概念都要在
any_of      = ["UAV", "drone", "aerial", "unmanned"]   # 并集：同一概念的不同写法
venues      = ["ICRA", "IROS", "RA-L"]                 # 可选：venue 白名单
since_years = 3

[[query]]
name    = "world-model-survey"                         # 临时专题：带 expires
any_of  = ["world model", "latent dynamics"]
expires = "2026-12-31"
```

**一次性的查询不用写进这里**：`search --any-of "..."` 可以当场给检索式，结果照样落库、照样走缓存。

---

## 8. 错误码

`ok=false` 时看 `error.code`：

| code | 含义 | 重试同一命令 | 若否，先做什么 |
|---|---|---|---|
| `busy` | 写锁被占用（另一个 run 在跑） | ✅ 稍后一次 | — |
| `projection_missing` | `works/` 投影缺失 | ❌ | 先跑 `textify --work <work_key>` |
| `no_profile` | 没有 `config/profile.toml` | ❌ | 按 SKILL.md §4 创建 |
| `profile_invalid` | 解析失败（附出错行） | ❌ | 修文件后重跑 `profile check` |
| `invalid_args` | 参数不合法 | ❌ | 改参数 |
| `not_found` | 标识或 `work_key` 未命中 | ❌ | 换标识，或先 `search` 联网取 |
| `source_error` | 全部源都失败、零写入 | ✅ | — |
| `network_error` | 网络不可达 | ✅ | — |
| `rate_limited` | 429 或余额耗尽且无法降级 | ✅ 稍后 | — |
| `io_error` | 本地读写失败 | ❌ | 排查路径与权限 |
| `internal_error` | 未分类缺陷 | ❌ | 报缺陷 |

可重试的**只重试一次**。`source_error` / `network_error` / `rate_limited` 在**仍产出结果**时不走本表，只记进 `counts.failures[]` / `degraded`。
