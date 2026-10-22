# 初始化与排障

首次使用、换机器或执行环境、安装副本更新后，以及启动或环境相关失败时读本文件。目标是让**当前执行 CLI 的子进程**拿到正确的仓库、解释器、凭据与权限。一次性检索无需画像。

## 1. 安装与路径绑定

保留一份完整代码仓库，把 `skills/academic-search/` 复制到宿主的 skill 目录。安装的是薄 bundle，代码和数据仍在仓库；更新技能时重新复制 `SKILL.md`、`references/` 和 `scripts/`，保留本机绑定。仓库内可直接跑 `tools/install_skill.py` 完成安装或更新：它只同步这三项，保留 `root.txt`，把被替换的副本备份到 `skills.backup/`，并支持 `--check` 只比差异、`--dest` 指定安装目录。

仓库根按 `--root` → `ACADEMIC_SEARCH_ROOT` → bundle 的 `root.txt` 第一行 → bundle 上两级目录解析。`--root <路径>` 或 `--root=<路径>` 必须在子命令前。仓库根须含 `src/academic_search/__init__.py`；它同时决定 `config/`、`data/`、`bytes/` 和 `works/` 的位置，**不是任意可写数据目录**。路径使用实际执行环境能访问的绝对路径；Windows 的盘符路径不能直接用于远端 Linux 容器。

仓库内调用可用默认位置。安装到别处时，在 bundle 的 `root.txt` 写一行仓库绝对路径（无引号），或在调用进程设置 `ACADEMIC_SEARCH_ROOT`。该文件只放路径，不放 key。复制到另一台机器后重新绑定。

存储采用 SQLite WAL，离线检索也需要写 `data/`。目录受限时优先为现有仓库取得宿主允许的写权限；迁移到可写位置时保留 `config/`、`data/`、`bytes/` 和 `works/`。新 clone 或空目录不会带上现有配置与缓存，不要静默切库，也不要清空数据排障。

## 2. Python 与调用环境

核心检索只用 Python 标准库，需要 Python `>=3.11` 及 `sqlite3`、`tomllib`；无需先 `pip install`，launcher 会设置 `PYTHONPATH`。检索与原件下载无需 pandoc。

优先用宿主已提供的解释器路径；否则用 PowerShell 的 `Get-Command python,py -All` 或 POSIX 的 `command -v python3` 找候选，用实际路径执行 `-c "import sys,sqlite3,tomllib; print(sys.executable); print(sys.version)"` 验证。WindowsApps 的 `python.exe` 可能只是不能运行的存根。将可用解释器的**可执行文件路径**设为 `ACADEMIC_SEARCH_PYTHON`，不要在值里拼 `-3.12` 等参数。

launcher 验证版本及模块后选用候选：Windows 为 `ACADEMIC_SEARCH_PYTHON` → PATH 上逐个 `python` → `py`，POSIX 为该变量 → `python3` → `python`。候选失败会继续尝试，最终失败时 stderr 给出修复入口。

环境变量属于进程。工具的多次 shell 调用未必共享会话；**把设置和 CLI 调用放在同一次工具执行中**，或使用宿主正式支持的环境注入。不要假定交互终端、PowerShell profile、父应用和沙箱子进程拥有相同的 PATH 或 key。宿主允许时，在其本机配置中持久化非秘密路径即可，无须让每个 agent 重找。

以下示例的仓库、bundle 和解释器路径均需替换。`D:\anaconda3\python.exe` 只是一个本机示例，不是通用安装位置。

```powershell
# 同一次执行；安装副本的入口用其 scripts/run.cmd 绝对路径。
$env:PYTHONIOENCODING = 'utf-8'
$env:ACADEMIC_SEARCH_PYTHON = 'D:\anaconda3\python.exe'
$env:ACADEMIC_SEARCH_ROOT = 'D:\path\to\academic-search'
$launcher = 'C:\path\to\academic-search-skill\scripts\run.cmd'
& $launcher --diagnose
if ($LASTEXITCODE -ne 0) { throw '先修复启动器诊断失败项' }
& $launcher search --refresh false --limit 1
```

```sh
# 同一次执行；用 sh 调用，无需预先设置执行位。
export PYTHONIOENCODING=utf-8
export ACADEMIC_SEARCH_PYTHON=/absolute/path/to/python3
export ACADEMIC_SEARCH_ROOT=/absolute/path/to/academic-search
launcher=/absolute/path/to/academic-search-skill/scripts/run.sh
sh "$launcher" --diagnose && sh "$launcher" search --refresh false --limit 1
```

## 3. OpenAlex 凭据

CLI 只读当前进程的 `OPENALEX_API_KEY`；**不自动加载 `.env`，不读取 Windows 用户级或系统级变量，也不代为注册 key**。本地检索与 arXiv 不需要 key；OpenAlex 的鉴权和额度事实见 [OpenAlex 参考 §2](openalex-fields.md#2-计费与鉴权决定现在能不能用)。

先用 `--diagnose` 看 `openalex_key=present|missing`，不打印值。`present` 只证明进程变量非空，不证明服务接受它，也不证明余额可用。优先使用宿主允许的 secret 注入。Windows 用户级变量已配置但当前进程缺失时，只有在宿主允许读取该凭据来源时，才可在**同一次执行**中条件补充：

```powershell
if (-not $env:OPENALEX_API_KEY) {
    $candidateKey = [Environment]::GetEnvironmentVariable('OPENALEX_API_KEY', 'User')
    if ($candidateKey) { $env:OPENALEX_API_KEY = $candidateKey }
    Remove-Variable candidateKey
}
# 接着设置第 2 节的路径并调用 launcher；不要输出变量值。
```

读取不到时，只能说“当前执行身份下凭据不可见”。不要覆盖已注入的进程变量，也不要跨账户查找或用其他凭据来源绕过宿主过滤。远端或容器需由其宿主单独注入，不能读取本机用户级变量来解决。

用户尚未配置时，引导其按 [OpenAlex 官方鉴权说明](https://help.openalex.org/api/authentication/) 获取并通过宿主配置 key；凭据不写入技能、`root.txt`、命令参数或报告。官方说明允许基础匿名请求，但本项目实测匿名额度按出口 IP 共享，不能当成已配置 key 的等价替代。选择 arXiv 继续时需说明覆盖变化，尤其是被引、FWCI 或载体筛选无法由该源替代。

## 4. 分层自检

| 检查层 | 操作 | 成功的证明范围 |
|---|---|---|
| 启动器 | `run.cmd --diagnose` / `sh run.sh --diagnose`，可前置 `--root <仓库>` | 路径指向代码仓库、解释器满足版本与模块要求；不打开数据库、不联网 |
| CLI | `version` | 代码能导入和输出信封；不证明存储可用 |
| 本地库 | `search --refresh false --limit 1` | `ok=true`、退出码 0：数据库能打开和初始化；空库返回空列表也算成功；不证明网络可用 |
| OpenAlex | 在已获准联网且有 key 时，执行下方强制刷新命令 | 检查实际请求、失败与降级信息，判断该来源是否完成；不证明另一来源可用 |

`--diagnose` 是 launcher 专用入口，单独调用，不与子命令混用。stdout 是 `name=value` 文本，含解析后的 `root` / `data` 路径、`root_valid`、实际 `python`、Python / SQLite 版本、key 存在性；`database` 与 `network` 固定为 `not_checked`。`stage=launcher_ready` 退出 0，`root_error` 或 `python_error` 退出 1 并在 stderr 给修法；根与解释器同时有问题时先报根。它的输出不是 CLI JSON 信封。

最小 OpenAlex 联网验证（沿用已设置的解释器、仓库和凭据）：

```text
<launcher> search --source openalex --all-of "world model" --refresh true --limit 1
```

该操作会写本地库并使用一次主题检索的额度；`--limit 1` 只限制展示，不减少远端取数。成功应有 `ok=true`、`data.network=true`、实际请求数大于 0，并核对 `counts.complete`、`counts.failures`、`degraded` 和 `warnings`，不能只看退出码或条目数。空结果仍可能是一次成功请求；`--refresh auto` 可能命中新鲜度而不联网，所以不用于此项验收。需要验证 arXiv 时，用同一命令将 `--source` 换成 `arxiv`，无需 key。

## 5. 按失败阶段恢复

| 现象 | 判断与下一步 |
|---|---|
| 无 JSON、仓库路径错误 | 用 `--diagnose` 检查解析根；更新本机绑定或显式给 `--root`，保留原库与配置 |
| 无合格 Python | 检查实际路径、版本、模块和执行权限；修复候选后重跑，不反复调用坏存根 |
| `data_not_writable` | 对照诊断中的 `data` 与错误中的绝对路径，检查目录和库文件权限；按 §1 处理原仓库，重复同一命令无用 |
| key 缺失 | 按 §3 处理注入；本地或 arXiv 可继续，需说明实际范围 |
| `WinError 10013`、明确的策略拒绝 | 可能来自沙箱、防火墙或其他网络策略；结合宿主权限确认，通过宿主授权机制允许目标访问后再试，换 key 不能解决 |
| DNS、TLS、代理或超时错误 | 检查当前子进程的网络与代理环境；确定性配置问题先修复，暂时故障最多重试一次 |
| HTTP 401 / 403 | 请求已到服务；查看授权错误原因，检查 key 有效性、激活和访问条件，不按断网处理或持续重试 |
| HTTP 402 / 429、余额降级 | 检查额度与速率，按返回信息停止、等待或使用已有结果；不绕过额度限制 |
| `busy` | 另一 run 占写锁，稍后最多重试一次；不删除数据库或锁相关文件 |

网页搜索工具可用不证明 CLI 子进程可联网；两者可能使用不同执行环境。不能修改宿主全局安全设置来完成自检。权限不允许且无法获准时，报告已通过的层、失败层、错误与仍可用的范围；未执行的层标为未验证。

环境机制依据：[PowerShell 环境变量官方说明](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_environment_variables?view=powershell-7.4)（进程继承与作用域）、[OpenAI 宿主配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)（子进程环境与命令网络权限，具体字段依宿主版本）。其他宿主使用其自身的注入与授权机制。
