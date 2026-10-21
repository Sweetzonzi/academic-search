# CLI 参考

> 按需加载：**要某个命令的参数、坑、载荷或错误码时才读本文**。SKILL.md 只给决策路径与硬规则。
> 参数签名与默认值的家在 `src/academic_search/cli.py`；本文列的是当前值，二者不一致时以代码为准。标「实测」的含义见 SKILL.md 开头。

---

## 1. `search`

| 参数 | 默认 | 说明 |
|---|---|---|
| `--all-of 词` | — | 可重复，也可一次给多个值（`--all-of a b`）；组内 **AND**（交集） |
| `--any-of 词` | — | 可重复，也可一次给多个值（`--any-of a b c`）；组内 **OR**（并集） |
| `--venue 名` | — | 可重复，也可一次给多个值。写**载体名**（会议或期刊都行，如 `ICRA` / `NeurIPS` / `IEEE T-RO` / `JAAMAS`）；**全称与别名也认**（`IEEE Robotics and Automation Letters` = `RA-L`，大小写与连字符不敏感）。名字由 `src/academic_search/venues.toml` 解析成 OpenAlex source id；**解析不到就报错**（`invalid_args`），也可直写 `S…`。判据是「作品的载体 source id 集合 ∩ 名单 ≠ ∅」，**`venue` 字符串不参与判定**。给了白名单时 **arXiv 那一腿不参与**（arXiv 没有载体概念），它在 `counts.by_source` 里记 0。**它只匹配已入库记录，且只认 OpenAlex 的载体指派**——经典论文常常只有预印本记录、或上游没给正式载体（实测 Ha & Schmidhuber 2018 的 NeurIPS 版在 OpenAlex 里只有 arXiv 载体），所以「用 venue 找某会经典」会漏；见下面的坑 |
| `--author 名` | — | 可重复，也可一次给多个值；任一作者满足即可（OR），与其他条件取 AND |
| `--since-years N` | — | 等价于 `published_at >= 今天 − N×365 天`（实测）；`0` 只留「今天之后」，通常为空 |
| `--limit N` | `20` | 计**合并去重后的 work 数**；`0` = 空列表。**只截断展示**——一次远端取数固定取一页 100 条，本地再过候选预算与结构化过滤，所以 `--limit 5` 之后池子里仍可能多出很多条，而返回条数也可能**少于** `--limit`（过滤太挑或候选预算用尽）。看 `counts.local` 的 `considered/matched/returned/truncated` 解释这个差 |
| `--offset N` | `0` | 过滤、去重、排序后跳过前 N 个 work，再按 `--limit` 返回；适用于主题查询与全库清单。与 `limit` 一样须为非负整数，负数报 `invalid_args`；零 limit 或超出范围返回空列表。仅影响本地展示，不参与新鲜度判定，也不追加远端取数 |
| `--source 源` | `sources.toml` 里启用的采集源 | 可重复；只在启用的 `openalex` / `arxiv` 内收窄，同名来源去重。未知、不支持或禁用的来源（如 `nosuch` / `crossref`）、没有启用任何采集源均报 `invalid_args`。**它参与新鲜度判定**：`--source arxiv` 与默认两源是两次不同的检索，前者不会顶替后者的新鲜度 |
| `--refresh auto\|true\|false` | `auto` | **三值必须显式写全**；见 §6 |
| `--max-age 小时` | `search.toml` 的 `24` | 只对 `--refresh auto` 生效；正数支持小数，负数与非有限值报 `invalid_args`；`0` 当前沿用配置时长 |
| `--in meta\|body\|both` | `meta` | `meta` = 只查标题/摘要；`body` = **只**扫 `works/` 下的 `paper.md`（`--refresh auto` 时不采集——正文只在本地；显式 `true` 仍照字面刷新元数据）；`both` = 两路并集。还没有生成过正文时 `body` 一条都不回 |
| `--sort date\|citations` | `date` | `date` = `published_at` 降序；`citations` = `cited_by_count` 降序。arXiv 不提供引用指标；仅排序仍保留该源，其采集按日期取数，本地无可用被引数据的结果排后。**它是取数条件**：OpenAlex 两档取不同切片，换档分别判定新鲜度；`--refresh false` 仍不联网。排的是**全局**被引，见下面的坑 |
| `--min-citations N` | 关闭 | 原始被引下限，包含等于（`>= N`）：远端下发 `filter=cited_by_count:N-`；N 为非负整数。arXiv 不提供被引次数，启用时跳过该源采集；本地按 work 聚合指标同判，没有被引数据的论文不通过。与主题条件取与，叠加不额外计费 |
| `--min-fwci X` | 关闭 | 领域归一化引用影响力下限，包含等于（`>= X`），远端为 `filter=fwci:X-`；X 必须是有限非负数，与 `--min-citations` 同时给出时**取与**。arXiv 不提供 FWCI，启用时跳过该源采集；本地没有该指标的论文不通过。很新的论文与部分类型缺少 FWCI，因此筛选偏向有一定年头的文献 |
| `--abstract` | 关闭（布尔开关） | 打开时每条附上摘要，截断到 `config/search.toml` 的 `abstract.chars`（默认 800）个字符。**不参与新鲜度判定**：同一检索式开了它也算同一次检索。`lookup` 不受它约束——`lookup` 固定带完整摘要 |

**分页用法（离线回归覆盖）：**

```powershell
scripts/run.cmd search --refresh false --offset 20 --limit 20
scripts/run.cmd search --all-of "world model" --refresh false --offset 40 --limit 20
```

两档排序的主值相同时按 `work_key` 升序，空值排后；数据库变化可能移动页边界，不提供跨命令快照。

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

# e) 排序（引用数降序）——这是**另一次取数**：换档会联网，比 `--sort date` 多回一批「被引最高的切片」
scripts/run.cmd search --any-of "multi-agent" --sort citations --limit 20 --refresh false

# f) 清单带上摘要片段，便于判断相关性（截断长度由 config/search.toml 的 abstract.chars 控制）
scripts/run.cmd search --all-of "world model" --any-of drone --abstract --limit 10 --refresh false

# g) 找「最新的高被引」：远端按被引过滤 + 本地同判，arXiv 腿自动跳过（实测 8 条全部 ≥ 100）
#    注意默认按日期取数，所以回来的是**最新的**那一批（实测那 8 条的出版年是 2019–2025），不是经典
scripts/run.cmd search --any-of "world model" --min-citations 100 --refresh true --limit 8

# g′) 找经典：再加 --sort citations 换成按被引取数。两档是两次不同的请求
#     它排的是**全局**被引，头部会有跨领域命中（实测同一检索式的头部是 1997 年的社会学论文与
#     2006 年的脑网络），必须逐条复核主题相关性
scripts/run.cmd search --any-of "world model" --min-citations 100 --sort citations --refresh true --limit 8

# h) 按载体筛：远端走 source id、本地走载体集合（实测 ICRA 一次取回 100 条）
scripts/run.cmd search --all-of "world model" --venue ICRA --refresh true --limit 5
```

**离线回归确认：**

- **过滤语义的缓存兼容**：带作者或引用阈值的查询使用明确包含 OR / 下界语义的身份，已有较窄采集记录不会使它们变新鲜，首次使用可能重新联网；已有语料保留。这是查询身份处理，不是数据库迁移。
- **正文布尔条件**：`all-of`、`any-of` 的组合在单份 Markdown 内判断，不跨文件拼接；`both` 合并元数据命中与正文命中，仍是两路并集。

**坑（实测）：**

- **不给检索式 = 列本地库全部**：空检索式跳过 FTS 过滤，返回本地所有 `live` work（条数受 `--limit` 限制）。**这一档只配 `--refresh false`**；配 `auto` / `true` 等于拿空检索式去联网。
- **不要给空值**：`--all-of` / `--any-of` / `--author` / `--venue` 的空字符串或全空白值报 `invalid_args`。要列全部就别给检索词，并配 `--refresh false`。
- `--limit` 数的是 **work**，不是 record：一篇由 arXiv + OpenAlex 两条记录合成的只算 1。
- **返回条数少于 `--limit` 时先看偏移与 `counts.local`**：`offset` 会跳过已匹配的条目；`considered` 是候选预算内看了多少 work，`matched` 是过滤后、offset / limit 切片前的匹配数，`returned` 是实际返回数；`truncated` 表示预算用尽，`venue_filtered_out` 是文本命中但被载体条件排除的 work 数。本地匹配数与本次远端取数没有减法关系。
- **`--venue` 只匹配已入库记录，且只认 OpenAlex 的载体指派**。经典论文常常只有预印本记录（`work_carriers` 里没有该会的 source id），于是被白名单 fail-closed 滤掉，`counts.local.venue_filtered_out` 会把它数出来。实测 Ha & Schmidhuber 2018 的 `World Models`：库里只有 arXiv 记录，OpenAlex 对应的 NeurIPS 版标题是 `Recurrent World Models Facilitate Policy Evolution`、且 `locations` 里**只有 arXiv**——`--venue NeurIPS` 查不到它不是本地漏采，是上游没有这个载体。→ 用 venue 找经典要叠加 `--sort citations`（默认按日期只取最新一页），并预期仍可能漏；漏了就改用主题词查。
- **短词 + `--sort citations` 的噪声比长短语大得多。** `--any-of dreamer` 按被引降序的前 20 条里，头部是 EEG 情感识别的图网络（1601 引）与 `DREAMER` 数据集（1120 引），只有 `Dream to Control`（573 引）是本方向的。词越短、越常见，全局被引越容易把跨领域高被引拉上来。→ **短词要与领域词共现**（`--all-of "world model" --any-of dreamer`），或先锚定主题再加 `--min-citations`。**不要**以为「只匹配标题」能救：`DREAMER` 数据集就在标题里，而 `Dream to Control` 的标题没有 `dreamer`——实测 title-only 会滤掉唯一相关的那篇。
- **`--refresh false` 也会写一条 run 台账**（`refreshed=0`、`requests=0`、台账的 `counts={}`）；`lookup` 的离线命中不写。两者都不发网络请求；信封里的 `counts.local` 与台账不是一回事。
- **「被引高」不是「这个领域里重要」。** `--sort citations` 与 `--min-citations` 排/筛的都是**全局**被引，与主题只有「短语命中」这一层关系。**先分清两个档**：只给 `--min-citations` 时仍按默认的 `--sort date` 取数，回来的是「最新的、被引超过阈值的一批」（实测 8 条出版年落在 2019–2025）；**要经典必须再给 `--sort citations`**，那时才按被引降序取。实测 `"world model"` 按被引降序的头部是：1971 年的定理证明（被引 4 865）、1997 年的社会学论文（4 684）、2006 年的脑网络（2 591），**前五里只有两条**与「世界模型」有关（`docs/实验/EX-2026-10-16-检索口径与来源校验.md` §12(b)(c)）。短语命中还可能是分词的产物——`small-world models` 含子串 `world model`，`world modeling` 归并成同一词。→ 用这两个参数时**主题词要给具体**、取回后**逐条复核主题相关性**，并在回答里说明「这是按被引排的，不是按相关性排的」。

  ```powershell
  # 反例：宽泛主题 + 被引阈值 → 头部是跨领域噪声
  scripts/run.cmd search --any-of "world model" --sort citations --refresh true --limit 5
  # 正例：具体主题 + 阈值 + 复核
  scripts/run.cmd search --all-of "world model" --any-of "quadrotor" --min-citations 50 --refresh true
  ```

- **`--min-fwci` 会整批丢掉最新论文。** `fwci` 是 OpenAlex 的加工指标，很新的论文与部分类型没有该值，而「空值不通过」是刻意的（fail-closed）。→ **追新**用 `--refresh true` 不加阈值；要**找经典**才加，并预期结果是「有一定年头的文献」。两个阈值各有分工：`--min-citations` 直观但随领域漂移，`--min-fwci` 以「领域同年平均 = 1」为尺度、跨领域不用重猜。

---

## 2. `lookup`

| 参数 | 默认 | 说明 |
|---|---|---|
| `--doi` / `--arxiv` / `--title` | — | **必须给一个**（一个都不给 → `ok=false`、`error.code=invalid_args`）；同时给多个时优先级 `--doi` > `--arxiv` > `--title`（实测） |
| `--limit N` | `20` | 只影响 `--title` 参与匹配的条目数 |
| `--refresh auto\|true\|false` | `auto` | 见 §6。`true` = **先刷再查本地**（本地已命中也会去更新引用、撤稿标记与载体）；`auto` = 只有本地未命中才联网；`--title` 两种档下都不联网（标题不是标识，用不了单实体 GET），`true` 时会进 `degraded` 并说明 |

```powershell
scripts/run.cmd lookup --arxiv 2610.18808 --refresh false   # 只读本地，零联网
scripts/run.cmd lookup --arxiv 2506.18537 --refresh true    # 先刷再查：命中也会更新引用数（单实体 GET 免费）
scripts/run.cmd lookup --doi 10.1109/mcse.2011.37 --refresh false
scripts/run.cmd lookup --title "Skytopia Monocular Drone Navigation" --refresh false
```

- **`lookup` 固定带完整摘要**（`abstract` 字段，不截断、不需开关）：它本来就是「看确定的这一篇」，体积不构成顾虑，而判断一篇值不值得读正好需要摘要。`search` 找到候选后转 `lookup` 读摘要，是推荐的动线。
- **`true` 与 `auto` 的区别只在「本地已命中时刷不刷」**：`auto` 命中即返回（零请求），`true` 命中也要刷一次。刷新本身**免费**——单实体 GET 的计费头是 `x-ratelimit-cost-usd: 0`（实测），所以这条差别不构成成本差异，只构成「引用数是不是最新的」。要修「库里只有 arXiv 记录、被引是 `null`」这类残缺，就用 `true`。
- `--title` 走 FTS5 **短语**匹配（标题 + 摘要），取第一条命中，**不是模糊排序**，也**从不联网**（标题不是标识，单实体 GET 用不上）。拼不准就改用 `search`；`--refresh true/false/auto` 三档在 `--title` 上都不发请求。
- 本地未命中且 `--refresh false` → `ok=false`、`error.code=not_found`（退出码 1）。换标识，或先 `search` 联网取。
- `--doi` 入参会归一化，粘 `https://doi.org/...` 形式照样命中。

---

## 3. `download`

取一篇的**原始字节**（arXiv LaTeX 源码 / arXiv HTML / PDF）存进本地库。**不转换成 Markdown**——那是 `read` 的事。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--work work_key` | 必填 | 要哪一篇的原件；`work_key` 来自 `search` / `lookup` 的结果条目 |
| `--source auto\|latex\|html\|pdf\|all` | `auto` | `auto` = LaTeX 源码优先、没有就回退 arXiv HTML（一次拿到一份能用的原件）；`latex` / `html` 指定即**不回退**；`pdf` 只在显式写 `pdf` 或 `all` 时下载，**只下载不解析**；`all` = 三条都取 |
| `--refresh auto\|true\|false` | `auto` | `auto` = 本地已有**同一版本**就零请求复用；`true` = 无视版本重取（arXiv 没变会回 **304**，不重下字节）；`false` = 绝不联网 |

**落到哪**：原件进内容寻址的 `bytes/`（只存一份，同名实体复用），并在 `works/<proj>/` 建一份投影：`source.tar.gz` / `source.html` / `source.pdf`。投影是硬链接，删掉重跑一次就回来。

目录名是 `<年>-<首作者>-<标题>-<work_key>`：**尾段 20 位才是身份**，前几段随元数据变（改标题就会改名）。所以要用路径就用载荷里的 `work_path`（它永远指向当前目录），别把抄下来的路径当标识（`docs/决策/ADR-0068.md`）。

**信封**（`data`）：`{work_key, record_uid, arxiv_id, artifacts[], network, cost}`。`artifacts[]` **逐取件路线一行**：

| 字段 | 说明 |
|---|---|
| `kind` | `source_tar_gz`（LaTeX 源码包）/ `source_html`（arXiv HTML）/ `source_pdf` |
| `status` | `fetched`（这次取的）/ `reused`（命中缓存或 304）/ `failed`（附 `reason` 与取件路线 `source`） |
| `source_version` | 取的是哪一版（`v1` / `v2`…）；`null` = 不知道 |
| `sha256` / `size` | 字节的身份与大小 |
| `path` / `work_path` | `bytes/` 里的实体 / `works/` 里的投影（投影不在时没有这个键） |

```powershell
# 拿一份能读的原件（LaTeX 优先，没有就回退 HTML）
scripts/run.cmd download --work <work_key> --refresh auto

# 只要 PDF（只下载，不解析）
scripts/run.cmd download --work <work_key> --source pdf --refresh auto

# 三条都要
scripts/run.cmd download --work <work_key> --source all --refresh auto

# 联网确认有没有更新（arXiv 没变回 304，不重下字节）
scripts/run.cmd download --work <work_key> --refresh true
```

- **判据是版本相等**：`search` / `lookup` 刷到的远端版本与本地取过的一致，`auto` 就零请求。版本未知（旧记录还没被元数据刷新过）时会**先读一次 arXiv feed** 补上，所以那一次是两个请求，之后不再发生。
- **`auto` 的回退是静默的**，但 `artifacts[]` 留着失败那条（`status="failed"` + `reason`）——要判断拿到的是 LaTeX 还是 HTML，看 `kind`。
- **一篇一次最多 3 个请求**（`all`），而 arXiv 有 3 秒请求间隔：不要对着候选列表逐篇跑。
- `--refresh false` 且本地没有这份原件 → `error.code=projection_missing`，重试无用。
- 非 arXiv 记录报 `source_error`：初版只从 arXiv 取原件。

---

## 4. `sync`

按名字跑 `config/profile.toml` 里的**具名查询**：联网采集 → 落候选池 → 写报告。**初版唯一按名字执行检索式的入口**，供任意调度器无头调用（工具本身不含调度语义）。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--query 名` | 画像里全部 `enabled = true` 且未过期的查询 | 可重复。**解析不到、或解析到但 `enabled = false` / 已过期 → `ok=false`、`error.code=invalid_args`**——显式点名一件画像明说不做的事是调用方的错，静默跳过会让整次调用白跑。画像缺失 → `no_profile`；画像里一条可跑的查询都没有 → `invalid_args` |
| `--refresh auto\|true\|false` | `auto` | 见 §6。按**每条查询各自的 `spec_hash`** 各记各的；跳过时该 run 照样写一行，`refreshed = false`、零成本 |

```powershell
# a) 跑画像里全部启用的查询
scripts/run.cmd sync

# b) 只跑一条，并强制联网
scripts/run.cmd sync --query world-model-uav --refresh true

# c) 只查本地（零请求、零成本）——想把报告重出一遍时用
scripts/run.cmd sync --refresh false
```

**每次调用写这些**：

- `data/runs/<run_id>/run.json` + `report.md`——**一条查询一行账本、一个目录、一份报告**。报告正文两栏：**新发表**（`published_at` 落在本次窗口内）与**新发现**（本次才入池、发表更早）。
- `data/latest-report.md`——**最近一次调用**的汇总，按查询分节；单查询调用时与那份 `report.md` 逐字相同。
- `data/runs/<run_id>/responses/<n>-<source>.json`——被留档的响应原文（产生了入库记录的页，以及失败的页）。`run.json` 的 `responses[]` 记 `{n, sha256, source, url, page, status}`；`url` 里的凭据记 `REDACTED`。
- **只有 `sync` 落 `data/runs/`**：`search` / `lookup` 只写账本行（交互式检索没有留档的消费者，而 `data/runs/` 会随调用次数增长）。

**信封**（`data`）：`{runs: [{run_id, status, query, spec_hash, refreshed, window: {from, to}, counts, cost}, …], degraded?: {reason}}`。

- **逐 run 自包含，没有调用级合计**：`counts` / `cost` 都在每个元素里，要总数自己求和。`network` 不是顶层字段——它等于各 `runs[].refreshed` 的或。
- **不返回结果清单**：清单在 `run.json` 的 `items.new_published` / `items.new_discovered` 里，stdout 只给调度器一眼判断成败。
- `degraded` 是**调用级**的：部分查询因新鲜度跳过、或余额不足，都汇总在这一句话里。

**窗口**：每次现算，上界恒为今天。

- **首次回溯**：查询名与当前 `spec_hash` 尚无完整联网采集记录时，用**它自己的** `since_years`；完成后用 `config/search.toml` 的 `sync.lookback_days`（默认 30）。同名查询改变条件或来源时重新回溯；离线、部分失败与额度中断不会消耗首次窗口；完整性判据见 §8。
- 上界不拿「本次实际取到的最大 `publication_date`」回填——窗口是**请求的口径**，用取到的结果当上界会让「这次取到哪一年」悄悄改变下一次的窗口。

**与 `search` 共用一本账**（`docs/决策/ADR-0049.md`、`docs/决策/ADR-0066.md`）：判据是 `spec_hash`，不是命令名。某条查询刚被一次字段等价的 `search` 刷过，`sync` 也会跳过它——这是账本判据的直接结果，不是故障。要补漏就给 `--refresh true`。

**额度**：`search` 与 `sync` 都使用 `config/search.toml` 的 `quota.min_remaining_usd`（默认 `0.05`）。OpenAlex 的成功或 HTTP 错误响应带回剩余额度，**低于它就不再发后续请求**，`degraded.reason` 说明整次跳过或部分完成。一次 `sync` 共用一个客户端，所以前一条查询读到的余额会拦住后一条。

---

## 5. `profile`

| 子命令 | 作用 | 载荷 |
|---|---|---|
| `profile show` | 回显 `config/profile.toml` 里的查询 | `data.queries[]` |
| `profile check` | 解析、校验、查重 | 成功 `data.queries[]`；失败 `error.code=profile_invalid`，`error.message` 逐条带行号 |
| `profile scaffold` | 吐一份带注释的模板 | `data.template` |

- **CLI 从不写配置**：新建与修改由 agent 直接编辑 `config/profile.toml`，改完跑 `profile check`。
- **每条 query 至少给 `all_of` 或 `any_of` 一组**，这是硬校验，实测 `profile check` 会报错——与 §1 的「空检索式 = 列全部」不是一回事。
- 没有 `config/profile.toml` 时 `profile show` 返回 `error.code=no_profile`。创建流程见 SKILL.md §4，字段见 §9。

---

## 6. `--refresh` 三值

`--refresh` **必须显式写出三值之一**：

| 取值 | 含义 |
|---|---|
| `auto`（默认） | **`search`**：同一检索式在 `refresh.max_age_hours`（默认 24 小时）内查过就跳过联网，并在 `data.degraded.reason` 里说明。**`sync`**：同一条查询按**各自的 `spec_hash`** 判，与 `search` 共用一本账。**`lookup`**：本地标识未命中才联网（不看时间） |
| `true` | **先刷新，再查库**——三条命令同义。`lookup` 在本地已命中时也会刷 |
| `false` | **绝不联网**，只读本地库 |

`search` 的判据是**检索式**，不是全局时间：换个关键词就是另一次检索。只有**会改变远端请求**的参数参与判定——`all_of` / `any_of` / `venues` / `authors` / `since_years` / `--source` / `--sort` / `--min-citations` / `--min-fwci`；`--limit` / `--offset` / `--abstract` / `--in` 不参与，所以换个 `--limit` 仍算同一次检索。新鲜度要求完整采集记录（§8）：离线和部分成功本身不使检索式变新鲜；历史行缺少完整性标记时保留语料，后续按需重新采集。

`sync` 用的是 `search` 那本新鲜度账，判据同为 `spec_hash`（`docs/决策/ADR-0066.md` 决策 7）。一次 `sync` 里若两条查询字段等价，后一条会命中前一条刚写的账而跳过——这是账本判据的直接结果，不额外去重。

`lookup` **不走这本新鲜度账**：它按标识取，`auto` 的判据只是「本地有没有命中」（口径见 `docs/决策/ADR-0065.md`）。所以同一篇 `lookup` 两次，两次都是零请求；想更新引用数就显式给 `true`。

---

## 7. `verify` 与 `version`

- `verify [--sample N]`：抽样重算 `bytes/` 里文件的 `sha256` 并与文件名比对。`--sample` 默认 20，**`0` = 全量**；只读、不修复；发现不匹配即 `error.code=io_error`、退出码 1 并列出文件。`bytes/` 下还没有产物时它是空跑（`mismatches: []`）。
- `version`：`data = {version, schema}`，自检用。

---

## 8. 信封里的载荷

`data.items[]` 的字段：

| 字段 | 说明 |
|---|---|
| `work_key` | 作品的稳定键 |
| `title` / `authors[]` / `venue` / `language` | 书目字段 |
| `published_at` | 组内最早的主日期（「新论文」语义） |
| `type` | 规范值：`article` / `preprint` / `chapter` / `thesis` / `paratext` / `other` |
| `doi` / `arxiv_id` / `openalex_id` | 可空 |
| `cited_by_count` | 只有 OpenAlex 记录有值，arXiv-only 的条目为 `null` |
| `fwci` | 同上取最大；`--min-fwci` 的判定依据（同义的 `citation_normalized_percentile` 不进载荷） |
| `sources[]` | 每条记录一行：`{source, source_id, first_seen, role}`，用于「这篇来自哪几条记录」 |
| `is_retracted` | 撤稿，不要推荐 |
| `dup_suspect` | 疑似同一篇但**未合并**，引用时必须说明 |

`data` 除 `items` 外：`network`（本次是否发过请求）、`cost`（`{usd, basis, requests}`，含失败请求）；`counts`（`search` 恒有，`lookup` 只在刷新时）——`counts.local` 是本地漏斗 `{considered, matched, returned, truncated, venue_filtered_out}`，用来解释「为什么返回条数少于 `--limit`」。`search` 进入采集流程时另有 `new_candidates` / `dedup_blocked` / `by_source` / `failures` / `complete`，即使全部来源被条件排除也保留这些字段，此时 `network=false`、`complete=false`。

**`data.warnings`**：`search` / `lookup` 成功返回的可选字符串列表，没有提示时省略。包含来源因能力限制被实际跳过、候选预算截断、部分来源失败、额度停止或标题查询无法执行所请求刷新的人读说明；同类合并。正常离线、新鲜度复用或零结果本身不产生提示。机器判断继续使用 `counts` / `degraded`；本字段不进入账本或 `sync` 留档。触发条件的权威定义见仓库 `docs/接口/输出载荷.md` §2。例如：

```json
"warnings": ["arXiv 采集接口不提供被引次数，已因 --min-citations 条件跳过该源采集。"]
```

**`counts.complete`（`search` / `sync`）**：实际发起请求、未被条件排除的来源均成功完成规定的一页、且未因额度停止时为 `true`；成功空页也算完成。这不是远端全库覆盖率。只有 `status=ok`、`refreshed=true`、`requests>0` 且此标记为真的账本行可证明新鲜度或首次回溯完成。离线与新鲜度跳过的账本 `counts={}`；`lookup` 不带此标记。部分成功后若没有完整采集依据，下次 `auto` 会重查整条检索式，逐源补查尚未实现。

**摘要按需出现，不是每次都有**：`lookup` 固定带**完整**摘要；`search` 要打开 `--abstract` 才带，且是截断到 `abstract.chars` 的片段；不开时条目里**没有这个键**（不是 `null`），解析时按可缺席处理。

**摘要不一定是可逐字引用的原文**——这是不提供来源标记的代价，必须心里有数：多篇成员的摘要不一致时按两级取值，先看**版本层级**（`published` > `preprint`），再看**文本保真度**（`arxiv` 原生 `<summary>` > `crossref` 原生 > `openalex` 由倒排索引还原）。因此：

- 一篇只有预印本记录时，拿到的是**预印本稿**，可能与正式版不同（实测：9 组配对里 2 组实质变化，最坏 Jaccard 0.53，见 `docs/实验/EX-2026-10-14-身份与去重.md` §6）；
- 经 OpenAlex 的摘要是 **`abstract_inverted_index` 的还原产物**（OpenAlex 因法律原因不提供纯文本摘要），词序与标点会有失真。

**要引用摘要原文或照它翻译时，先回到 arXiv 摘要页或出版商页面核对。** 载荷里没有标记告诉你这条路有没有走过。

---

## 9. `config/profile.toml`

一个关注方向就是一条 `[[query]]`。

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | 查询名，**本文件内唯一**；也是 `sync --query` 的名字 |
| `any_of` | 与 `all_of` 至少给一组 | 关键词并集，同义词放这里 |
| `all_of` | 同上 | 关键词交集；与 `any_of` 同时生效 |
| `venues` | 可选 | 载体白名单，写名字（会议与期刊都可以）。登记表有 16 条：ICRA / IROS / NeurIPS / ICML / ICLR / arXiv，以及 RA-L / IEEE T-RO / IJRR / Science Robotics / Journal of Field Robotics / Autonomous Robots / AAMAS / JAAMAS / JAIR / Artificial Intelligence——**完整清单与别名见 `src/academic_search/venues.toml`**。名字解析不到会让该查询报错。**`AAMAS`（会议）与 `JAAMAS`（期刊）是两个实体**，名字只差 `Multiagent` / `Multi-Agent` 的拼写：写缩写 `AAMAS` 得到会议 `S4306417597`，写带连字符的全称 `Autonomous Agents and Multi-Agent Systems` 得到期刊 `S5405189`，写会议真名（`Multiagent` 连写）解析不到、直接报错 |
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

## 10. 错误码

`ok=false` 时看 `error.code`：

| code | 含义 | 重试同一命令 | 若否，先做什么 |
|---|---|---|---|
| `busy` | 写锁被占用（另一个 run 在跑） | ✅ 稍后一次 | — |
| `projection_missing` | 本地没有这篇的正文，且此时不允许联网 | ❌ | 去掉 `--refresh false` 重跑，或先 `download --work <work_key>` |
| `no_profile` | 没有 `config/profile.toml` | ❌ | 按 SKILL.md §4 创建 |
| `profile_invalid` | 解析失败（附出错行） | ❌ | 修文件后重跑 `profile check` |
| `invalid_args` | 参数不合法 | ❌ | 改参数 |
| `not_found` | 标识或 `work_key` 未命中 | ❌ | 换标识，或先 `search` 联网取 |
| `source_error` | 全部源都失败、零写入 | 原因允许时一次 | 授权、依赖或策略问题先修原因，见[初始化排障](setup.md#5-按失败阶段恢复) |
| `network_error` | 网络不可达 | 暂时故障一次 | 网络策略拒绝、代理或证书配置错误先修原因，见[初始化排障](setup.md#5-按失败阶段恢复) |
| `rate_limited` | 429 或余额耗尽且无法降级 | 依返回原因 | 速率限制稍后一次；额度耗尽停止，按返回信息等待或使用已有结果 |
| `io_error` | 本地读写失败 | ❌ | 排查路径与权限 |
| `data_not_writable` | 数据目录不可写（只读挂载 / 权限不足）；**连 `--refresh false` 都打不开库** | ❌ | 按[初始化排障](setup.md#5-按失败阶段恢复)检查原库权限与根目录；迁移保留配置和数据 |
| `internal_error` | 未分类缺陷 | ❌ | 报缺陷 |

可重试的**只重试一次**。启动器失败可能没有信封，先用 [launcher 诊断](setup.md#4-分层自检)定位；`version` / `profile` 成功不证明数据库或网络可用。`source_error` / `network_error` / `rate_limited` 在**仍产出结果**时不走本表，只记进 `counts.failures[]` / `degraded`。

**缺 pandoc 也报 `source_error`**，但那不是网络问题、重试无用——按 `error.message` 把它装好（获取方式与三处查找顺序见 SKILL.md §7）。
