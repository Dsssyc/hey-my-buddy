# 调研：四个 harness 的桌面版装在哪、自带的 CLI 在哪

日期：2026-10-22。调研人：Codex CLI Worker，按 Host 冻结的范围采集本机与 Windows 测试机的只读证据。

## 问题

为修改 [ADR-017](../decisions/017-local-installation-and-harness-discovery.md) 第 10 条量清事实：Claude Code、Codex、DSH、ZCode 的桌面版怎样被系统登记，包内 CLI 与独立 CLI 各在哪里，现在的发现实际列出什么。续查 [2026-10-11 的记录](2026-10-11-harness-discovery-across-systems.md) 已指出的桌面包发现缺口；本次没有改发现规则。

## 看了什么

实测来源是当天这台 macOS 27.0.1（26A434）的本机安装，以及经 `ssh Dsssyc-x86-PC` 访问的专用账户可见的 Windows（`OSVersion.VersionString` 为 `Microsoft Windows NT 10.0.26200.0`）。Mac 枚举了 `/Applications`、`~/Applications` 的应用包，读取 `Info.plist`，按观察到的 bundle id 调用 `mdfind`，检查四个应用包的文件名、类型、权限、符号链接和相关 `package.json` 元数据。Windows 检查 HKCU、HKLM 的 `Software\Microsoft\Windows\CurrentVersion\Uninstall` 及各自 `Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall`，当前账户的全部 `Get-AppxPackage` 结果、相关包的应用清单、用户与公共开始菜单的 `.lnk` 目标、`Get-StartApps`、用户与机器的注册表 PATH，以及找到的安装目录、npm 包元数据和 CLI 包装文件。没有查询其他账户的 AppX 包。

对 harness 只运行 `--version`，没有启动桌面界面、读取登录状态、进行模型调用、安装或改系统设置。Windows 脚本经 PowerShell `-EncodedCommand` 传入，没有向测试机写文件，因此没有产生待清理的远程工作目录。未上网查厂商材料。未读厂商应用的业务源码，也未解包 `app.asar`；读取了本任务要求的三个 Windows `.cmd` CLI 包装文件全文，下面照录。Linux 没有机器，整体为“未观察”。“未安装”仅对应本次安装登记、应用目录与命令位置的检查范围，不证明磁盘任意位置都没有副本；安装命令和历史均未观察。

本项目对应提交 `bfd61bb6`。读取本项目发现入口与 10-11 的调研记录，并以 `PYTHONPATH` 的等效源码路径导入方式，在 Mac 对四个 harness 各调用一次 `hey_my_buddy.buddy.harnesses.discovery.candidate_snapshot`，没有传 `manual_path` 或替换环境，没有执行健康检查；Windows 没有运行产品代码。每次快照含内部收集的 Node 候选。原始证据在本检出的忽略目录 `tmp/harness-desktop-entries/`：`capture.py` 与各探测脚本保存命令，`*.json` 保存参数与退出码，`*.stdout`、`*.stderr` 保存未经修改的输出字节；四份 `*-snapshot.json` 保存完整函数返回值。Windows 标准输出设置为 UTF-8，PowerShell 在标准错误上的 GBK/CLIXML 字节仍按原样保留。本文将两台机器的主目录分别写作 `~`。

## 看到的事实

Mac 应用包元数据与 Spotlight 实测如下。四个主要桌面版都安装在 `/Applications`；`~/Applications` 没有四个主要桌面版的另一份，但有下表最后一项 URL Handler。

| 对象 | 应用包位置 | `CFBundleIdentifier` | `CFBundleShortVersionString` | `mdfind` 按该 id 返回的位置 |
| --- | --- | --- | --- | --- |
| Claude 桌面版 | `/Applications/Claude.app` | `com.anthropic.claudefordesktop` | `3.1623.0` | `/Applications/Claude.app` |
| Codex 桌面版 | `/Applications/ChatGPT.app` | `com.openai.codex` | `26.1016.61520` | `/Applications/ChatGPT.app` |
| DSH 桌面版 | `/Applications/DeepSeek Harness.app` | `com.deepseek.dsh` | `0.2.0-rc.2` | `/Applications/DeepSeek Harness.app` |
| ZCode 桌面版 | `/Applications/ZCode.app` | `dev.zcode.app` | `3.15.3` | `/Applications/ZCode.app` |
| Claude Code URL Handler | `~/Applications/Claude Code URL Handler.app` | `com.anthropic.claude-code-url-handler` | 字段未观察 | `~/Applications/Claude Code URL Handler.app` |

五次 `mdfind` 均退出 0，只返回表中的一个位置。Codex 的应用包名字是 `ChatGPT.app`，本次观察到的 bundle id 是 `com.openai.codex`，不能仅由应用包名字推导 id。URL Handler 的 `Contents/MacOS/claude` 是指向 `~/.local/share/claude/versions/2.1.299` 的符号链接；它与主要 Claude 桌面包是两个登记项，没有把它当作另一份独立 CLI。

Mac 桌面包内的 CLI 如下；相对位置均以相应 `.app` 为根。表内成功运行只证明 `--version` 可用。

| 桌面版及观察版本 | 包内相对位置 | 文件形态 | 从原处运行与标准输出 |
| --- | --- | --- | --- |
| Claude app `3.1623.0` | 可独立调用的 CLI 未观察；`Contents/Resources/claude` 不存在 | 枚举全部包内普通文件，未发现独立的 `claude` CLI；`Contents/MacOS/Claude` 是桌面入口，未运行 | CLI 版本未观察；未解包 `app.asar`，不判断其内部是否包含相关实现 |
| Codex app `26.1016.61520` | `Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex` | 原生 Mach-O arm64 可执行文件 | 原路径加 `--version`，退出 0，`codex-cli 0.163.0-alpha.11.1` |
| Codex app 同上 | `Contents/Resources/codex-cli/bin/codex` | POSIX shell 可执行脚本 | 原路径加 `--version`，退出 0，同上 |
| DSH app `0.2.0-rc.2` | `Contents/Resources/runtime/cli/bin/dsh` | POSIX shell 可执行脚本 | 原路径加 `--version`，退出 0，`0.2.0-rc.2` |
| ZCode app `3.15.3` | `Contents/Resources/glm/zcode.cjs` | `.cjs`，可执行的 `/usr/bin/env node` 脚本 | 原路径加 `--version`，退出 0，`0.16.9`；显式 `/opt/homebrew/bin/node <该文件> --version` 也退出 0，输出相同 |

Mac 独立 CLI 的实测如下。“npm 包布局”依据符号链接目标及 `package.json`，不声称观察到了原来的安装动作。

| Harness | 独立入口与实际位置 | 安装形态及版本输出 |
| --- | --- | --- |
| Claude Code CLI | `~/.local/bin/claude` → `~/.local/share/claude/versions/2.1.299` | 原生 Mach-O arm64；独立安装器布局是推断，安装方式未观察；`--version` 退出 0，`2.1.299 (Claude Code)` |
| Claude Code CLI 保留版本 | `~/.local/share/claude/versions/2.1.283`、`~/.local/share/claude/versions/2.1.284` | 两个文件各运行 `--version` 均退出 0，分别输出 `2.1.283 (Claude Code)`、`2.1.284 (Claude Code)`；当前入口不指向它们 |
| Codex CLI | `~/.nvm/versions/node/v24.21.0/bin/codex` → 同一 Node 前缀下 `lib/node_modules/@openai/codex/bin/codex.js` | npm 包布局，`@openai/codex` 元数据版本 `0.162.1`；`--version` 退出 0，`codex-cli 0.162.1` |
| DSH CLI | `~/.local/bin/dsh` → `~/.local/share/dsh/lib/node_modules/@deepseek-ai/dsh/lib/bin.js` | 私有前缀中的 npm 包布局，元数据版本 `0.1.5-rc.1`；是否经独立安装器创建未观察；`--version` 退出 0，`0.1.5-rc.1` |
| ZCode CLI | 未安装独立 CLI；所查 PATH、常见目录和 Node 包目录未找到另一份 | 独立版本未观察，桌面版 `.cjs` 见上表 |

Mac 当前进程 PATH 中，指向这四个桌面版安装位置的目录只有 `/Applications/ChatGPT.app/Contents/Resources/codex-cli/codex-path`、`/Applications/ChatGPT.app/Contents/Resources`、`/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS`。前两处没有名为 `codex` 的普通文件候选，最后一处有。完整 PATH 原样见 `mac-initial.stdout`；没有向登录 shell 要 PATH，也没有把其他应用（例如 VMware Fusion）的 PATH 项算入四个 harness。

Windows 当前账户的安装状态与三类系统登记交叉核对如下。四个卸载登记根均存在；唯一匹配四个 harness 的卸载条目来自 HKCU 的非 WOW6432Node 根。另三个根均未观察到对应条目。

| Harness | 桌面版状态与卸载登记 | 当前账户 MSIX/AppX | 开始菜单 |
| --- | --- | --- | --- |
| Claude Code / Claude | 未安装桌面版；四个卸载登记根无对应条目，已查常见目录也无 | 未观察到对应包 | 用户与公共 `.lnk`、`Get-StartApps` 均无对应项 |
| Codex | 已安装；四个卸载登记根无对应条目 | `OpenAI.Codex`，版本 `26.1016.6573.0`，安装位置见下文 | `.lnk` 未观察；`Get-StartApps` 的名称是 `ChatGPT`，AppID 为 `OpenAI.Codex_2p2nqsd0c76g0!App` |
| DSH | 已安装；HKCU 条目 `1bf39983-50d0-5fe0-9ef4-cece76f67c5e`，字段见下表 | 未观察到对应包 | `~\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\DeepSeek Harness.lnk` → `D:\Deepseek\DeepSeek Harness.exe`；参数为空，工作目录 `D:\Deepseek`；`Get-StartApps` 的 AppID 为 `com.deepseek.dsh` |
| ZCode | 未安装桌面版；四个卸载登记根无对应条目，已查常见目录也无 | 未观察到对应包 | 用户与公共 `.lnk`、`Get-StartApps` 均无对应项 |

| DSH 卸载登记字段 | 实测值 |
| --- | --- |
| `DisplayName` | `DeepSeek Harness 0.2.1-rc.2` |
| `InstallLocation` | `D:\Deepseek` |
| `DisplayIcon` | `D:\Deepseek\DeepSeek Harness.exe,0` |
| `UninstallString` | `"D:\Deepseek\Uninstall DeepSeek Harness.exe" /currentuser` |
| `DisplayVersion` | `0.2.1-rc.2` |

Codex 的完整包名为 `OpenAI.Codex_26.1016.6573.0_x64__2p2nqsd0c76g0`，`InstallLocation` 为 `C:\Program Files\WindowsApps\OpenAI.Codex_26.1016.6573.0_x64__2p2nqsd0c76g0`，以下称 `<Codex 包>`。应用清单的 `App` 项指向 `app/ChatGPT.exe`，`CodexCoreCommandRunner` 项指向 `app/resources/codex-command-runner.exe`，两项的 `EntryPoint` 均为 `Windows.FullTrustApplication`。这些是应用清单观察，没有启动这些入口。

Windows 桌面包内 CLI 的实测如下。Codex 包目录可被此 SSH 账户枚举并直接执行下面的 CLI；本次没有观察到 MSIX 限制阻止该 `--version`，不能据此推定其他账户或其他命令也可用。

| 桌面版及观察版本 | 相对安装根的位置 | 文件形态 | 从原处运行与标准输出 |
| --- | --- | --- | --- |
| Codex app `26.1016.6573.0` | `app\resources\codex.exe` | 原生 Windows 可执行文件，开头字节 `4D-5A-78-00` | 原目录直接执行加 `--version`，退出 0，`codex-cli 0.163.0-alpha.11` |
| Codex app 同上 | `app\resources\codex` | 无扩展名，开头 `7F-45-4C-46`（ELF） | 未运行；它在 Windows 包内的存在不构成 Linux 真机观察 |
| Codex app 同上 | `app\Codex.exe` | 20,272 字节、开头 `4D-5A-90-00` 的文件，版本描述字段为空 | 是否为可独立运行的 CLI 未观察；未运行，避免把未知入口当成 CLI |
| DSH app `0.2.1-rc.2` | `resources\runtime\cli\bin\dsh.cmd` | `.cmd`，全文见下；通过 `ELECTRON_RUN_AS_NODE=1` 将桌面可执行文件作为 Node 运行，并传入归档内的 CLI 路径 | `cmd.exe /d /c D:\Deepseek\resources\runtime\cli\bin\dsh.cmd --version`，退出 0，`0.2.1-rc.2` |
| Claude、ZCode | 桌面版未安装，包内 CLI 未观察 | 未观察 | 未观察 |

Windows 独立 CLI 的实测如下。`C:\nvm4w\nodejs` 本身是符号链接，目标为 `D:\nvm\v22.12.0`；表中保留实际命令被找到时的位置。`Get-Command -All` 同时列出了 Claude/Codex 的 `.ps1`、`.cmd` 和无扩展名入口，版本探测明确调用 `.cmd`，没有依赖 PowerShell 对 `.ps1` 的选择。

| Harness | 独立入口与形态 | 安装形态及版本输出 |
| --- | --- | --- |
| Claude Code CLI | `C:\nvm4w\nodejs\claude.cmd` → 同前缀 `node_modules\@anthropic-ai\claude-code\cli.js` | npm 包布局，元数据 `2.1.86`；`--version` 退出 0，`2.1.86 (Claude Code)` |
| Claude Code CLI | `~\.local\bin\claude.exe`；`~\.local\share\claude\versions\2.1.72` 与其 SHA-256、大小相同 | 原生 Windows 可执行文件，独立安装器布局是推断，安装方式未观察；前者 `--version` 退出 0，`2.1.72 (Claude Code)`；未重复运行内容相同的版本库文件 |
| Codex CLI | `C:\nvm4w\nodejs\codex.cmd` → 同前缀 `node_modules\@openai\codex\bin\codex.js` | npm 包布局，元数据 `0.163.0`；`--version` 退出 0，`codex-cli 0.163.0` |
| DSH CLI | 未安装独立 CLI；找到的 `dsh.cmd` 来自桌面目录 | 在所查当前 Node 前缀、常见 npm 根和 `~\.local` 位置未观察到独立包；独立版本未观察 |
| ZCode CLI | 未安装独立 CLI | 同上范围与 PATH 中未观察到入口或包；版本未观察 |

Windows 用户注册表 `HKCU\Environment` 的 PATH 为 `REG_SZ`，其中 `D:\Deepseek\resources\runtime\cli\bin` 直接指向 DSH 桌面版；其余四个 harness 的桌面安装位置未观察到 PATH 项。机器注册表 `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment` 的 PATH 为 `REG_EXPAND_SZ`，没有指向这四个桌面版安装位置的目录。用户的 `~\AppData\Local\Microsoft\WindowsApps` 是通用 App Execution Alias 目录，不是 `<Codex 包>` 的安装目录，不能把它记作对该包目录的直接引用。原始未展开值与展开值分别在 `windows-detail.stdout`、`windows-initial.stdout`；没有写注册表。

本任务要求照录的 Windows CLI `.cmd` 内容如下。DSH 桌面版 `0.2.1-rc.2` 的 `resources\runtime\cli\bin\dsh.cmd`：

```bat
@echo off
setlocal DisableDelayedExpansion
set "ELECTRON_RUN_AS_NODE=1"
"%~dp0..\..\..\..\DeepSeek Harness.exe" --expose-internals "%~dp0..\..\..\app.asar\dsh\node_modules\@deepseek-ai\dsh-desktop-host\lib\cli.js" %*
exit /b %errorlevel%
```

Claude Code npm 包 `2.1.86` 对应的 `C:\nvm4w\nodejs\claude.cmd`：

```bat
@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0

IF EXIST "%dp0%\node.exe" (
  SET "_prog=%dp0%\node.exe"
) ELSE (
  SET "_prog=node"
  SET PATHEXT=%PATHEXT:;.JS;=;%
)

endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\node_modules\@anthropic-ai\claude-code\cli.js" %*
```

Codex npm 包 `0.163.0` 对应的 `C:\nvm4w\nodejs\codex.cmd`：

```bat
@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0

IF EXIST "%dp0%\node.exe" (
  SET "_prog=%dp0%\node.exe"
) ELSE (
  SET "_prog=node"
  SET PATHEXT=%PATHEXT:;.JS;=;%
)

endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\node_modules\@openai\codex\bin\codex.js" %*
```

四份 harness 候选数依次为 Claude 34、Codex 33、DSH 33、ZCode 34。`exists=true` 的候选只有 Claude 的 `common` 独立 CLI、Codex 的 `nvm` 独立 CLI 与 `path` 包内 CLI、DSH 的 `common` 独立 CLI、ZCode 的 `app` 包内 `.cjs`。Claude 的固定 `app` 路径和 Codex 的固定 `app` 路径均不存在；DSH 桌面入口没有 `app` 候选。这里的文件存在性来自快照，不能替代上文的版本实测。完整快照分别保存在 `tmp/harness-desktop-entries/claude-snapshot.json`、`tmp/harness-desktop-entries/codex-snapshot.json`、`tmp/harness-desktop-entries/dsh-snapshot.json`、`tmp/harness-desktop-entries/zcode-snapshot.json`。

Linux 的桌面安装、系统登记、包内 CLI、独立 CLI、PATH 与当前发现结果：均未观察。Windows 包内存在 ELF 文件只是一条 Windows 文件观察，没有在 Linux 上执行它。

## 取了什么，没取什么

取这些真机事实供 ADR-017 第 10 条待改时使用：桌面版可以由 macOS bundle id 或 Windows AppX/卸载登记/开始菜单给出位置；实际 CLI 可以在更深的目录，是原生文件、shell/`.cmd` 包装或需要 Node 的 `.cjs`；桌面版和独立 CLI 同时存在且版本不同。这些观察说明现在三处 macOS 固定路径没有覆盖本次实际安装，其中两处已不对应本机 CLI；Windows DSH 这次靠注册表 PATH 暴露目录，Codex 则有 AppX 安装证据。本文没有决定各登记来源的优先级、去重规则或未来 harness 的声明格式，未将一台机器的路径硬编码成新规则。

没取任何厂商业务代码，也不凭应用名称猜 CLI 或执行未知的桌面入口。没有把一次 `--version` 成功记作完整 harness 能力验证，没有把未装桌面版的 Windows Claude/ZCode 或没有真机的 Linux 补成默认安装路径。没有改产品、测试、ADR 或安装环境；本记录是调查证据，尚非发现规则的实现或验收。

## 时效

以上仅代表 2026-10-22 采集时的两个账户、这些 app/CLI 版本及提交 `bfd61bb6`。应用更新、重新安装、移动应用包、独立 CLI 升级、PATH 或默认 Node 变化、Spotlight 索引变化、MSIX 包或账户权限变化、产品候选规则变化，都会使对应事实过时。Linux 以及本次未安装的桌面版仍需真机补查，不能由本记录推导。
