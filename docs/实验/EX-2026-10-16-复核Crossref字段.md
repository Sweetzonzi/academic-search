---
doc-id: "EX-2026-10-16-crossref-fields"
kind: "experiment"
date: "2026-10-16"
title: "Crossref lookup 字段复核"
---

# 实验记录：Crossref lookup 字段复核

> 实验日期：2026-10-16（本机，Asia/Shanghai）。目的：用真实请求复核 [IFACE-SOURCE §3.3](../接口/数据源适配.md) 取用的六个字段与 §4 的 `type` 映射。
> 一句话结论：**六个字段的取值形状全部与文档一致**，四种 `type` 实测值也都映射正确；但发现四处偏差——最要紧的一处是 **`title` 的 JATS 标记没有剥**，而它正是 `title_key` 的输入。

**(a) 取样。** 16 个 DOI，逐个单实体 GET `https://api.crossref.org/works/<doi>`（免费、无需 key）。15 个有响应，1 个 404（见 (f)）：

| `type` | 样本数 | 来源 |
|---|---|---|
| `journal-article` | 4 | PLOS ONE、Nature、Proc. R. Soc. A、Acta Astronautica |
| `proceedings-article` | 3 | ASCC 2026、ICCA 2026 ×2 |
| `book-chapter` | 2 | Springer |
| `posted-content` | 3 | Preprints.org |
| **`other`** | 3 | **Wiley 书章** |

**(b) 六个字段的取值形状：与文档一致。**

| 字段 | 实测形状 | 解析结果 |
|---|---|---|
| `title` | **数组**；可能带内联标记与填充空白 | `_first()` 取 `[0]` ✓ |
| `container-title` | **数组**；同样可能带填充空白 | `_first()` 取 `[0]` ✓ |
| `issued.date-parts` | `[[2017, 2, 7]]`，年/月/日齐全 | `2017-02-07` ✓ |
| `author[]` | `given` + `family` 两字段；**可能是空数组** | 拼接 ✓；书章样本 0 作者 |
| `type` | 字符串 | §4 映射 ✓，覆盖见 (d) |
| `abstract` | 字符串，**带 JATS 标记** | 剥标记 ✓，但见 (c) 第 2 条 |

**(c) 四处与文档不符。**

1. **`title` 的 JATS 标记没有剥，而它进 `title_key`。** 实测原文两例（同一本 Wiley 书的两章）：

   - `'<scp>GenAI</scp>\n                    Multi‐agent Systems'`
   - `'Integrated\n                    <scp>CAV</scp>\n                    Agent Architectures with\n                    <scp>MCP</scp>\n                    and\n                    <scp>A2A</scp>'`

   `parse_work()` 的 `strip_markup` 只作用于 `abstract`（`src/academic_search/sources/crossref.py` 60–62 行），`title` 原样入库。这条链路的后果比"显示难看"重：`title` → `title_key` → L2 去重判据，带 `<scp>` 的标题派生的键与干净标题不同，**同一篇论文在两个源下会被判成不同作品**。

   [IFACE-SOURCE §3.3](../接口/数据源适配.md) 的括号里已经写了「实测污染过标题」，但正文那句「必须先剥标记」只点了 `abstract`，实现也就只剥了 `abstract`。

2. **`strip_markup` 不折叠空白。** `contenthash.strip_markup('<jats:title>Abstract</jats:title>\n<jats:p>\n  Array programming…')` 返回 `' Abstract \n \n  Array programming…\n '`：换行与连续空格原样保留，`<jats:title>Abstract</jats:title>` 还留下字面的 `Abstract` 一词作前缀。实测 Nature 那篇落库后的摘要开头是 `'Abstract \n                   \n Array programming provides a powerful, c…'`。

3. **`container-title` 带填充空白。** `'Generative                     AI                     for Connected and Autonomous Vehicles'`——它会进 `record.venue`。白名单比对前的 `normalize_venue()` 会折叠空白，但**落库的是原文**。

4. **`mailto` 参数没有生效。** `lookup(client, doi, mailto=…)` 只在 `mailto` 非空时设了 `Accept: application/json`（同文件 84–85 行）——`mailto` 既没进查询串也没进 `User-Agent`，所以进不了 Crossref 的 polite pool。

**(d) `type` 映射的实测覆盖。**

| 规范值 | Crossref | 实测 |
|---|---|---|
| `article` | journal-article | ✓ 4 例 |
| `article` | proceedings-article | ✓ 3 例 |
| `preprint` | posted-content | ✓ 3 例 |
| `chapter` | book-chapter | ✓ 2 例 |
| `chapter` | reference-entry | 未采到样本 |
| `thesis` | dissertation | 未采到样本 |
| `paratext` | editorial / erratum / peer-review / component | 未采到样本 |
| `other` | 其余 | ✓ 3 例——**Wiley 书章实测给 `other`** |

最后一行值得单独记：`10.1002/9781394426713.ch11` 从 DOI 形态（`.ch11`）与书名看都是书章，Crossref 却给 `other`。**`type` 不能全信**，同一类出版物在不同出版社的投放口径不一致；把它当硬判据（例如靠 `type` 判章节）会漏。

**(e) 摘要覆盖率。** 15 个响应里 **5 个**带 `abstract`（33%），比 [EX-2026-10-14-identity §6](EX-2026-10-14-身份与去重.md) 记的「Crossref 侧约 10%」高。但本批是**定向取样**（含 3 个 Preprints.org 预印本，它们全部带摘要），不能当总体覆盖率。可用的结论只有一条：**`lookup` 兜 DOI 时不能指望 Crossref 补上摘要**。

**(f) 反例：arXiv 的 DOI 不在 Crossref。** `10.48550/arXiv.1803.10122` 返回 **404**（arXiv 的 DOI 注册在 DataCite）。`lookup()` 对 404 返回 `None`（同文件 88–91 行），兜底逻辑正确；这条也是「别把 Crossref 当万能兜底」的实测依据。

**(g) 复现。**

```powershell
# 带标记的标题
curl.exe -s "https://api.crossref.org/works/10.1002/9781394426713.ch11?mailto=you@example.com" |
  Select-String -Pattern '"title"|"type"'
# 带 JATS 标记的摘要
curl.exe -s "https://api.crossref.org/works/10.1038/s41586-020-2649-2?mailto=you@example.com" |
  Select-String -Pattern '"abstract"'
# 反例：Crossref 里没有
curl.exe -s -o NUL -w "%{http_code}`n" "https://api.crossref.org/works/10.48550/arXiv.1803.10122"
```

**(h) 四处偏差落地后的样子（2026-10-16 追加）。** (c) 记的四处已经改完，实测结果记在 [EX-2026-10-16-source-contract](EX-2026-10-16-检索口径与来源校验.md) §16(c)：`title` / `container-title` 剥标记并折叠空白、摘要里的 `jats:title` 整段删掉、`mailto` 进查询串与 `User-Agent`。本节 (c) 第 3 条提到的 `normalize_venue()` **已随 [ADR-0060](../决策/ADR-0060.md) 删除**——venue 名单不再比对名字字符串，改判「载体 source id 集合 ∩ 名单」；`venue` 列仍按原文落库，它只用于展示。
