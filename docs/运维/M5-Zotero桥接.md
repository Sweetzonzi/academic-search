---
doc-id: RUN-M5
kind: runbook
status: draft
title: "M5：Zotero 桥接（升级、授权、写读删）"
---

# M5：Zotero 桥接（升级、授权、写读删）

> 操作手册。前置：Zotero 未运行、库从未同步。初版（M0–M4）不接 Zotero，本手册在初版稳定后执行（[ADR-0032](../决策/ADR-0032.md)）。

**前置**：Zotero 当前未运行（已确认），库从未同步。

## 1. 备份（必做，先于一切）

```powershell
# 1) 关闭 Zotero（本机当前未运行）
# 2) 复制整个数据目录（库仅 2.22 MB，附件 51 MB）
Copy-Item -Recurse -Force "D:\ZoteroLib" "D:\ZoteroLib-backup-20261014"
# 3) 校验副本完整性（只读打开副本，不碰原库）
python -c "import sqlite3;c=sqlite3.connect(r'D:\ZoteroLib-backup-20261014\zotero.sqlite');print(c.execute('pragma integrity_check').fetchone())"
# 4) 记录升级前基线（条目数 / 附件数 / 全文索引数 / databaseSchema）
```
**记录基线**：顶层条目 5、附件 6、`fulltextItems` 6、`databaseSchema=37`、`collections=1`、`tags=3`。

## 2. 升级 Zotero 到 10.0.6

- 覆盖安装后**逐项比对基线**：`databaseSchema`、条目数、附件数、`fulltextItems` 数、PDF 能否打开、标签与 collection 是否完好。
- 若任何一项不符 → **停止后续步骤**，用备份回退（升级是单向的，回退依靠备份而非降级安装）。

## 3. 授权本地写入

```powershell
# 1) 启动 Zotero，确认设置中已勾选「允许本机其他应用与 Zotero 通信」
# 2) 读取 Server ID（读请求无需认证）
curl.exe -i http://127.0.0.1:23119/api/
#    → 关注响应头 Zotero-Server-ID: <id>
# 3) 请求授权（必须带 Server-ID 头；Zotero 会弹窗，用户点 "Always Allow"）
curl.exe -X POST http://127.0.0.1:23119/api/local/authorize `
  -H "Content-Type: application/json" `
  -H "Zotero-Server-ID: <id>" `
  -d '{\"appName\": \"academic-search\"}'
#    → 返回 {"key":"<32字符>","remember":true}
```
注意：默认是**单次 key**（首个成功写请求即消耗），只有 "Always Allow" 才可复用；每分钟最多 5 次弹窗。

## 4. 创建专用 collection 并做写入/读取/删除测试

```powershell
# 1) 创建 collection "Agent Inbox"（POST /api/users/0/collections）
# 2) 写一条最小条目（仅元信息，不带附件）
#    POST /api/users/0/items  → 201 + {"successful":{"0":{"key":"<itemKey>"}}}
#    body 示例：[{"itemType":"journalArticle","title":"[test] academic-search spike",
#                 "DOI":"10.0000/test","tags":[{"tag":"agent-test"}],"collections":["<collectionKey>"]}]
# 3) 读回校验（GET /api/users/0/items/<itemKey>）→ 确认字段落库
# 4) 删除（DELETE /api/users/0/items/<itemKey>）→ 确认进入回收站、UI 中消失
```
**同时验证**：写请求带 `If-Unmodified-Since-Version` 的语义；删掉 key 后写请求应返回 `401`（用于确认授权确实生效）。

## 5. 回滚验证

- 按 `itemKey` 删除测试条目后，确认 `GET /api/users/0/items?q=academic-search spike` 无结果。
- 在 sidecar 侧记录 `run_id` 与该条目的对应关系，验证"按 run 回滚"的查询路径可跑通。

## 6. 连接器回退通道

```powershell
# Zotero 未升级时，用连接器通道导入一条 RIS/BibTeX（需 Zotero 运行且选中目标集合，回答 Q3）
curl.exe -X POST "http://127.0.0.1:23119/connector/import" `
  -H "Content-Type: text/plain" --data-binary "@sample.ris"
```

---
