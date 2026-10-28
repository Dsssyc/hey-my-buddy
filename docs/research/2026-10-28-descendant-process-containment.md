# 一次执行的全部后代怎样找到、怎样证明已停：三个系统的机制与成熟工具的做法

## 问题

Worker 的复现报告（2026-10-28）证实：POSIX 上的停止只观察根的进程组，调用 setpgid 或 setsid 的后代在“已确认停止”之后仍活着。Host 的工作提议是：以根的 POSIX 会话加上仍在父子链上的后代为 POSIX 的边界，拒绝删除仍被活进程用作工作目录的检出，并把 macOS 上 setsid 加双重 fork 的守护进程记为没有覆盖的边界。开发者质疑这条边界：追踪全部后代应当是系统软件的常见需求，要看社区怎样做。这份记录回答：在 macOS（普通账户，没有 root、特殊权利或系统扩展）、Linux（非特权）与 Windows 上，成熟的工具靠什么找到并停掉自己起的进程的全部后代，包括 setsid 加双重 fork 的守护进程；macOS 上有没有比会话更强、普通账户就能用的机制。它服务于修订 [ADR-031](../decisions/031-one-process-per-execution.md) 第 4、5 条（停止证据）：黑板要用一种对所有 harness（Codex、Claude Code、ZCode、DSH，以后的 agy）都一样的机制证明一次执行的进程已经结束。选择待开发者定。

## 看了什么

- **本仓库**：[ADR-030](../decisions/030-harness-layer-scope.md) 第 2 条与文末两项 2026-10-15 的修改；ADR-031 第 4、5、7 条；`protocol/process_handle.py` 与 `protocol/process_identity.py`；Worker 的复现报告（在检出的 `tmp/` 下，未入库）。
- **本机安装的软件**（macOS 27.0.1，Darwin 27.0.0，arm64；只读）：Command Line Tools 里 MacOSX27.0.sdk 的头文件声明与注释（`sys/event.h`、`sys/proc_info.h`、`sys/spawn.h`、`sys/syscall.h`、`libproc.h`、`bsm/audit.h`、`bsm/libbsm.h`、`EndpointSecurity/` 下各头文件、Kernel.framework 的 `kern/coalition.h`）；SDK 里的手册页 kqueue(2)、setaudit_addr(2)、auditon(2)、launchd.plist(5)、launchctl(1)；系统自带的 `taskinfo`、`ps -L`、`launchctl`。头文件只读了声明与注释，当作接口说明用。
- **公开文档**（2026-10-28 读取）：
  - Apple：Endpoint Security 的 [`es_new_descendants_client`](https://developer.apple.com/documentation/endpointsecurity/es_new_descendants_client(_:_:))、[`es_new_client`](https://developer.apple.com/documentation/endpointsecurity/es_new_client(_:_:)) 与 [ES 客户端权利](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.endpoint-security.client)三页，经文档站的数据接口读了全文。
  - Linux：kernel.org 的 [cgroup v2](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)（读了前约七成，含核心接口文件与委派两节）与 [5.14 的 cgroup 合并请求](https://lkml.iu.edu/hypermail/linux/kernel/2107.0/00573.html)；man-pages 6.19 的 [cgroups(7)](https://man7.org/linux/man-pages/man7/cgroups.7.html)、[PR_SET_CHILD_SUBREAPER](https://man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html)、[pidfd_open(2)](https://man7.org/linux/man-pages/man2/pidfd_open.2.html)、[pidfd_send_signal(2)](https://man7.org/linux/man-pages/man2/pidfd_send_signal.2.html)、[pid_namespaces(7)](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html)、[user_namespaces(7)](https://man7.org/linux/man-pages/man7/user_namespaces.7.html)；systemd 262 的 [systemd.kill](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html)、[systemd-run](https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html)、[systemd.scope](https://www.freedesktop.org/software/systemd/man/latest/systemd.scope.html)、[systemd.resource-control](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html)、user@.service，以及 systemd.io 的 [cgroup 委派说明](https://systemd.io/CGROUP_DELEGATION/)；[Ubuntu 24.04 发行说明](https://documentation.ubuntu.com/release-notes/24.04/)里非特权用户命名空间一节。
  - Windows：Microsoft Learn 的 [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)、[Nested Jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs)、[Process Creation Flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags)，三页都是 2025-07-14 版。
  - 工具：Jenkins 的 [Spawning Processes From Build](https://www.jenkins.io/doc/book/managing/spawning-processes/)、[Aborting a build](https://www.jenkins.io/doc/book/using/aborting-a-build/) 与旧 wiki 的 [ProcessTreeKiller](https://wiki.jenkins.io/display/JENKINS/ProcessTreeKiller)；GitLab Runner 的 [Shell executor](https://docs.gitlab.com/runner/executors/shell/) 与 [feature flags](https://docs.gitlab.com/runner/configuration/feature-flags/)；Bazel 的 [Sandboxing](https://bazel.build/docs/sandboxing) 与 [2015 年的沙箱博客](https://blog.bazel.build/2015/09/11/sandboxing.html)（2017 年更新）；Nix 2.30 的 [nix.conf 参考](https://nix.dev/manual/nix/2.30/command-ref/conf-file)；Supervisor 4.3.0 的[配置说明](https://supervisord.org/configuration.html)；psutil 文档（开发版）的 API 与 Recipes 页；TeamCity 2026.2 的 [Swabra](https://www.jetbrains.com/help/teamcity/build-files-cleaner-swabra.html)；Docker Desktop 的 [VMM 页](https://docs.docker.com/desktop/features/vmm/)；npm 上 tree-kill 1.2.2 的说明文字。
  - 二手：Elastic Security 8.19 的检测规则 [Tampering with RUNNER_TRACKING_ID](https://www.elastic.co/guide/en/security/8.19/tampering-with-runner-tracking-id-in-github-actions-runners.html)。
  - 搜过、没找到官方说明的：GitHub 文档里的 `RUNNER_TRACKING_ID`；Azure Pipelines 代理怎样清理进程；Buck2 的本地动作怎样停进程；pm2 的 treekill；Watchman。
- **自己的实验**（2026-10-28，普通账户；脚本与原始结果在 `<scratch>/containment-research/`，一个会话专用的临时目录）：
  - `kq_probe.py`（跑两次）与 `spawn_probe.py`：kqueue 的 NOTE_TRACK、NOTE_FORK，附着到非子进程与 pid 1，以及用追踪器追 setsid 加双重 fork；被追的根有 Python 写的，也有 C 写的（`dfork_root`、`spawn_root`）。`listchild_probe.py`：中间进程退出之后还能不能从它列出孙进程。
  - C 写的 `audit_probe`（审计会话号）、`es_probe`（两种 ES 客户端）、`token_signal_probe`（按审计令牌发信号）。
  - `launchd_probe.py`：用 `launchctl submit` 往本账户的 launchd 域提交一个临时任务（跑两次），看它的主进程退出与被移除时 launchd 停掉哪些进程，结束时 `launchctl remove`。
  - 工具：系统的 `/usr/bin/python3` 3.9.6，Apple clang 21.0.0。Bash 沙箱拒绝执行 setuid 的 `/bin/ps`、拒绝 `launchctl submit`、读不到 Finder 的任务端口，这几次在沙箱外跑。没有用 sudo，没有安装任何东西，没有调用模型，没有碰日常黑板与服务；实验起的进程都按 pid 与启动时刻核对后停掉，临时任务已移除，最后清点没有残留。
- 没有读任何项目的源码，包括 XNU、Jenkins、runner、Bazel 与 Nix。搜索结果的摘要里出现过别的项目源文件的注释，没有打开，也没有采用。

## 看到的事实

**macOS 27.0.1（普通账户）**

- **kqueue 不跟随 fork。** 头文件写明 NOTE_TRACK、NOTE_TRACKERR、NOTE_CHILD 自 10.5 起不再支持，NOTE_FORK 的事件里不带子进程号；SDK 里的 kqueue(2) 已不提 NOTE_TRACK（厂商自述）。实测：注册 NOTE_TRACK 得到 ENOTSUP；NOTE_FORK 事件的 data 为 0；posix_spawn 同样触发 NOTE_FORK；能附着到同一账户的非子进程，也能附着到 pid 1（本机实测）。
- **事件驱动的追踪在实测里没有漏，但没有保证。** 追踪器收到 NOTE_FORK 后用 `proc_listchildpids` 找新的子进程并接着附着。被追的根反复做 setsid 加双重 fork，中间进程一 fork 就退出（Python 写的 120 次，C 写的 fork 与 posix_spawn 两种各 200 次）；负载均值约 8 至 12（10 核）时，520 个守护进程全部被追到（本机实测）。窗口确实存在：中间进程一退出，哪怕还没被回收，孙进程就改挂到 launchd 下，从中间进程再也列不出来（本机实测）。所以这只说明这台机器上追踪器跑得比中间进程的退出快，不是内核保证；追踪器不在时什么也看不到（推断）。psutil 的文档也写明，树里一个进程消失后它的后代不再返回；tree-kill 1.2.2 在 macOS 上用 `pgrep -P`、在 Linux 上用 `ps --ppid` 列子进程（各自的说明）。
- **审计会话按登录分，不按执行分。** setsid 与 setsid 加双重 fork 之后，后代的审计会话号不变；同一图形登录里的 Finder 也是这个号（本机实测）。普通账户用 `setaudit_addr` 申请新会话得到 EPERM（手册写明要特权，本机实测相同）；用 `auditon` 读进程的审计信息，连读自己都得到 EPERM；经 `task_name_for_pid` 取审计令牌能读同一账户进程的会话号（沙箱外实测），读 pid 1 失败。`audit_session_self/join/port` 在头文件里属于 Apple 私有接口（厂商头文件）。
- **联盟（coalition）普通账户用不上。** SDK 的用户态头文件里没有可调用的联盟接口，只有系统调用号、类型名与跟踪码；Kernel.framework 的 `kern/coalition.h` 写明只有内核私有接口（厂商头文件）。`taskinfo --coalitions-only` 与 `--responsibility` 都要求 root，`ps` 没有联盟这一列，`launchctl procinfo`（负责进程与审计会话）也要 root（本机实测，后者手册也这样写）。
- **launchd 只停进程组。** launchd.plist(5)：任务死掉时，launchd 杀掉与任务同进程组的剩余进程，AbandonProcessGroup 关掉这一行为（厂商自述；SDK 里这页的日期是 2019-07-30）。实测两次：`launchctl submit` 提交的任务落在 `gui/<uid>` 域，`launchctl print` 显示它有以标签命名的资源联盟与 jetsam 联盟；主进程退出时，同组的子进程被停掉，setpgid、setsid、setsid 加双重 fork 的三个子进程活过了主进程的退出，也活过了随后的 `launchctl remove`。没有核对它们是否还在那个联盟里。被提交的任务本身 ppid 为 1，不是提交者的后代（本机实测）：后代可以借 launchd 起出任何按父子关系划界的办法都看不到的进程（推断；`open` 与 XPC 这类经 launchd 起的进程同理，未测）。
- **Endpoint Security 在 macOS 27 有了只看后代的客户端。** `es_new_client` 要 ES 权利、用户在“完全磁盘访问”里的批准与 root（厂商文档与头文件）。macOS 27.0 新增的 `es_new_descendants_client` 只看调用者与它的全部后代，包括建立时已有的与此后递归 fork 或 exec 的，其余进程一概看不到；要 ES 权利，不要 root，也不要用户批准；调用者不是 root 时，它与后代都不能执行 setuid/setgid 程序，这样的 exec 在运行之前就被杀掉（厂商文档）。这个权利要向 Apple 申请（厂商文档）。实测：自签的探针调用它得到 `ERR_NOT_ENTITLED`，调用 `es_new_client` 得到 `ERR_NOT_PRIVILEGED`。本机的 setuid 程序有 `/bin/ps`、`top`、`sudo`、`su`、`crontab`、`at` 等（本机实测），在这种客户端下 harness 的工具调用 `ps` 会被杀（推断）。ES 的进程描述带 `original_ppid`（改挂之后不变）与负责进程的审计令牌（厂商头文件）；改挂到 launchd 下的守护进程、经 launchd 代起的进程是否仍算后代，文档没有明说，未核对。
- **PID 重用有现成的防护。** `libproc.h` 公开声明了 `proc_signal_with_audittoken`，没有文档（厂商头文件）。实测：普通账户可以按审计令牌给自己的子进程发信号；exec 之前取的令牌在 exec 之后失效，返回 ESRCH，子进程不受影响。ES 头文件写明 (pid, pidversion) 标识一次具体的执行，exec 让 pidversion 增加（厂商头文件）。
- 本会话的 Bash 沙箱拒绝执行 setuid 的 `/bin/ps`（Operation not permitted）。复现报告里 Codex CLI 0.164.0 的 command/exec 执行 `/bin/ps` 遇到的是同一个错误，也许同因（推断，未核对）。

**Linux（本次没有实测）**

- **cgroup v2 是现成的容器**（kernel.org）：每个进程恰在一个 cgroup 里，fork 出来的进程生在父进程的 cgroup 里；写 `cgroup.kill` 用 SIGKILL 停掉整棵子树，并处理好并发的 fork、不受迁移影响（5.14 合入，合并请求说它要取代用户态“一直杀到空”的循环）；`cgroup.events` 的 `populated` 为 0 表示子树里没有活进程，变化时可以 poll；被委派者不能把进程移出委派给它的子树。
- **非特权账户经 systemd 拿到 cgroup**（systemd 文档）：`KillMode=control-group` 是默认，停止时杀掉单元 cgroup 里剩下的全部进程；`systemd-run --user --scope` 在本账户的服务管理器里建临时 scope；文档的例子里 `user@.service` 带 `Delegate=yes`；systemd-run 的例子说用户管理器在至少有一个登录会话时一直在，开了 lingering 则登出之后也在；systemd 要求别的程序只在委派给它的子树里建 cgroup。没有 systemd 用户管理器的环境（容器、不用 systemd 的发行版）里，普通账户拿不到 cgroup；后代可以请用户管理器另起一个单元，那个进程就不在我们的 scope 里（两条都是推断）。
- **subreaper 与 pidfd**（man-pages 6.19）：设了 PR_SET_CHILD_SUBREAPER（3.4 起）的进程，后代成了孤儿就改挂到最近的活着的 subreaper 祖先，不交给 init；这个属性不随 fork 继承；手册说它适合要得知双重 fork 守护进程何时退出的管理者，并提到 systemd 也用它。setsid 改的是会话，不改父子链，挡不住 subreaper（推断）。pidfd_open（5.3）给出指向一个进程的描述符，进程结束时可读；pidfd_send_signal（5.1）避开 PID 重用的竞态。
- **PID 命名空间**（man-pages）：命名空间的 init 退出时，内核用 SIGKILL 停掉其中全部进程；3.8 起非特权进程能建用户命名空间，并在同一次调用里建 PID 命名空间。Bazel 的 linux-sandbox 靠它“可靠地杀掉全部进程，包括动作起的守护进程”（Bazel 文档）。Ubuntu 24.04 默认允许不受限的非特权程序建用户命名空间，但拒绝它在其中使用任何能力（发行说明），所以这条路在那里默认走不通（推断）。

**Windows（本次没有实测）**

- Microsoft Learn：进程进了 Job 之后，它用 CreateProcess 起的子进程默认也在 Job 里；只有 Job 设了 BREAKAWAY_OK 且父进程带 CREATE_BREAKAWAY_FROM_JOB，或 Job 设了 SILENT_BREAKAWAY_OK，子进程才不进；两种都不设时整棵树都在。Windows 8 起，把已在 Job 里的进程再放进别的 Job 会形成嵌套，终止一个 Job 连同它的子 Job 一起终止。原文写明：用 WMI 的 `Win32_Process.Create` 起的子进程不在 Job 里。产品现在的 Job 两种 breakaway 都没有设。任务计划程序、COM 服务器这类由别的服务代起的进程同样不在 Job 里（推断）。

**成熟工具的做法**

- **环境变量标记。** Jenkins 在构建的进程里放一个标记变量，停止时取全机进程及其环境变量，杀掉带标记的；想留下守护进程就把 `BUILD_ID` 改成别的值（Pipeline 改 `JENKINS_NODE_COOKIE`），也能用一个系统属性整体关掉（Jenkins 文档与旧 wiki）。Jenkins 还会发现守护进程拿着构建的 stdout/stderr 管道不放，给出警告并结束构建（Jenkins 文档）。GitHub Actions 的 runner 据第三方说用 `RUNNER_TRACKING_ID` 做同样的事，GitHub 自己的文档里没有找到；Elastic 的检测规则说，改这个变量是为了躲开自托管 runner 的跟踪与清理，并且出现在 Shai-Hulud 2.0 npm 蠕虫里（二手）。
- **进程组。** GitLab Runner 的 shell executor 在 Unix 上让主进程自成进程组，超时或取消时先 SIGTERM，10 分钟后 SIGKILL；Windows 上有一个默认关闭的开关改用 Job，强停时关闭 Job（GitLab 文档）。Supervisor 4.3.0 的 `stopasgroup`、`killasgroup` 把信号发给整个进程组，并要求被管的程序自己不要变成守护进程（Supervisor 文档）。launchd 见上。
- **内核容器。** systemd 用 cgroup；Bazel 在 Linux 上用 PID 命名空间，在 macOS 上用 `sandbox-exec`，文档不提 macOS 上怎样停掉动作的进程，2015 年的博客说 macOS 上似乎没有 PID 命名空间或 subreaper 这类机制，进程管理至今没能做得可靠（Bazel 文档与博客）。Docker Desktop 在 macOS 上把容器放进一台 Linux 虚拟机（Docker 文档），包含发生在 Linux 那一侧。
- **按账户。** Nix 让每个构建用构建组里的一个专用账户跑，同一时刻一个账户只跑一个构建（Nix 文档）；构建结束时是否按账户杀进程，文档没有写，只有源码里才看得到，未核实。这条路要 root 建账户，并换掉了 harness 的身份。
- **按检出。** TeamCity 2026.2 的 Swabra 能找出锁着检出目录里文件的进程，靠 Sysinternals 的 handle.exe，要管理员权限（TeamCity 文档）。
- **小结（推断）。** 有内核容器的地方（Linux 的 cgroup 与 PID 命名空间、Windows 的 Job），成熟工具都用它，守护进程一并停掉；macOS 上普通账户没有这样的容器，工具退回到进程组、父子树或环境变量标记，并写明守护进程会逃出去（launchd 的 AbandonProcessGroup、Supervisor 的要求、Bazel 的博客）。macOS 27 的 ES 后代客户端是第一个第一方的后代追踪，但要 Apple 授予的权利。

## 取了什么，没取什么

这份记录不做选择，下面各项待开发者定。它们落到 ADR-031 第 4、5 条（停止证据与执行的身份），第 7 条里 Windows 证据的措辞与 AGENTS.md 里关于身份的一段随之核对；若取丁，还要改 ADR-030 第 2 条那张穷尽的清单。甲、乙、己可以叠加。

- **甲：POSIX 会话加父子链，setsid 加双重 fork 记为没有覆盖的边界（Host 的工作提议）。** 最强的理由：不要新权限、新输入，ADR-030 的清单不变；根以 `start_new_session` 启动，会话号就是记录里的根 pid，存储形式不用改；macOS 与 Linux 同一种做法。代价：复现过的 setsid 加双重 fork 照样逃出；一次扫描是空的不等于“没有”；会话号也是会被重用的数字，只能像现在对进程组那样在完整身份核对之后才用（推断）；每次检查要遍历全机进程。
- **乙：执行进程从起根开始持续追踪后代**（macOS 用 kqueue 的 NOTE_FORK 加列子进程，Linux 用 subreaper 加 pidfd，Windows 不变）。最强的理由：普通账户、标准库就够；正好覆盖复现的情形，实测 520 次没有漏；Linux 上只要执行进程活着，孤儿留在它的树里由内核保证。代价：macOS 上原理上有竞态，没有内核保证；追踪器一停（执行进程崩溃、服务接回之前）就看不见，追到的身份要随工作规模写进文件（ADR-031 第 12 条），断档只能记为不知道，这改了跨边界的事实与存储形式；看不到经 launchd 或别的服务代起的进程；三个系统三套代码。
- **丙：macOS 用 ES 后代客户端**，由一个带权利签名的原生辅助程序做 harness 根的父进程。最强的理由：第一方、内核支持的整棵子树，不要 root 与用户批准，事件带审计令牌，追踪与强停在同一处。代价：要向 Apple 申请受限权利，Apple 会不会为这种用途批准不得而知；产品经 PyPI 与 uv 分发，要多出一个签名的原生产物（推断）；只有 macOS 27 起有；子树里执行 setuid 程序会被杀，`ps`、`top`、`sudo` 一类工具调用失败，违背 ADR-030 的“用户的 harness 原样使用”；改挂到 launchd 下的守护进程是否仍在范围内未核对；辅助程序崩溃之后同样看不见（推断）。
- **丁：环境变量标记**（Jenkins、GitHub Actions 的做法）。最强的理由：最常用的两个 CI runner 都这样做，标记随环境穿过 setsid 与双重 fork，三个系统都能用。代价：要改 ADR-030 的清单，而经 harness 的环境交东西正是 2026-10-15 撤回的做法；harness 未必把环境交给工具（ADR-030 记录 Codex CLI 0.162.1 只交 `HOME` 与 `PATH`），复现报告里空环境的 exec 也漏掉了；标记可以被改掉，真实的攻击里就这样做过（二手）；要读全机进程的环境，涉及隐私，Windows 上还要读别的进程的内存。
- **戊：Linux 用 systemd 的 scope 加 `cgroup.kill`，以 `populated` 为 0 为证。** 最强的理由：内核级的包含与原子强停，空也能证明；按名字可以在执行进程崩溃之后由服务接着核对，与 Windows 的命名 Job 对称（推断）；systemd 自己就这样做。代价：要有 systemd 用户管理器与 cgroup v2，没有时还得有另一套办法，同一件事两种机制；后代可以请用户管理器另起单元（推断）；scope 的名字要进身份，改存储形式；Linux 在本项目里还没有实测过。PID 命名空间更强，但改变了 harness 看到的进程环境，Ubuntu 24.04 上默认走不通。
- **己：删除检出之前的保护**（Host 提议的一部分）。最强的理由：不管进程怎样逃出，真正造成损害的是在活进程脚下删目录，这一步直接挡住它；TeamCity 有类似的做法。代价：用户自己在检出里开的编辑器或终端也会挡住删除；进程可以 chdir 离开；Windows 上读别的进程的工作目录没有公开的批量接口（复现报告）。
- **各项都用得上的一处改进**：强停时按身份发信号，macOS 用 `proc_signal_with_audittoken`（公开声明、没有文档，exec 之后要重取令牌），Linux 用 pidfd，去掉“核对之后再按 pid 发信号”之间的竞态。

## 时效

- Apple 改了 ES 后代客户端的门槛（例如不再要受限权利），公开了联盟或负责进程的普通账户接口，或 kqueue 恢复 NOTE_TRACK；
- launchd 改为按联盟停掉任务的全部进程；
- 某个 harness 改了起工具进程的方式（新的 POSIX 会话、经 launchd、经 systemd）；
- 发行版放开或收紧非特权用户命名空间，systemd 改了用户管理器的委派；
- GitHub 公开 runner 的清理机制，或 Jenkins 改了它的做法；
- 产品开始在 Linux 上实测，或 Windows 上出现由别的服务代起、不在 Job 里的进程。
