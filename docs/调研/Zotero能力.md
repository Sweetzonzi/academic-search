---
doc-id: REF-ZOTERO
measured: 2026-10-14
title: Zotero 能力边界
---

# Zotero 能力边界

> 依据 Zotero 官方文档与本机 9.0.4 / schema 实测；升级 10.x 后需复核「写入通道」一节。

## 1. 写入通道（三选一）

| 路线 | 前提 | 能力 | 来源 |
|---|---|---|---|
| **本地 API 写入** | **Zotero 10.0+**（10.0 于 2026-08-17 发布；当前 10.0.6，2026-10-07） | items / collections / saved searches 的 `POST`/`PUT`/`PATCH`/`DELETE`、标签删除、全文写入、三阶段文件上传（<4 GB，仅 `imported_file`/`imported_url`） | 文档（Local API、版本历史） |
| Web API | zotero.org 账号 + 开启同步 + API key | 同上（云端路径）；官方建议并发 ≤4，遇 `Backoff` / `429` 退避；批量写 ≤50 对象/请求 | 文档（Write Requests、Basics） |
| 连接器通道 | Zotero 运行中（**9.x 亦可用**） | `POST /connector/import`（BibTeX/RIS/CSL JSON 等字符串）、`POST /connector/saveItems`（Zotero JSON 数组）、`saveSnapshot`、`saveAttachment`、`updateSession`、`getSelectedCollection` | 源码 `chrome/content/zotero/xpcom/server/server_connector.js`：`saveItems` 在 293-344 行、`import` 在 794-846 行；保存目标取自 UI 当前选中集合或 `lastViewedFolder`（30-66 行） |

**本地 API 写入的授权语义**（文档）：`POST /api/local/authorize` 带 `Zotero-Server-ID` 头，弹窗 "Allow / Always Allow / Deny"；返回 `{key, remember}`；**默认单次 key**（首个成功写请求即消耗），"Always Allow" 才可复用；拒绝 → `403 {"denied":true}`；每分钟最多 5 次会弹窗的请求，超出 429 + `Retry-After`。

**本地 API 其他差异**（文档）：

- 读请求**无需认证**、无速率限制、离线可用；
- 写请求必须带 `Zotero-Server-ID`（缺 → `428`，不匹配 → `412`）；
- Zotero 10+ 的对象版本是**本机局部版本**，与 Web API 版本无关；官方要求"跨运行存数据的客户端按 server ID 分区"；
- **默认不分页**（一次返回全部匹配），需显式传 `limit`/`start`；
- 支持执行已保存搜索：`<prefix>/searches/<key>/items`；
- 附件下载：`<prefix>/items/<key>/file` 返回 302 到 `file://` URL；
- 全文：`GET/PUT <prefix>/items/<key>/fulltext`，`POST /fulltext` 批量上限 10，`GET /fulltext?since=`；
- 不支持 Atom；`PATCH …/file` 分块上传返回 405。

## 2. 对象模型

- Zotero 的"元数据条目"与"附件"是**两个独立 item**（`items` + `itemTypes` 区分；附件另有 `itemAttachments.parentItemID`）。**没有任何字段要求条目必须有附件**；`fulltextItems` 只为已索引附件建行。
- `itemData` 是固定字段集的 EAV：`itemData(itemID, fieldID, valueID)`，**无 `key`/`version`/`synced`/`libraryID` 列**，必须 join `items`（实测 `PRAGMA table_info`）。
- 附件的 `linkMode`：0 = 导入文件（本机 6/6 均为 0）。

### 2.1 Zotero 可索引与可标注的格式（源码依据）

| 格式 | 全文索引 | 可标注 | 依据 |
|---|---|---|---|
| PDF | ✅（走 pdftotext 转换器） | ✅ | `xpcom/fulltext.js:226-231` |
| **HTML** | ✅ | ✅ | 同上；`xpcom/data/item.js:2022`：「标注父项必须是 PDF、EPUB 或 HTML 快照」 |
| EPUB | ✅（专门流程） | ✅ | `fulltext.js:412-434`、`541-546` |
| text/plain | ✅（带 charset 时） | ❌ | `fulltext.js:559-567` |
| **Markdown `.md`** | **未验证**（非一等类型，可能落入 text/plain 分支也可能被跳过） | ❌ | 无对应的 MIME 处理分支 |

**含义**：想让文本版论文在 Zotero 里**被搜到、被标注**，附件应当是 **HTML 快照**；Markdown 属于"给 LLM 读"的形态，放 sidecar 的字节层（`works/` 投影）更合适。

## 3. Extra 字段的真实行为

Zotero 会**解析** `Extra` 字段内容：`Zotero.Item.prototype.getExtraField`（源码 `xpcom/data/item.js:311-312`）调用 `Zotero.Utilities.Internal.extractExtraFields()`；保存时会做字段迁移与规范化（`item.js:5375-5679`，包括"未知字段存入 Extra"与"从 Extra 迁移到正式字段"）。**结论：Extra 不是自由文本框**，其中的 `键: 值` 行会被解释，内容还会随导出进入 BibTeX 等格式。

## 4. 直接修改 `zotero.sqlite`

官方立场是**只读**：Zotero 的缓存层会破坏 SQLite 文件锁，**运行中修改极易损坏数据库**，且绕过数据校验与外键检查（文档：Direct SQLite Database Access）。本项目遵守该约束：**读用副本，写走官方通道**。

## 5. Zotero 10.0 的其他相关变化（文档）

撤销/重做、批量编辑、全文检索显著加速、中日韩**整短语**检索与重音不敏感检索、数据库自动压缩；开发者条目明确列出 "Local API write support"。升级会迁移数据库结构（本机当前 `databaseSchema=37`），**不可逆**。

---

