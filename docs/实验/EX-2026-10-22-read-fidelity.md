---
doc-id: "EX-2026-10-22-read-fidelity"
kind: "experiment"
status: "complete"
date: "2026-10-22"
title: "OverSeer 的摘要、引用与图件阅读回归"
---

# OverSeer 的摘要、引用与图件阅读回归

## 1. 样本与复现

样本为 arXiv `2610.28554`，work_key 为 `c7tdvk6jfoincq33kdgz`。只读本地源码包，SHA256 为 `c00599d6c623e5b2e38d36323f435205c04bd84ac7d8af1afa6d9db97dcb5e41`；不联网，不修改日常 `data/`、`bytes/` 或 `works/`。本实验没有核验远端当前版本；独立库中的 `v1` 是实验登记值，不作为远端版本证据。

复现脚本为 [experiments/m2/28_overseer_read_regression.py](../../experiments/m2/28_overseer_read_regression.py)，`--out` 必须是新目录。它在独立库登记源包后调用 `read --refresh false`，重复调用检查缓存，并逐图比较投影与内容寻址字节。

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'D:\anaconda3\python.exe' experiments\m2\28_overseer_read_regression.py --source works\2026-Li-OverSeer-Multi-Agent-World-Models-Beyond-What-c7tdvk6jfoincq33kdgz\source.tar.gz --out experiments\m2\out\overseer-regression
```

**工具实测**：Python 3.12.7、pandoc 3.9、`pdftocairo@24.03.0`，转换器内部版本 `read@2`。受限沙箱中启动 pandoc 返回 `3221225794`，真实工具验收经宿主授权在沙箱外完成。结果留档：`experiments/m2/out/overseer-rd-20261022-a1/result.json`。

## 2. 缺损机制与实现

| 缺损 | 源码与实现证据 | 处理 |
|---|---|---|
| 摘要整段缺失 | 根文件在 `document` 环境前使用 `\abstract{\input{sec_iclr/abstract_iclr}}`，pandoc 没有保留该自定义宏的内容 | 展开源码后提取平衡宏参数，移入正文，作为无编号的 Abstract 小节 |
| 表头含 `(lr)` 与区间数字 | 源码中的 `\cmidrule(lr){2-11}` 被当作正文 | 转换前删除该排版命令，包含宽度、裁剪及区间参数 |
| 附录显示普通节号、表格引用暴露标签 | 引用表只读根文件，而章节及表格在 `\input` 文件中；编号未处理 `\appendix` | 按源码出现顺序展开文件、忽略注释与逐字环境、跳过星号章节、附录按字母编号；源码章节与表格编号优先于输出的顺序猜测 |
| 5 处文献未解析 | 源码声明 `references,vedaldi_general,vedaldi_specific`，实现只选第一份；3 个缺失 key 均存在于另外两份 | 按源码声明解析全部文献库，逐份传给 citeproc，不补录也不隐藏失败引用 |
| `{.nocase}` 泄漏 | 属性清理表达式在点号前要求单词边界，未匹配独立类属性 | 修正匹配并纳入 `.nocase`、`.uri`，保留姓名文本 |

源码预处理不执行 TeX，只访问解包目录内的静态文件；循环、越界、缺文件或展开超限明确使该路线失败。转换只写临时副本。转换器内部版本参与缓存键，修复部署后可由已有原件离线重转；无需强制联网刷新。

## 3. 实测结果

基线正文 SHA256 为 `01dfafbc7b948db37c54c8198451ee038c89914d5ed00e03b82a49fcf3daa2fa`。读文件核对基线后，在隔离目录转换，得到下表；这两个哈希对应不同目录的独立产物，日常库中的基线仍保留。

| 指标 | 基线产物 | 隔离回归产物 |
|---|---|---|
| 正文字符数 | 74,714 | 75,937 |
| Abstract 标题 | 无 | 有，后续 300 字内含 `Despite the success of video world models` |
| `(lr)` 残留 | 16 | 0 |
| `{.nocase}` 残留 | 30 | 0 |
| 未解析文献标记 | 5 | 0 |
| 原始交叉引用标签链接 | 6 | 0 |
| 附录引用 | `sec. 8`、`sec. 13` | `Appendix B`、`Appendix G` |
| Minecraft 主表引用 | 内部标签 | `Table 1` |
| 图件 | 11 | 11 |
| HTML 表格相关行 | 191 | 188 |

回归正文 SHA256 为 `5c82f98ddef1a96bfa0d403e6585db09f6ebcbf90ebe89d9210ae9246ed165a9`。转换时间写入元信息，独立重转不要求哈希相同；缓存命中要求同一哈希。

首次及重复 `read` 均为零请求、`network=false`；重复正文哈希相同。正文投影逐字节相同，11 张图的相对路径均可用，逐图与 `bytes/` 内容一致，全量字节校验不匹配数为 0，源包哈希未变。

**人工复核**：读取恢复的摘要、两处附录引用和主表引用；表格保留 Solaris Benchmark / MAC-Bench 的合并表头及 VLM / FID 子列。复杂表格保留 HTML 是结构表达方式，不以 HTML 行数归零作为验收目标。pandoc 仍返回媒体复制权限警告，预置媒体使本样本的 11 张图均成功保留；本实验不把警告隐藏或当作缺图。

## 4. 覆盖与边界

`tests/test_latex.py` 覆盖多文件与嵌套参数、注释与逐字环境、循环和越界、源码声明的多文献库、星号与附录编号、部分未解析交叉引用的可见降级，以及真实 pandoc 的完整组合回归。`tests/test_read.py` 额外检查内部转换器版本变化触发本地重转、无网络请求。

全量离线回归实跑：**481 passed**（22.53 秒，无跳过）。真实论文验收由独立脚本完成，不将桩客户端当作真实工具证据。

人和 agent 的默认阅读入口按 [IFACE-PAYLOAD §5.2](../接口/输出载荷.md) 使用 `doc.work_path`，投影不可用时才回退内容坐标与图件映射；操作流程见[论文精读](../../skills/academic-search/SKILL.md#4-论文精读)。

**需人读**：文档语义漂移、整体自包含性、公式与复杂表格是否忠于原文、论文结论是否有充分依据，均不能由上述脚本证明。本次复核覆盖列出的摘要、引用和表头，不宣称所有宏、所有表格或其它论文已完整保真。

## 5. 附录子级锚点的完整大纲验收

`read@2` 的引用侧已使用源码附录编号，但锚点仅在标题标签命中引用表时替换编号。没有显式标签的标题仍走独立数字计数，出现 `7.0.0.1 id:data.`、`12.1.0.1` 和 D 后的 `11`。`Data.` 在源码中是没有标签的 `\paragraph`，仅去掉自动 id 的尾点无法补齐映射；该段落也没有论文编号，不能改成虚构的 A.1。

`read@3` 由同一次源码扫描生成完整标题大纲与引用表：所有标题均记录层级、是否编号、所属编号祖先及可选标签。输出标题按标签或顺序中的标题文字匹配；无标签的附录节参与字母编号，无编号段落只保留所属章节与标题。源码声明的 `secnumdepth` 控制编号深度；标准类缺省值按 article 的 3、book/report 的 2 处理，自定义类内部编号宏未全面解析。无法对齐的输出标题保留标题位置并警告，不制造编号。

同一脚本增加全体附录锚点检查，再次在新隔离目录运行，结果留档 `experiments/m2/out/overseer-anchors-20261022-a1/result.json`，正文 SHA256 为 `a6684fccc45f83c728eb73497d63bf23baae2f320ac51291775c64e25b2ed3a9`，字符数为 76,599。

| 定位对象 | 实测锚点 |
|---|---|
| Dataset Curation Pipelines | `A id:app:data` |
| Data. 段落 | `A · Data. id:data.` |
| Scene-Independent Pose Prior. 段落 | `B · Scene-Independent Pose Prior. id:scene-independent-pose-prior.` |
| 无显式标签的 Limitations and Future Work | `E` |
| The judge 小节 | `F.1 id:app:prompts_judge` |
| Revisiting benchmark. 段落 | `F.1 · Revisiting benchmark. id:revisiting-benchmark.` |
| More Visualizations | `G id:appendix:more_vis` |

**实测**：附录范围内数字开头的锚点为 0；A–G 连续，无标题对齐警告。引用中的 Appendix B / G 与 Table 1 仍正确，11 张图、正文投影与缓存检查均通过，两次读取均零请求，原始源包哈希未变。人工逐项比对本节表中的锚点与源码标题层级。

全量离线回归为 **486 passed**（29.95 秒，无跳过）。新增组合覆盖无标签的节与子节、跳级段落、星号标题、宏标题、显式编号深度，以及 citeproc 给文末标题附加 `.unnumbered` 的情况；源码可确认的编号优先于该输出属性。其它自定义类及宏的完整编号保真仍需人读扩样。
