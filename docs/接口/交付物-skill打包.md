---
doc-id: "IFACE-SKILL"
kind: "normative"
status: "living"
title: "交付物：skill 打包契约"
---


# 交付物：skill 打包契约

```
academic-search/            # skill bundle（**薄**：只装给 agent 读的文本与启动器）
├─ SKILL.md                 # frontmatter: name + description(触发条件)；正文=决策树与失败分支（命令细节在 `references/`）
├─ references/              # 按需加载：setup.md / cli-reference.md / openalex-fields.md / arxiv-syntax.md（M5 追加 zotero-write.md）
├─ scripts/run.cmd|run.sh   # launcher：仓库根 = --root → ACADEMIC_SEARCH_ROOT → bundle 的 root.txt → 解析链接后的脚本往上三级
└─ root.txt                 # 复制模式安装时写入的一行仓库路径；链接模式无需生成
```

**装载位置与安装选项**统一见[初始化参考 §1](../../skills/academic-search/references/setup.md#1-安装与路径绑定)：安装者的 agent 根据宿主实际扫描位置指定 `--dest`，安装器默认创建链接。宿主的扫描与刷新机制由安装者核实。

**每个宿主有一个安装入口，技能、代码与数据共享仓库**：默认链接仓库薄 bundle，决定与理由见 [ADR-0075](../决策/ADR-0075.md)；CLI 实现、`config/`、`data/`、`bytes/`、`works/` 也保留在仓库里，由 launcher 定位。薄 bundle 与代码、数据分离的原则见 [ADR-0055](../决策/ADR-0055.md)，不支持链接时仍可显式复制薄 bundle。**数据在仓库内 ⇒ 重新 clone 会丢 `config/`**，安装说明里必须提醒先备份。

**launcher 的约定**：绑定解析、解释器选择与 agent 初始化步骤统一见[初始化参考](../../skills/academic-search/references/setup.md)。显式 `--root` 在仓库校验之前生效，坏的环境变量或 `root.txt` 不阻断正确的显式路径；候选解释器须满足版本和核心模块要求。失败非零退出并给出修法，不静默选错库。launcher 专用的 `--diagnose` 输出本地诊断文本，不打开数据库、不联网，不属于 CLI JSON 信封；普通子命令的接口不变。`root.txt` 是**复制模式安装时写入的本机仓库路径**，各机不同，因此不进版本库（`.gitignore` 里是 `skills/*/root.txt`）。**`.cmd` 例外为 CRLF**（`.gitattributes` 里的 `*.cmd text eol=crlf`）：实测 cmd.exe 对 LF-only 的批处理会错切行——`rem` 注释被切开、`if (...)` 块整体失效——而仓库默认 `eol=lf`。

**落地现状**：`SKILL.md`、四份 `references/` 与两个 launcher 已在 `skills/academic-search/`。`tools/install_skill.py` 默认链接安装，迁移会备份已有副本；安装器回归见 `tests/test_install_skill.py`。初始化的进程级回归见 `tests/test_launchers.py`，覆盖链接与复制安装从无关 cwd 调用、显式根覆盖坏绑定、解释器回退、离线初始化、凭据缺失与只读库。DSH 的复制安装曾于 2026-10-19 实测：以 `filesystem` 提供方解析到 `~/.dsh/skills/`，`run.cmd version` 与 `profile check` 退出码 0，`search --refresh false` 回本地条目；该实测不证明 DSH 已识别链接安装。`sync` 已接线（首次真跑见 [EX-2026-10-16-sync](../实验/EX-2026-10-16-首次真sync.md)），命令表的接线状态必须与实现同步。

**初版交付物**（M3 一并产出，[PLAN §1](../04-里程碑与计划.md)）：`SKILL.md`、图示的四份参考和两个 launcher；`zotero-write.md` 与 `export` 相关说明随 M5 追加。

**初始化验收**：[EX-2026-10-22-initialization](../实验/EX-2026-10-22-技能初始化.md) 记录启动器回归、独立空库与真实 OpenAlex 验证；该实验区分沙箱拒绝联网与获准后的来源成功。

**SKILL.md 应写死的硬规则**：① 检索默认 `--refresh auto`（按检索式新鲜度自行判断，会标注是否跳过）；要强制最新用 `--refresh true`，离线或省钱用 `--refresh false`（[ADR-0043](../决策/ADR-0043.md)）；② 结论必须带出处，无出处不输出；③ 入库只能进专用 collection 且先 `--dry-run`（**M5 起适用**：初版不接 Zotero，[ADR-0032](../决策/ADR-0032.md)）；④ 余额不足或额度耗尽时降级而非重试。
