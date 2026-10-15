# arXiv：检索、日期窗与取件（agent 速查）

来源：仓库 `docs/接口/数据源适配.md`、`docs/调研/数据源事实.md`；日期窗部分是 2026-10-15 的实测（`docs/实验/EX-2026-10-15-首次真检索.md` §3.1）。

## 1. 通道

| 用途 | 端点 |
|---|---|
| 检索 / 按 ID 取元数据 | `http://export.arxiv.org/api/query`（Atom XML，无 key） |
| 取全文（LaTeX 源码包，首选） | `https://arxiv.org/e-print/<id>` |
| 取全文（HTML） | `https://arxiv.org/html/<id>` —— **不一定有**，只认状态码，不要信 feed 里的声明 |
| 摘要页 | `https://arxiv.org/abs/<id>` |

## 2. 限流（硬约束）

- **每 3 秒 1 个请求、单连接**，而且是「你控制的所有机器合计」。
- 单次 ≤ 2000 条、总量 ≤ 30000 条。
- **新论文午夜后才对 API 可见** → 一天查一次足够，同一天刷两次几乎没有额外收益（这正是 `refresh.max_age_hours = 24` 的依据）。

## 3. 检索语法

- 字段前缀：`all:` 全文、`ti:` 标题、`abs:` 摘要、`au:` 作者、`cat:` 分类（如 `cat:eess.SY`）、`jr:` 期刊引用、`co:` 评论。
- 布尔：`AND` / `OR` / `ANDNOT`，用括号分组。
- 短语：`abs:"world model"`。
- 排序只有 `relevance` / `lastUpdatedDate` / `submittedDate`（本 CLI 用 `submittedDate` 降序）。

## 4. 日期窗的实测限制（会咬人）

    submittedDate:[YYYYMMDDHHMM TO YYYYMMDDHHMM]

- **只有上界等于没有过滤**（论文都在过去），所以有信息量的是下界。
- **退化的边界值一律 HTTP 500**：`000000000000`、`*`、`999999999999` 三个都实测 500，不是 400、也不是空结果。
- 可用的写法：`submittedDate:[199101010000 TO 202610152359]`；上界用 `999912312359` 也实测 200。
- 本 CLI 的行为：**只在有下界（`--since-years` 或增量窗口）时才下发日期子句**，上界缺省用 `999912312359`。所以不写 `--since-years` 时不会因为日期子句而 500。

## 5. 版本号

- 条目 id 形如 `2610.18808v2`；**没有版本号 = 最新版**。
- 建身份时归一掉 `vN`（`2610.18808` 与 `2610.18808v2` 是同一篇）；**取全文时要带具体版本**。
- 旧式 id 形如 `math/0601001`（含斜杠），不要用「必须有小数点」的假设去套。
- `<arxiv:doi>` 是作者手填的，实测 3 篇里只有 1 篇有 → 不要依赖它建身份。

## 6. 本 CLI 的参数怎么映射到 arXiv

| CLI 参数 | arXiv 查询 |
|---|---|
| `--all-of x`（可重复） | `abs:"x"`，多个之间用 `AND` |
| `--any-of a b` | `(abs:"a" OR abs:"b")` |
| `--author A` | `au:"A"` |
| `--venue V` | **不生效**：arXiv 记录没有 venue。带上 `--venue` 只会让结果变少，不会报错 |
| `--since-years N` | `submittedDate:[(今天-N 年)0000 TO 今天2359]` |
| `--limit N` | `max_results`（上限 2000） |

> 关键词走的是 **`abs:`（摘要）**，不是 `all:`：只出现在标题里的词不会命中。结果偏少时先把词放宽，而不是怀疑接口坏了。
