---
doc-id: "IFACE-SKILL"
kind: "normative"
status: "living"
title: "交付物：skill 打包契约"
---


# 交付物：skill 打包契约

```
academic-search/            # skill bundle
├─ SKILL.md                 # frontmatter: name + description(触发条件)；正文=决策树与命令示例
├─ references/              # 按需加载：openalex-fields.md / arxiv-syntax.md / zotero-write.md / dedupe-rules.md
└─ scripts/                 # 指向 CLI（确定性）
```

**装载位置**（[REF-DSH](../调研/DSH能力.md)）：`<projectRoot>/.dsh/skills`(100) → … → `~/.dsh/skills`(400) → bundled(600)；`projectRoot` = 最近的含 `.git` 的祖先。**建议同时装到 `~/.dsh/skills/`**（定时任务可能在别的 cwd 的会话中被唤醒）。

**SKILL.md 应写死的硬规则**：① 检索默认 `--refresh auto`（按检索式新鲜度自行判断，会标注是否跳过）；要强制最新用 `--refresh true`，离线或省钱用 `--refresh false`（[ADR-0043](../决策/ADR-0043.md)）；② 结论必须带出处，无出处不输出；③ 入库只能进专用 collection 且先 `--dry-run`；④ 余额不足或额度耗尽时降级而非重试。