# OpenAlex：覆盖、计费与字段事实（agent 速查）

快照来源：仓库 `docs/调研/数据源事实.md`（REF-SOURCES，实测 2026-10-14）与 `docs/实验/EX-2026-10-15-首次真检索.md`（实测 2026-10-15）。数字会变，逐条来源 URL 见 `docs/调研/参考资料-公开API事实.md`。

## 1. 覆盖范围

- 主力元数据 + 引用图谱：`ids`、`abstract_inverted_index`（倒排索引，需还原成文本）、`referenced_works`、`filter=cites:<id>`（向前被引）、`related_works`（**主题相似，不是引用关系**）、`topics` / `keywords`、`fwci`、`citation_normalized_percentile`、`is_retracted`、`open_access.oa_status`、`best_oa_location.pdf_url`、`counts_by_year`、`language`。
- **中文文献不在覆盖内**：CNKI / 万方 / 维普在官方 125 个文档页里一次都没出现；官方给的收录路径是「把作品放进我们已经在抓的地方，对多数期刊就是通过 Crossref 注册 DOI」。
- **扩展语料默认不可见**：约 1.9 亿条低质记录（大量无 DOI 的机构库 / DataCite 记录）默认不出现在 API 结果里，要 `corpus=all` 才取 → 无 DOI 的学位论文、技术报告即便有 Work ID 也可能查不到。
- 摘要覆盖率：2022 年后作品 > 60%、2000 年前约 45%，尾部有噪声 → **摘要缺失是正常分支**，不要当成查询失败。
- **自由文本噪声极大**：`search=attention is all you need` 命中 320 万条。做「有没有人做过」的判定要走结构化检索，不要拿命中数当结论。

## 2. 计费与鉴权（决定「现在能不能用」）

| 项 | 实测 / 文档值 |
|---|---|
| 匿名（无 key） | `x-ratelimit-limit-usd: 0.1` / 天，**按出口 IP 共享**（同一网络的其他人也在用这个池子） |
| 免费 key | **$1/天**（10 倍），每日 00:00 UTC 重置；速率 30 req/s（匿名 10 req/s） |
| 不含全文检索的 `filter` | `x-ratelimit-cost-usd: 0.0001` → $0.1/千次 |
| **任何全文检索** | `0.001` → $1/千次。`search=` 与**带 `.search` 后缀的 filter**（如 `filter=title_and_abstract.search:`）**同价**——分档的轴是「有没有做全文检索」，不是参数名（实测） |
| 单实体 GET | **免费**（额度耗尽后仍返回 200） |
| 任意 4xx / 5xx | **免费**（计 0 credits） |
| `from_created_date` / `from_updated_date` | 文档标注付费层，无 key 实测 429 → 「增量」只能走「回看 N 天 + 本地去重」 |

**含义**：匿名档随时可能 429，额度耗尽会影响同一出口 IP；可靠使用 OpenAlex 应配置 key。凭据注入与可用性检查统一见[初始化参考 §3](setup.md#3-openalex-凭据)。改用 `--source arxiv` 时说明覆盖变化，该源不能替代被引、FWCI 或载体筛选。

## 3. 字段语义里会咬人的几条

1. **`publication_date` 是正式版日期**：实测 arXiv 2009 年的论文在 OpenAlex 里是 2012（期刊版）、arXiv 2024-01 的是 2023-11（会议版）。→ 判断「领域新论文」**必须用 arXiv 的 `published` / `updated`**，不能用它。
2. **预印本与正式版是同一条 Work**：`10.48550/arxiv.1102.1523` 与 `10.1109/mcse.2011.37` 同为 `W2146292423`。二者靠 location 级 `version` 区分，**没有 `is_preprint_of` 字段**；`related_works` 是主题相似，别混用。
3. **`ids` 不含 arXiv 键**：arXiv 副本体现为 `locations[]` 里的 `pmh:oai:arXiv.org:...` 命名空间 id（**大小写敏感**），或 `10.48550/arXiv.<id>` 形式的 DOI。跨源桥要自己构造并校验。
4. **被合并 / 删除的 Work ID 直接 404，没有跳转**：官方删除日志实测 5231 万条、滞后约 3 周，取 5 个真实 ID 全部 404 且无 `Location` 头（存活对照 200）；官方文档同时存在「301」与「404」两种说法，实测以后者为准，且 Walden 切换后不再提供旧 ID 到新 ID 的映射。→ **Work ID 不能当不可变主键。**
5. **`ids.doi` 有错配**：实测出现过 DOI 指向另一篇论文的记录（GPT-4 技术报告 `W4327810158` 的 `ids.doi` 指向 `10.4230/lipics.cosit.2024.11`）。→ 凡是用 DOI 建身份，必须回查标题。
6. **filter 值必须转义**：标题含 `,` 或 `:` 的请求直接 400。
7. `select=` 只能选顶层字段；`per_page` 文档上限 100（本 CLI 取 100）；普通分页上限 1 万条，再往前要 `cursor=*`；整条 URL 上限约 4 KB；`|`（OR）的值 ≤ 100 个。
8. 语法有新旧之分：`filter=title_and_abstract.search:` 与 `default.search:` 仍可用但已弃用；新写法是 `search=` 与 `search.title_and_abstract=`。

## 4. 本 CLI 的参数怎么映射到 OpenAlex

主题条件走 `search.title_and_abstract=`（一个布尔串），结构化条件走 `filter=`：

| CLI 参数 | OpenAlex 请求 |
|---|---|
| `--all-of x`（可重复） | `search.title_and_abstract=("x1" AND "x2")`——每个值加双引号，值里的 `"` 被剥掉 |
| `--any-of a b` | 同上，组内用 ` OR `；两组之间 ` AND `。如 `("world model") AND ("drone" OR "UAV")` |
| `--venue V` | `filter=locations.source.id:<S-id>`——会名先由 `src/academic_search/venues.toml` 登记表解析；**解析不到即报错**，也可直写 `S…` |
| `--author A B` | 单个 `filter=raw_author_name.search:"A" OR "B"`，姓名短语取 OR |
| `--since-years N` | `filter=from_publication_date:<今天-N 年>` + `to_publication_date:<今天>` |
| `--min-citations N` / `--min-fwci X` | `filter=cited_by_count:N-` / `fwci:X-`，包含下界；两个同时给出取与 |
| `--sort date` / `--sort citations` | `sort=publication_date:desc` / `cited_by_count:desc`，**总是显式下发** |

- **一次取数固定一页 100 条**（`per_page=100`），`--limit` 只截断展示——计费按次不按条，取少不省钱。
- **排序与来源都参与新鲜度判定**（和 `--venue` / 两个阈值一样）；`--limit` / `--abstract` / `--in` 不参与。
- **给 `--venue` 白名单或任一引用阈值时，arXiv 那一腿不参与**（arXiv 没有载体概念、没有被引数据），它在 `counts.by_source` 里如实记 0，不算失败。

> `--since-years` 在 OpenAlex 上走的是 `publication_date`，也就是**正式版日期**。要找真正的新预印本，用 `--source arxiv`（或补跑一次），不要只看 OpenAlex 的日期窗。
