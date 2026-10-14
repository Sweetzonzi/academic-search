---
doc-id: REF-DSH
kind: reference
measured: 2026-10-14
title: DSH（agent 宿主）能力事实
---

# DSH（agent 宿主）能力事实

> 宿主版本变化后需复核。

| 项 | 事实 |
|---|---|
| skill 扫描根（rank 顺序） | `<projectRoot>/.dsh/skills`(100) → `<projectRoot>/.agents/skills`(200) → `customSkillDirs`(300) → `~/.dsh/skills`(400) → `~/.agents/skills`(500) → bundled(600)；`projectRoot` = 最近的含 `.git` 的祖先目录（本工作区 `D:\Files\Project_Paper` 与 `D:\Files` 均无 `.git`，故取 cwd） |
| skill 格式 | `<name>/SKILL.md` 目录 bundle 或 `<name>.md` 平铺；frontmatter 必填 `name`（kebab-case）与 `description`，可选 `whenToUse`/`metadata`/`user-invocable`/`disable-model-invocation`；发现深度仅一层；新增/改名**热更新** |
| 上下文成本对比 | 参考 `zotero-mcp` 的度量：MCP 默认 38 个工具 schema ≈ **13,448 tokens/请求**；skill frontmatter ≈ **98**、正文 ≈ **1,368**、reference ≈ **4,389** |
| schedule 语义 | 提醒持久化但投递需 live 根 agent；一次性提醒与固定间隔（最小 5 分钟）；错过多个间隔**只投递最后一次**；无邮件/推送等外部通道 |

---

