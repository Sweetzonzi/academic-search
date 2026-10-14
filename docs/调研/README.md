---
doc-id: REF-INDEX
kind: reference
title: 调研索引
---

# 调研索引

> 本目录装「外部与本机的事实」，不装结论与决策。每条事实标注实测日期；换机器、升级依赖或外部口径变化后重测。

| 文档 | 内容 | 何时复核 |
|---|---|---|
| [本机环境](本机环境.md) | Python/依赖/路径/本机库规模 | 换机器或改依赖 |
| [Zotero 能力边界](Zotero能力.md) | 写入通道、对象模型、Extra 行为、10.0 变化 | 升级 Zotero |
| [数据源事实与配额](数据源事实.md) | 数据源分工、额度实测、元数据缺陷、格式可得性 | 配额/计费口径变化 |
| [SQLite / FTS5 中文检索](SQLite中文检索.md) | 分词器与召回实测 | 换分词方案 |
| [DSH 能力事实](DSH能力.md) | skill 装载根、上下文成本、schedule 语义 | 宿主升级 |
| [可复用资产](可复用资产-zotero-mcp.md) | zotero-mcp 可复用清单与许可证边界 | 上游升级 |
| [公开 API 事实](参考资料-公开API事实.md) | 端点、字段、配额与逐条来源 URL | 外部口径变化 |

---

## 来源清单

**本地代码与文档**

- `D:\Files\Project_Paper\zotero-mcp` —— v0.14.1，HEAD `0faf1b2`（2026-10-07）
- `D:\Files\Project_Paper\zotero` —— HEAD `70c4cadab`（2026-05-22）；连接器端点与 Extra 字段解析逻辑来源
- [参考资料：公开 API 事实](参考资料-公开API事实.md) —— 端点、字段、配额与逐条来源 URL（含"未查证"标注）

**官方文档**

- [Zotero Local API](https://www.zotero.org/support/dev/web_api/v3/local_api)（本地写入授权、`Zotero-Server-ID`、版本语义、文件上传、全文写入）
- [Zotero 版本历史](https://www.zotero.org/support/changelog)（10.0 于 2026-08-17 发布并新增本地 API 写入）
- [Zotero: Direct SQLite Database Access](https://www.zotero.org/support/dev/client_coding/direct_sqlite_database_access)（只读立场）
- [Zotero Web API: Write Requests](https://www.zotero.org/support/dev/web_api/v3/write_requests) / [Basics](https://www.zotero.org/support/dev/web_api/v3/basics)
- [OpenAlex 鉴权](https://help.openalex.org/api/authentication/) / [弃用说明](https://help.openalex.org/api/deprecations/) / [计费示例](https://help.openalex.org/access/example-costs/) / [分页](https://help.openalex.org/api/paging/) / [过滤](https://help.openalex.org/api/filtering/) / [数据构建](https://help.openalex.org/data/how-its-built/)
- [arXiv API 使用条款](https://info.arxiv.org/help/api/tou.html) / [用户手册](https://info.arxiv.org/help/api/user-manual.html) / [标识符规范](https://info.arxiv.org/help/arxiv_identifier.html) / [批量数据](https://info.arxiv.org/help/bulk_data_s3.html)
- [Crossref REST API 鉴权与速率](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/)
- [Semantic Scholar API](https://www.semanticscholar.org/product/api) / [Unpaywall API](https://unpaywall.org/products/api) / [CORE API](https://core.ac.uk/services/api) / [DOAJ API](https://doaj.org/api/docs) / [PMC 开发者政策](https://pmc.ncbi.nlm.nih.gov/tools/developers/)
