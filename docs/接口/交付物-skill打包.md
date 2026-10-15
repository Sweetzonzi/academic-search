---
doc-id: "IFACE-SKILL"
kind: "normative"
status: "living"
title: "交付物：skill 打包契约"
---


# 交付物：skill 打包契约

```
academic-search/            # skill bundle（**薄**：只装给 agent 读的文本与一行入口）
├─ SKILL.md                 # frontmatter: name + description(触发条件)；正文=决策树与命令示例
├─ references/              # 按需加载：openalex-fields.md / arxiv-syntax.md（M5 追加 zotero-write.md）
├─ scripts/run.cmd|run.sh   # launcher：仓库根 = --root → ACADEMIC_SEARCH_ROOT → bundle 的 root.txt → 脚本往上三级
└─ root.txt                 # 安装时写入的一行仓库路径（用环境变量时不需要）
```

**装载位置**（[REF-DSH](../调研/DSH能力.md)）：`<projectRoot>/.dsh/skills`(100) → … → `~/.dsh/skills`(400) → bundled(600)；`projectRoot` = 最近的含 `.git` 的祖先。**建议同时装到 `~/.dsh/skills/`**（定时任务可能在别的 cwd 的会话中被唤醒）。

**每个宿主装一份薄的，代码与数据只有一份**：不同宿主的 skill 扫描根不同，SKILL.md 与 references 必须各装一份；但 CLI 实现、`config/`、`data/`、`bytes/`、`works/` 都在仓库里，由 launcher 指过去（[ADR-0055](../决策/ADR-0055.md)）。**数据在仓库内 ⇒ 重新 clone 会丢 `config/`**，安装说明里必须提醒先备份。

**launcher 的约定**：仓库根按 `--root` → `ACADEMIC_SEARCH_ROOT` → `bundle/root.txt` → **脚本往上三级**（bundle 装在仓库内时正好落在仓库根）解析。解析结果必须含 `src/academic_search/`，否则非零退出并提示怎么修——**允许失败，不允许悄悄用错根**。解释器取 `ACADEMIC_SEARCH_PYTHON`，未设则用 PATH 上的 `python`（POSIX 上优先 `python3`），并先跑一次 `-c "import sys"` 探测：Windows 上裸 `python` 常命中应用商店存根（退出码 1 且无输出），探测就是为了把这个静默失败变成一句能照做的提示。`root.txt` 是**安装时写入的本机仓库路径**，各机不同，因此不进版本库（`.gitignore` 里是 `skills/*/root.txt`）。**`.cmd` 例外为 CRLF**（`.gitattributes` 里的 `*.cmd text eol=crlf`）：实测 cmd.exe 对 LF-only 的批处理会错切行——`rem` 注释被切开、`if (...)` 块整体失效——而仓库默认 `eol=lf`。

**落地现状**：`SKILL.md`、两份 `references/` 与两个 launcher 已在 `skills/academic-search/`，并在仓库根实测可用（`version`、真 arXiv 检索取回 5 条、紧随其后的 `--refresh auto` 跳过联网、仓库根指错时退出码 1）。`sync` 尚未接线，SKILL.md 的命令表把它标成「尚未接线」——**契约先写、能力后到**，免得 agent 去调一条还不存在的命令。

**初版交付物**（M3 一并产出，[PLAN §1](../04-里程碑与计划.md)）：`SKILL.md` + `references/openalex-fields.md` + `references/arxiv-syntax.md`；`zotero-write.md` 与 `export` 相关说明随 M5 追加。

**SKILL.md 应写死的硬规则**：① 检索默认 `--refresh auto`（按检索式新鲜度自行判断，会标注是否跳过）；要强制最新用 `--refresh true`，离线或省钱用 `--refresh false`（[ADR-0043](../决策/ADR-0043.md)）；② 结论必须带出处，无出处不输出；③ 入库只能进专用 collection 且先 `--dry-run`（**M5 起适用**：初版不接 Zotero，[ADR-0032](../决策/ADR-0032.md)）；④ 余额不足或额度耗尽时降级而非重试。