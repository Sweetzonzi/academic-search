---
doc-id: "EX-2026-10-18-download"
kind: "experiment"
date: "2026-10-18"
title: "首次真实 `download`：版本判据、条件 GET 与三列迁移"
---

# 实验记录：首次真实 `download`

> 实验日期：2026-10-18（本机，Asia/Shanghai）。目的：对着真源跑 `download`，逐条核对 [PLAN §2](../04-里程碑与计划.md) 为 [ADR-0067](../决策/ADR-0067.md) 登记的三条判据（命令拆分、`record.source_version`、schema 4），并量一下版本判据在真实响应头上的效果。
> 一句话结论：**三条判据都满足**——首次取件落 `bytes/` 并与 `works/` 同 inode、同参数第二次零请求、`304` 复用成立、旧库平滑升到 4；过程中发现并修掉两处缺陷：**`source_version` 为空时「NULL 视为不等」会让每次取件都整份重下**，以及**重投影会把 `bytes/` 的只读去掉**（§4）。
> 范围：只测 `download`。`read` 未接线，不在本文；HTML 重渲染（版本号不变、内容变）没有样本，未测。

## 1. 实验设置

| 项 | 值 |
|---|---|
| 命令 | `python -c "from academic_search.cli import main; sys.exit(main())" download …`（`PYTHONPATH=src`，工作目录 = 仓库根） |
| 解释器 | `D:\anaconda3\python.exe`（3.12.7，SQLite 3.45.3） |
| 样本 | `work_key = hxfa4uro3pmplq5bjdsq`，arXiv `2610.20609`（单成员 work，record 即首领） |
| 库的前置状态 | `user_version = 3`、`record` 3 897 行、`artifact` 0 行、`bytes/` 与 `works/` 都不存在 |
| 测试 | `pytest` 261 通过（新增 `tests/test_download.py` 16 例与 2 个迁移/登记用例） |

## 2. 结果

### 2.1 首次取件（`--refresh auto`，版本为空）

| 观测 | 结果 |
|---|---|
| 请求 | 1 次，`/e-print/2610.20609`（record 没有版本，不钉版本号） |
| 字节 | 6 986 013 B，`sha256 = a792f7f7…c81d1feb` |
| 落盘 | `bytes/a7/a792f7f7…` 与 `works/hxfa4uro3pmplq5bjdsq/source.tar.gz` **同 inode**（硬链接，删除 `works/` 不释放空间） |
| `artifact` 行 | `kind = source_tar_gz`、`source_version = NULL`、`etag = "sha256:425eb318…"`、`last_modified = Thu, 15 Oct 2026 02:06:02 GMT` |
| 迁移 | 打开旧库即 `user_version` 3 → 4，3 897 行 `record` 与既有 `run` 账本均未受影响 |

**留档的验证器形态与 `ADR-0067` 的实测表一致**：新版 `/src` 给的是 `"sha256:…"` 形式的强 ETag，`/html` 仍是 `"CNTE47bxupcDEAE="` 那种短串。

### 2.2 版本为空时的代价（`--refresh auto`，第二次）

第二次仍是 **1 次请求、整份重下**：`record.source_version` 与 `artifact.source_version` 都是 `NULL`，按「NULL 视为不等」判不中缓存。这与 ADR 代价 3 写的「过渡期内可能多取一次」不符——**实际是每次都多取**（6.9 MB / 次），因为补版本的时机只挂在 `search` / `sync` / `lookup` 上。修法与复测见 §4。

### 2.3 补齐版本之后

| 命令 | 请求 | 结果 |
|---|---|---|
| `download --refresh auto`（补版本后） | 2（feed 1 + 钉版本的 e-print 1） | `status = fetched`、`source_version = v1`、字节与首次**同一个 sha256** |
| `download --refresh auto`（第三次） | **0** | `status = reused`、`network = false`、`cost.requests = 0`，`run` 账本不新增行 |
| `download --refresh true` | 1 | 条件 GET 带 `If-None-Match` + `If-Modified-Since`，**arXiv 回 304**，`status = reused` |
| `download --source all` | 2（latex 命中缓存不请求） | `source_html` 574 953 B、`source_pdf` 7 762 871 B，两者都钉 v1；三条投影齐全 |
| `download --refresh false --source all` | 0 | 三条全部 `reused`、`network = false` |
| 删掉 `works/hxfa4uro3pmplq5bjdsq/` 后重跑 `--refresh false --source all` | 0 | 三条都是 `reused`，投影按 `bytes/` **重建**且仍是硬链接——`works/` 确实可重建 |
| `verify --sample 0` | 0 | 全部字节实体重算一致，`mismatches = []` |

**`run` 账本**（请求数 → 语义）：219 = 1（首次）、220 = 2（补版本 + 取件）、221 = 1（304，`counts.artifacts` 为空）、222 = 2（html + pdf）。**离线那一次不写行**：没有请求就没有 run。

### 2.4 落盘清单

```
bytes/a7/a792f7f7…c81d1feb        6,986,013 B   ← LaTeX 源码包
bytes/0d/0d039e7d…aadec1fe06      7,762,871 B   ← PDF
bytes/e4/e4fa3307…dd454aa8          574,953 B   ← arXiv HTML
works/hxfa4uro3pmplq5bjdsq/source.tar.gz   ← 三条都与 bytes/ 同 inode
works/hxfa4uro3pmplq5bjdsq/source.html
works/hxfa4uro3pmplq5bjdsq/source.pdf
```

## 3. 判据对照

| PLAN §2 判据 | 结果 |
|---|---|
| 首次：`data.artifacts[]` 含 `source_tar_gz`、`network=true`、`bytes/` 出现实体且与 `works/<work_key>/source.tar.gz` 同 inode | ✅ §2.1 |
| 同参数第二次：`network=false`、`artifacts[].status="reused"`、账本没有新请求 | ✅ §2.3（补版本之后；见 §4 的缺口） |
| arXiv 记录 `source_version` 形如 `v3`、与 feed 的 `<id>` 版本一致；`search` / `sync` / `lookup` 刷新后都更新；非 arXiv 记录为 NULL | ✅ 真源为 `v1`；`tests/test_cli.py::test_lookup_writes_the_arxiv_version` 与 `tests/test_sources.py` 的 fixture 用例覆盖两个方向 |
| 旧库（`user_version=3`）打开后升到 4，`artifact` 三列存在；登记 `source_html` / `source_pdf` 不报错 | ✅ §2.1 的真库迁移 + `tests/test_store.py` 的构造库用例 |

## 4. 发现并修掉的两处缺陷

### 4.1 版本为空时的重下

**现象**：`record.source_version` 与 `artifact.source_version` 都为空时，「NULL 视为不等」使 `--refresh auto` 每次都判不中缓存，6.9 MB 的源码包每次整份重下；而补版本的时机只挂在元数据刷新上，不以 `search` / `sync` / `lookup` 过一遍就永远自愈不了。

**修法**（决策层面的表述已写回 [ADR-0067](../决策/ADR-0067.md) 决策 3 与代价 3）：`download` 在版本为空且允许联网时**先读一次 feed** 拿当前 `vN`，读到就落回 `record.source_version`，随后钉住 `vN` 取件，于是 `artifact.source_version` 也是事实而不是猜测。feed 读不到（404 / 网络错）才退回取最新版、版本留 `NULL`——那一次缓存不成立，下次仍取件。

**复测**：修后第一次 = 2 请求（feed + 钉版本取件），第二次起 = 0 请求（§2.3）。代价是一次 30 KB 量级的 feed 请求，只在「这条 record 还没有版本」时发生一次。

### 4.2 重投影把 `bytes/` 的只读去掉了

**现象**：删掉 `works/` 再重跑之后，三份字节实体的 Windows 属性从 `ReadOnly, Archive` 变成 `Archive`——`hardlink_or_copy` 在目标已存在时先 `make_writable(dest)` 再删，而 `dest` 是实体本身的一条硬链接，`chmod` 作用在共享的 inode 上。

**为什么这次才暴露**：首个投影是「目标不存在 → 直接 `os.link`」，不碰权限；`download` 命中缓存时**每次都会重投影**（把 `works/` 补回来是它的一部分职责），于是这条路径从「只在重建时走一次」变成常态。

**修法**：`hardlink_or_copy` 在目标已存在时先比 `os.path.samefile(src, dest)`，同一条链就**什么都不做**返回 True；只有指向别处才删掉重建。语义上更准确（「让 dest 成为 src 的链接」本来就是幂等的），且不再改写 `bytes/` 的只读。

**复测**：恢复只读后再跑一次 `download --source all --refresh auto`（三条全部 `reused`），三个实体仍为 `ReadOnly, Archive`；`tests/test_paths.py` 新增用例守这条不变量。

## 5. 局限

1. **HTML 重渲染未测**：arXiv 重新生成 LaTeXML 输出时版本号不变，`auto` 判不中，只有 `--refresh true` 的条件 GET 能发现。本实验没有这类样本，只证明了「内容未变 → 304」这一侧。
2. **`--source all` 是每篇最多 3 个请求**，受 arXiv 3 秒间隔约束（本次请求之间未见限流）；批量使用的节奏控制不在本文范围。
3. **PDF 不解析**：本次只证明能取到、能存取、能投影，PDF→Markdown 仍是顺延项。
4. **`read` 未接线**：`paper_md` 的物化、`stale` 标注与投影重建（删 `works/` 后重跑）都还没实现，属 PLAN §2 剩下的那一行。
