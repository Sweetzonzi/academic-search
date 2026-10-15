---
doc-id: REF-API
kind: reference
measured: 2026-10-14
title: 公开学术数据库 API 事实核查报告
---

# 公开学术数据库 API 事实核查报告（2026-10-14 实查）

## A. OpenAlex（主力元数据源）

**基础与配额（重大变化）**：文档已从 docs.openalex.org 迁至 [help.openalex.org](https://help.openalex.org/api/authentication/)。
- **免费 API key = 每日 $1 额度**；无 key = $0.10/天（差 10×）。额度以"信用点/美元"计费，不再说"每日请求数上限"。
- 速率：无 key **10 req/s**、免费 key 或预付 **30 req/s**、年费计划 **50 req/s**；语义搜索固定 **1 req/s**；带 >5 个 OR/AND/NOT 的搜索限制为 1/5/25 req/s。超每日额度或超速率均返回 429。
- 计费：单实体查询 `/works/{id}` **免费**；list+filter **$0.10/千次**；`search=` **$1/千次**（即 1 次搜索 10 分钱额度）；`rerank=true` 每次 +$0.001；PDF 下载（content API）**$0.010/篇**。
- 硬限制：每 filter 的 OR 值 **100**、`per_page` 最大 **100**、`sample` 最大 **10000**、普通分页上限 **10000** 条结果。
- 响应头 `X-RateLimit-Limit / -Remaining / -Credits-Used / -Reset`（[authentication](https://help.openalex.org/api/authentication/)、[example-costs](https://help.openalex.org/access/example-costs/)）。
- **`mailto` polite pool 已废除**（2026-02 起）：`mailto` 被忽略，改用 API key（[deprecations](https://help.openalex.org/api/deprecations/)）。
- 端点：`/works`、`/authors`、`/sources`、`/institutions`，另有 `/topics`、`/publishers`、`/funders`、`/awards`、`/locations`、`/keywords`。

**实用 filter**（[filtering](https://help.openalex.org/api/filtering/)、[works attributes](https://help.openalex.org/data/works/attributes/)）
- `doi:` 短式与 URL 式都可、**大小写不敏感**，且匹配"本 work 的 doi **或任一 location 的 doi**"（所以能命中 preprint DOI 所属 work）；最多 100 个用 `|` 批量。
- `publication_year:`（支持 `2020-2024`）、`from_publication_date:`/`to_publication_date:`、`type:`、`cited_by_count:>N`、`is_oa:true`、`authorships.author.id:`、`authorships.institutions.id:`、`topics.id:`/`primary_topic.*`、`has_fulltext:`、`is_retracted:`、`corpus=core|expansion|all`。
- **不要用名字 filter**：文档明确要求先 `/authors?search=` 拿 ID 再 `authorships.author.id:` 过滤。
- **增量同步**：`from_created_date:` / `from_updated_date:`（"创建/更新于某日之后"），文档标注为 **Paid plans** 功能（[filtering#sync-filters](https://help.openalex.org/api/filtering/)）。
- 搜索：`search=` 搜 title+abstract+fulltext+keywords（$1/千次）；`search.title_and_abstract=` 仅标题摘要；旧的 `filter=fulltext.search:` 仍可用但已弃用，**`default.search` 是 `fulltext.search` 的弃用别名**（行为相同）。整条请求 URL 上限约 **4KB**（4094 字节），超长布尔式需分块后客户端取并集（[searching](https://help.openalex.org/api/searching/)）。
- `sort=`：`cited_by_count:desc`、`publication_date`、`publication_year`、`relevance_score`（必须配合搜索）（[sorting](https://help.openalex.org/api/sorting/)）。
- `select=`：只返回顶层字段（`select=id,doi,display_name` 可以，`select=open_access.is_oa` **报错**）；list 与单实体可用，group_by/autocomplete 不可用（[selecting-fields](https://help.openalex.org/api/selecting-fields/)）。

**摘要**：`abstract_inverted_index` 为倒排索引（词 → 位置数组），OpenAlex 出于法律原因不直接提供纯文本，需自行还原；覆盖率为 2022 年作品 >60%、2000 年前约 45%，偶有尾部噪声（章节标题等）（[works attributes](https://help.openalex.org/data/works/attributes/)）。还原法：建 `位置 → 词` 字典，按位置 0..max 取值拼接。

**引用图**：`referenced_works`（本 work 引用的 OpenAlex ID 列表）、`referenced_works_count`、`cited_by_count`、`cited_by_api_url` **未在当前文档中再出现（未查证是否仍返回）**、`related_works`（"近年主题最相近的论文"，算法生成，非引用关系）、`fwci`。文档未说明 `referenced_works` 有分页或截断上限（**上限未查证**）；但明确警告 `referenced_works` 可能短于原文参考文献表（引用匹配失败即丢弃，且很多 Crossref 记录根本不含参考文献）（[citations](https://help.openalex.org/data/works/citations/)）。

**标识与全文位置**：`ids` 含 `openalex`、`doi`、`mag`、`pmid`、`pmcid`（**无 arxiv 键，且自 2023-03 有文档以来从未有过**）；`indexed_in` 可为 arxiv/crossref/doaj/pubmed。`locations[]`、`primary_location`、`best_oa_location` 提供 `pdf_url`、`landing_page_url`、`license`、`license_id`、`version`（**`publishedVersion` / `acceptedVersion` / `submittedVersion`**）、`is_oa`、`is_published`、`source`、`doi`；另有 `/locations` 端点（约 6.5 亿条"副本"记录，可按 `work_id`、`source_id`、`version`、`ingested_at` 过滤）（[locations](https://help.openalex.org/data/locations/)）。`best_oa_location` 评分：必须是 OA，publisher > repository，publishedVersion > acceptedVersion > submittedVersion，有直链 PDF 优先（[works attributes](https://help.openalex.org/data/works/attributes/)）。

**身份与稳定性（2026-10-14 实测，独立复核过）**：

| 事实 | 证据 |
|---|---|
| `ids` 只有 5 个键，**不含 arXiv** | attributes 页原文；本机实测 3 条记录（新记录连 `mag` 都没有） |
| arXiv 副本只体现在 `locations[]`（`merge_key.arxiv`、`pmh:oai:arXiv.org:…`）与 arXiv 源 `S4306400194` / `indexed_in:["arxiv"]` | attributes 页 + 本机实测 |
| **预印本与正式版是同一条 Work**，不是两条 | 官方示例：arXiv DOI `10.48550/arxiv.1102.1523` 与期刊 DOI `10.1109/mcse.2011.37` → 同一条 `W2146292423`（5 个 location）；本机实测两篇同结论 |
| 预印本/正式版靠 **location 级 `version`** 区分；**不存在 `is_preprint_of` 字段** | 官方 works 页；⚠️ `related_works` **不是**这个链接（它是"主题相似"算法推荐） |
| **被合并/删除的 Work ID 返回 404，且无 `Location` 头**（不是 301） | 官方 [access/sync](https://help.openalex.org/access/sync/) 明说 404，另 4 个页面却说 301——**文档自相矛盾**。本机从官方删除日志取 5 个真实 ID 实测：**全部 404、无 Location**；存活对照 200 |
| **已不存在"合并前 ID → 存活 ID"的映射表** | 2025 年 Walden 切换后旧 `merged_ids/` 目录停止；官方指引改为"顺着 location ID 追" |
| 删除日志公开可下 | `https://openalex.s3.amazonaws.com/data/jsonl/works/deleted_ids.csv.gz`——实测 160.6 MB（压缩）、**52,309,193 条**、`work_id` 列是完整 URL、最新 `deleted_date` = 2026-09-23（滞后约 3 周） |
| 无 DOI 的记录**也会**拿到 Work ID | 官方："Any record with a DOI **or a usable title** is minted as a work of its own"；只有 PID 与标题都缺的才被丢弃 |
| **扩展语料（约 1.9 亿条）默认不出现在 API 结果里**，且自 Walden 切换后是**静态**的 | 官方 corpus 页；要取需 `corpus=all`。大量无 DOI 的机构库/DataCite 记录落在这里 |
| 已知会**重复铸条**：机构自铸 DOI 的记录会被当成另一篇论文 | 官方 repositories 页明确警示 |

**额度模型（2026-02 起）**：polite pool 已废除、`mailto` 被忽略，改为 API key + 按次计费：**有 key 免费 $1/天**（00:00 UTC 重置）；无 key **$0.10/天且同一出口 IP 共享**；**不含全文检索的 `filter` = $0.0001/次，任何全文检索 = $0.001/次**——`search=` 与带 `.search` 后缀的 filter（`filter=title_and_abstract.search:`）同价，分档的轴不是参数名（[EX-2026-10-16-source-contract](../实验/EX-2026-10-16-检索口径与来源校验.md) §1 实测）；**单实体 GET 免费，所有 4xx/5xx 免费**；速率上限 无 key 10 / 免费 key 30 / 年费档 50 req/s（[pricing](https://help.openalex.org/access/pricing/)、[authentication](https://help.openalex.org/api/authentication/)）。

**arXiv → OpenAlex 的两条桥（本机实测）**：① **可构造的 DataCite DOI** `10.48550/arXiv.<arXiv-id>` —— `filter=doi:10.48550/arXiv.<id>` 直接命中（$0.0001）；该 DOI 在 DataCite 里从 2006 年（含 `math/0601001` 老式 id）到当天新论文都存在。**但 REF-SOURCES §5 已记录过一次 DOI→错误记录的错配**，命中后必须校验标题/年份。② **Semantic Scholar** `GET /graph/v1/paper/arXiv:<id>` 无 key 返回 `externalIds = {MAG, ArXiv, DBLP, DOI, CorpusId}` —— **唯一显式给出 arXiv 映射的地方**，免费但共享池极易 429。注：Crossref **不认识**这些 DataCite DOI（实测 404）；arXiv API 自带的 `<arxiv:doi>` 是**作者手填**的（实测 3 篇中仅 1 篇有）。

**分页**：`per_page` 1–100（默认 25，旧 `per_page=200` 已弃用）；`page × per_page ≤ 10000`；超过则用 cursor：`cursor=*` → 读 `meta.next_cursor` → 传入下一次请求，直到 `next_cursor` 为 null。文档同时警告**不要用 cursor 拉全库**（需数天且加重服务器负担），全量应使用快照（[paging](https://help.openalex.org/api/paging/)）。

**更新频率**：持续性抓取、"数据库每日演进"（daily），但官网未给出明确的"最新数据滞后 N 天"指标。**机构库（含 arXiv）**：官方称"**每日、每个端点**"跑一轮（约 5000 个端点），且"今天提交的 deposit 通常在**一两天内**进入 OpenAlex"（[repositories](https://help.openalex.org/data/sources/repositories/)、[how-to/repositories](https://help.openalex.org/how-to/repositories/)）——**没有 arXiv 专属的滞后承诺**。本机实测：2026-10-13 发布的 arXiv 论文，到 2026-10-14 19:40 用 `filter=doi:10.48550/arXiv.<id>` 仍 `count=0`，而 2024 年的论文可命中 → **实际滞后 > 1.5 天**。`updated_date` 会因引用数重算而变化，做增量时要注意噪声。

## B. arXiv

- 端点 `http://export.arxiv.org/api/query`（GET 或 POST），参数 `search_query`、`id_list`、`start`、`max_results`（默认 10）、`sortBy`、`sortOrder`。
- `search_query` 前缀字段：`ti`、`abs`、`au`、`cat`、`all` 等；布尔 `AND`/`OR`/`ANDNOT`，短语用 `ti:"..."`。`sortBy` 仅 **relevance / lastUpdatedDate / submittedDate**，`sortOrder` = ascending/descending。
- 硬上限：单次调用 `max_results` 总量 **30000**，但**每次最多 2000**（用 `start` 切片，如 `start=6000&max_results=2000`）；>30000 返回 HTTP 400。文档建议 >1000 条时收窄查询；批量元数据应用 OAI-PMH。
- **速率：每 3 秒不超过 1 个请求，且只用单个连接**；该限制按"你控制的所有机器"合计，不得用多机绕过（[tou](https://info.arxiv.org/help/api/tou.html)）。手册另表述为"连续调用之间加入 3 秒延迟"（[user-manual](https://info.arxiv.org/help/api/user-manual.html)）。文档未规定必须发送特定 User-Agent（**未查证**）。
- Atom 字段：`id`（含版本号，如 `http://arxiv.org/abs/1501.00001v1`）、`published`、`updated`、`summary`、`author/name`、`category@term`、`arxiv:doi`、`arxiv:journal_ref`、`arxiv:comment`、`arxiv:primary_category`；`<link rel="alternate">` 指向 abs 页，通过 `title="pdf"` 的 link 得 PDF（约定 `https://arxiv.org/pdf/{id}`）。无版本号的 ID 指**最新版本**；arXiv 不保证 ID 大小写语义（[arxiv_identifier](https://info.arxiv.org/help/arxiv_identifier.html)）。
- 数据刷新：提交有 24 小时周期，**新文章在当日午夜后才对 API 可见**，`<feed><updated>` 反映当天午夜——同一查询一天调用一次即可，需缓存。
- 批量：S3 requester-pays 桶（`s3://arxiv/pdf/...`、`src/...`），按 ~500MB tar 分块 + manifest.xml，**约每月更新**；2025-04 全量约 **9.2 TB**（PDF 2.7 TB / 2023-03）。不得自行托管 arXiv PDF 对外分发（[bulk_data_s3](https://info.arxiv.org/help/bulk_data_s3.html)）。

## C. Semantic Scholar Graph API

- **无 key：全平台共享 1000 requests/second**（所有未认证用户共享同一个池），高峰期会进一步限流，常见 429；**有 key：1 RPS（全端点），经审核可略高**（[product/api](https://www.semanticscholar.org/product/api)、[tutorial](https://www.semanticscholar.org/product/api/tutorial)）。
- 端点：`/graph/v1/paper/search`（relevance，`query`+`limit`+`offset`，limit 上限 100）、`/graph/v1/paper/search/bulk`（布尔语法 + token 分页，官方建议"大多数情况用 bulk"）、`/graph/v1/paper/{id}/citations`、`/references`、`/paper/batch`（POST，批量取详情）、`/author/batch`。base URL `https://api.semanticscholar.org/graph/v1`；key 以 `x-api-key` 头传递。
- bulk 语法：`|` = OR、`+` = AND、`-` = NOT、`"..."` 短语、`term*` 前缀、`term~3` 编辑距离、`"blue lake"~3` 邻近；可选 `sort=paperId|publicationDate|citationCount`、`year=2023-`、`fieldsOfStudy`、`minCitationCount`、`openAccessPdf`、`venue`、`publicationTypes`。**bulk 每页约 1000 条（第三方资料一致，官方页面未明示 → 未查证）**；它匹配 title + abstract。
- 字段：`abstract`、`tldr`、`externalIds`、`citationCount`、`influentialCitationCount`、`openAccessPdf`（含 `url`/`status`）、`publicationDate`、`publicationTypes`、`fieldsOfStudy`、`authors`、`references`/`citations` 等（[tutorial](https://www.semanticscholar.org/product/api/tutorial)）。
- ID 类型：S2 文档说明 `paper_id` 支持 S2 paperId(CorpusId)、`DOI:xxx`、`ARXIV:xxxx`、`PMID:xxx`、`MAG:xxx`、`ACL:xxx`、`URL:xxx`（**具体清单以 JS 渲染的 api-docs 为准，我未能以纯文本抓取 → 部分未查证**）。
- 引用/被引：`citations`/`references` 返回 `data` + `offset` + `next`，单页上限 **1000**，用 `offset` 翻阅（每篇论文最多 1000 条，`limit` 最大 1000）。**总返回条数是否截断于 1000 未查证**。
- 批量数据：Datasets API（`/datasets/v1`）提供 JSON 快照 + incremental diffs，适合自建库以摆脱 1 RPS。

## D. 其他源与工具

- **Crossref**：[访问层级与限速](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/) —— Public **5 req/s、并发 1**；Polite（带 `mailto` 或 `agent` 头）**10 req/s、并发 3**；Plus **150 req/s、无限并发**；限速值同时由响应头 `x-rate-limit-limit`、`x-rate-limit-interval`、`x-concurrency-limit` 给出；超限 429，人工封禁返回 403。官方请求"指定识别身份的 User-Agent"。定位：DOI 注册元数据与卷/页等书目细节。实测 `/works/{doi}` 返回 `title`、`container-title`、`volume`、`page`、`issued/published`、`license`、`link[]`（含 `content-version: vor` 的 PDF/XML/HTML，`intended-application: text-mining`），并且**参考文献默认包含**（实测该 DOI 返回 `reference-count: 52` 与 52 条 `reference` 数组；`reference-count`/`references-count` 字段也在响应中）。
- **Unpaywall**：`https://api.unpaywall.org/v2/{doi}?email=you@real.domain`，**email 为必需**：用测试邮箱会得到 **HTTP 422**「Please use your own email address in API calls」；实测返回 `best_oa_location`、`oa_locations[]`、`oa_status`、`is_oa`、`genre`、`published_date`、`journal_is_in_doaj`、`z_authors`，每个 location 含 `url_for_pdf`、`url_for_landing_page`、`host_type`、`version`、`license`（[products/api](https://unpaywall.org/products/api)）。注意实测中 `url_for_pdf` 常为 `null`（该文只有 landing page）。**即使 email 正确，官方页面为 JS 渲染，文档化速率数字我未能抓取 → 每日调用上限未查证**。
- **Europe PMC**：`https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=...&format=json&resultType=core|lite`，实测可用、支持 DOI/cursorMark、返回 `pmid/pmcid/doi/isOpenAccess/inEPMC/citedByCount/hasReferences/fullTextUrlList`，并在同一响应给出 PMC 全文与 `commentCorrectionList`（预印本关联，如 `PPR…` "Preprint in"）（[developer docs 被 Cloudflare 拦截，速率数字未查证](https://europepmc.org/RestfulWebService)）。定位：生物医学 + 预印本 + 全文检索的多合一补充。
- **PubMed E-utilities**：`https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=...&retmode=json` 实测正常（返回 `count`、`idlist`）。**官方限额页被 reCAPTCHA 拦截，无 key 3 req/s、有 key 10 req/s 这两个数字未查证**（应查 [NBK25497](https://www.ncbi.nlm.nih.gov/books/NBK25497/)）。PMC 侧官方明确：批量抓取只允许 E-Utilities、OAI-PMH、BioC 等指定服务，其他自动化批量抓取被禁止（[PMC For Developers](https://pmc.ncbi.nlm.nih.gov/tools/developers/)）。
- **CORE**：免费、**无需注册**即可用（注册获得更快速率，机构会员更快），公开标称 **每 10 秒 1 次批量请求或 5 次单请求**；语料 4.52 亿篇可检索、5700 万全文（[services/api](https://core.ac.uk/services/api)）。价值：仓库级全文。
- **DOAJ**：公开 Search API（v4）**无需 key**，key 只面向投稿出版商；**每 2 秒 2 次请求的速率上限**（允许突发 5 个排队）(api docs)。价值：纯 OA 期刊/文章白名单与 APC 信息。
- **BASE**：官网在本次抓取中不可达（**覆盖率与 API 条款未查证**）。判断：若已有 OpenAlex（已聚合 DOAJ/Crossref/PubMed/arXiv/仓库），BASE 的边际价值主要在德语区仓库覆盖，**优先级最低**。
- **DOI→合法 PDF 路径（OA 优先）**：① Crossref `link[]` 中 `content-version: vor` + `intended-application: text-mining`（出版商许可的 TDM 链接）；② Unpaywall `best_oa_location.url_for_pdf`（可能是 null，此时退到 `url_for_landing_page`）；③ OpenAlex `best_oa_location.pdf_url` / `openAccess.oa_url`（`oa_url` 可能只是落地页）；④ arXiv/PMC 直链；⑤ OpenAlex content API 缓存的 PDF（$0.01/篇，免费额度约可下 100 篇/天）。坑：`pdf_url` 为 null 很常见；落地页 ≠ PDF（需再解析）；`content-version` 可能是 am（accepted manuscript）而非 vor；直接抓出版商 PDF 很可能 403 或触发反爬；arXiv 明确禁止自行托管分发 PDF。

## E. 标识与去重的事实依据

- **DOI**：OpenAlex 明确说明 `doi:` filter **大小写不敏感**，短式（`10.7717/peerj.4375`）与 URL 式（`https://doi.org/10.7717/peerj.4375`）等价（[works attributes](https://help.openalex.org/data/works/attributes/)）。**DOI Handbook 层面的"前缀大小写不敏感、后缀应视为不透明字符串不得改写"这一条我未抓到官方原文 → 未查证**，但 OpenAlex 同一 work 可同时有 preprint DOI 与 published DOI，因此**规范化只能做"去 `https://doi.org/` / `doi:` 前缀 + trim + 大小写折叠用于比较"，不得改写后缀字符**（SICI 等旧式后缀的官方规范本轮未查证）。
- **arXiv**：ID 形态 `arXiv:YYMM.number{vV}`（0704–1412 为 4 位序号，1501 起 5 位；2007-03 及以前为 `archive[.class]/YYMMNNN`）；`vV` 是字面 `v` + 版本号，**无版本号 = 最新版本**；ID 本身不含分类信息（[arxiv_identifier](https://info.arxiv.org/help/arxiv_identifier.html)）。
- **预印本 ↔ 正式版关联**：arXiv 侧提供 `arxiv:doi` 与 `arxiv:journal_ref`（作者/出版方后续回填，可能缺失或滞后）。OpenAlex 侧**不做"预印本→正式版"的字段级指针，而是把多个副本合并成同一个 work**：`locations[]` 中每个副本各带 `version`（publishedVersion/acceptedVersion/submittedVersion）、`doi`、`is_published`；文档明确"一个 work 可有多个 DOI（预印本 DOI 与发表 DOI），`doi` 字段只保存其中一个（优先 Crossref 的、发表版优于预印本）"（[locations](https://help.openalex.org/data/locations/)、[works attributes](https://help.openalex.org/data/works/attributes/)）。**因此"打印本→正式版"的映射应通过 OpenAlex work + locations 实现，而不是靠单一字段。**
- **ID 映射枢纽**：OpenAlex `ids`（openalex/doi/mag/pmid/pmcid）适合做 DOI/PubMed 枢纽；但**不含 arXiv 键**（arXiv 副本体现为 `locations` 里的 `pmh:oai:arXiv.org:...` 命名空间 id，且 location id 大小写敏感）。S2 `externalIds` + 多 ID 接受格式可补 arXiv/MAG/ACL。务实做法：**以 DOI（规范化后）为主键，OpenAlex work id 为聚合键，arXiv id 与 PMID/PMCID 作为辅助别名**。
- **标题归一化去重的已知风险**：OpenAlex 官方描述其 location→work 匹配机制为"有共享 PID 则视为同一 work（确定路径），无共享 PID 时用**标题归一化 + 作者名**的模糊匹配（不确定路径）"，并说明预印本/仓库副本多数走模糊路径（[works overview](https://help.openalex.org/data/works/)）。**"缩略、副标题分隔符、希腊字母、连字符、多语言标题"的具体规范化清单官方未给出 → 未查证**；这些属于工程实践（Unicode NFKC、去副标题、破折号/连字符统一、大小写折叠、LaTeX 转义展开），不要声称有权威依据。

## F. Zotero 侧的写入与集成（关键可行性判断）

- **本地 HTTP API**：`http://localhost:23119/api/`，需在"设置 → 高级 → 允许其他应用与 Zotero 通信"中开启，否则 **403**。读取**无需认证**；本地 API **无速率限制**、默认**不分页**（返回全部匹配对象）、仅支持 API v3、**不支持 Atom（501）**、仅能访问本机登录用户的数据（user id 传 `0` 或真实数字 id）。**官方文档明确：写入能力是 Zotero 10+ 才有的**（[local_api](https://www.zotero.org/support/dev/web_api/v3/local_api)、[basics](https://www.zotero.org/support/dev/web_api/v3/basics)）。→ 对 Zotero 7/8，本地 API **只读**。
- **Zotero 10 新增本地写入**（10.0 于 **2026-08-17** 发布，changelog 的开发条目为 "Local API write support"）：支持 items/collections/saved searches 的 `POST/PUT/PATCH/DELETE`，另支持标签删除、全文写入（`PUT …/items/{key}/fulltext`）、三步式文件上传（本地上传到 `/api/local/uploads/{uploadKey}`，单文件 <4 GB，仅 `imported_file`/`imported_url`）、`searches/{key}/items` 执行已保存搜索、`items/{key}/file` 302 跳转到 `file://`。
  - **授权**：`POST /api/local/authorize` 带 `{"appName": …}`，Zotero 弹窗显示 "Allow / Always Allow / Deny"；成功后返回 `{"key": "<32字符>", "remember": bool}`。**默认是单次密钥**：首个成功校验的写请求即消耗掉，之后的写需重新授权；选 Always Allow 才可复用。用户拒绝 → **403 `{"denied": true}`**；为防弹窗骚扰，**每分钟最多 5 次会弹窗的请求**，超出返回 429 + `Retry-After`。密钥**无 scope**（可写该用户可编辑的任何库），可在"设置 → 高级 → Clear Write Authorizations"整体撤销。
  - **Server ID 与版本**：Zotero 10+ 每个响应含 `Zotero-Server-ID`；写请求**必须**带该头（否则 **428**），不匹配则 **412**。对象版本改为**本地版本**，与 Web API 版本、其他实例版本**无关**；空 `GET /api/` 即可取 ID，且客户端缓存数据应按 server ID 分区。
  - 本地写入是"普通本地改动"：立即在 UI 可见，下次同步会上传（[local_api](https://www.zotero.org/support/dev/web_api/v3/local_api)、[changelog](https://www.zotero.org/support/changelog)）。
- **Web API 写入**（`https://api.zotero.org`，仅 HTTPS）：需要带写权限的 key（`Zotero-API-Key` 头 / `Authorization: Bearer` / `key=` 参数），权限粒度见 OAuth 预置项：`library_access`、`notes_access`、`write_access`、`all_groups=none|read|write`；OAuth 1.0a 换取的 key 长期有效。写入格式：`POST /items`（数组，≤**50** 个对象/请求）、`PUT`（全量，未列字段会被清空）、`PATCH`（仅改字段）、`DELETE`（批量删除 ≤50）。版本前置条件用 `If-Unmodified-Since-Version` 或对象 `version`，失败 **412**；`Zotero-Write-Token`（32 位随机）防重复提交（Web 端缓存 12 小时，本地端仅内存缓存，重启即失效）；超量 **413**。
  - **速率**：官方未公布数字化的读写配额，而是给出 **`Backoff: <秒>` 响应头**（任何响应都可能出现，含成功响应）、**429 + `Retry-After`**、以及"客户端一般**不超过 4 个并发请求**"的要求；官方建议对大量对象用**批量写入**而非逐个更新（[basics#rate-limiting](https://www.zotero.org/support/dev/web_api/v3/basics)、[write_requests](https://www.zotero.org/support/dev/web_api/v3/write_requests)）。**读/写分别的具体 RPS 数字未查证（官方未公布）。**
- **直接改 zotero.sqlite 的官方立场**：官方文档标题即为"Direct Access…"，明确要求 **只读访问**；理由是 Zotero 的**缓存层破坏了 SQLite 的正常文件锁**，运行中修改**极易导致数据库损坏**，且绕过 Zotero 与 Web API 的数据校验与外键完整性检查；即使关闭 Zotero 再改也不被认可；并提醒**数据库结构可能随版本变化**（[direct_sqlite_database_access](https://www.zotero.org/support/dev/client_coding/direct_sqlite_database_access)）。官方替代方案是 Web API、JavaScript API、本地 API。是否对"外部修改后的数据库"有专门检测/修复机制：**未查证**（10.0.2 仅提到"启动时对数据库损坏或只读数据库给出更清晰的报错"）。
- **三种附加自定义数据的方式**：官方对"需要额外信息"的建议是**使用所有条目类型都有的 `Extra` 字段**（而不是改数据结构，改结构会破坏同步、导入与引用格式化）（[direct_sqlite…](https://www.zotero.org/support/dev/client_coding/direct_sqlite_database_access)）。`Extra` 是普通可写字段，随同步上传、可被搜索；child note 是独立 note 条目，可用 `item.getNotes()` 读取，HTML 内容；attachment 是真实文件（上传受 4 GB/类型限制）。**三者的字符上限官方未给出（未查证）**；已知限制：`Extra` 是纯文本（无结构/无长度保证）、note 内容为 HTML、attachment 需走文件上传流程且本地 API 不支持分块差量上传（`PATCH …/file` → 405）。
- **本地全文索引**：可通过 `q` + `qmode=everything` 让 Zotero 的快速搜索包含全文内容（默认 `qmode=titleCreatorYear` 只搜标题与创建者字段）；也可用 `GET /items/{itemKey}/fulltext` 直接取某附件的已索引全文，返回 `content` + `indexedPages/totalPages`（PDF）或 `indexedChars/totalChars`（文本），`indexedPages < totalPages` 可判断索引是否完成；`GET /fulltext?since=<version>` 增量列出有新全文的对象；`PUT /items/{key}/fulltext` 可写入全文（Zotero 10+ 本地版也可）。Zotero 10 起全文检索显著加速、CJK 支持整短语匹配、并改善了大写/重音不敏感搜索（[fulltext_content](https://www.zotero.org/support/dev/web_api/v3/fulltext_content)、[changelog](https://www.zotero.org/support/changelog)）。**底层 fulltextItems/fulltextWords 表的字段级细节与"如何触发重建"官方文档未说明（未查证）**——可行路径是走本地 API 的全文端点，而不是直接读表。

## 对构建检索 skill 最关键的 10 条事实

1. **OpenAlex 的 polite pool 已死（2026-02 起 `mailto` 被忽略）**，现在必须用免费 API key；免费额度是**每天 $1**，无 key 只有 **$0.10/天** → 不用 key 基本做不了任何批量检索。
2. **OpenAlex 按次计费，不是按请求数免费**：单实体查询免费、list+filter $0.10/千次、**`search=` $1/千次**、PDF $0.01/篇。$1/天 ≈ 1000 次搜索或 1 万次过滤查询；`per_page=100` + `select=` 是省额度的关键。
3. **OpenAlex 硬上限**：OR 值 ≤100、`per_page` ≤100、普通分页 ≤10000 条、请求 URL ≤~4KB；超过必须用 `cursor=*` 游标翻页。
4. **增量同步用 `from_created_date` / `from_updated_date`，但文档标注为 Paid plans 功能** → 免费层做"每日增量"的可行性需先实测（未验证）。
5. **OpenAlex 的 `abstract_inverted_index` 是倒排索引**，必须按位置还原；且 `referenced_works` 会短于真实参考文献表（匹配失败即丢，很多 Crossref 记录无参考文献）。
6. **arXiv 只允许"每 3 秒 1 个请求、单连接"**，单次 ≤2000 条、总量 ≤30000 条，且新文章只在**午夜后**才可见 → 必须缓存、一天一次足够。
7. **S2 无 key 是"全局共享 1000 RPS"，有 key 只有 1 RPS**（且全端点共享）。要批量就应改用 `/paper/search/bulk` 或 Datasets 快照，而不是逐篇调用。
8. **Crossref 是唯一给出明确分层数字的源**：Public 5/s、Polite 10/s（并发 3）、Plus 150/s；带上 `mailto` 与识别性 User-Agent 是最低成本的速度提升。
9. **Unpaywall 必须用真实 email**（假邮箱直接 422），且 `url_for_pdf` 经常为 null → DOI→PDF 需要多级回退（Crossref TDM link → Unpaywall → OpenAlex `best_oa_location` → arXiv/PMC → OpenAlex content API）。
10. **Zotero 写入的可行性取决于版本**：Zotero 7/8 本地 API **只读**；**Zotero 10（2026-08-17 发布）+ 本地 API 支持写**，但需要用户弹窗授权（默认**单次密钥**，每分钟最多 5 次弹窗）、必须带 `Zotero-Server-ID`（否则 428）、版本号是本地版本（与 Web API 无关，缓存必须按 server ID 分区）。**直接改 `zotero.sqlite` 被官方明确反对（只读、否则易损坏数据库）。**