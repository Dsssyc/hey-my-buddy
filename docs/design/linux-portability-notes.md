# Linux 可移植性调查（2026-10-16）

本次调查以 `275ed6af7db919734c6011c837eeb3ca896d10a5` 为基线。五项现象均已定位：第 1、4 项来自同一个私有组目录校验缺陷，第 2 项是合成运行时夹具漏复制 numpy 的共享库，第 3 项来自 Docker Desktop 的 Rosetta 缓存，第 5 项是测试把 inode 编号当作永久文件身份。本次修复三处根因，覆盖第 1、2、4、5 项；按本轮决定，第 3 项保留测试、断言及 Docker 设置，原生 Linux 验证留待依赖支持 arm64 后完成。没有修改版本、解释器选择或超时，也没有执行实际产品安装。

## 环境和运行范围

Linux 使用 Docker Desktop 的 `python:3.13-slim`、`--platform linux/amd64`，容器内为 CPython 3.13.16、uv 0.13.1，并安装 git、procps、ca-certificates。先将当前检出 `git clone --no-hardlinks` 到系统临时目录，再将该克隆挂到容器的 `/src`，把本次修改同步到该克隆；没有挂载受管检出，未提交、未切换分支或标签。容器创建 tester 用户和同名主组，以 `su tester -c 'cd /src && uv run --frozen python -m hey_my_buddy.cli.checks --jobs 1 <files>'` 运行；实际 UID/GID 均为 1000，`su tester -c umask` 返回 `0002`。没有在产品、运行器、测试或验证命令中调整 umask。

首轮通过 Docker 的 `exec -u` 直接运行普通用户，未经过 PAM，会话继承的是 `0022`，因此当时第 1、4 项未复现。续接回合按原始方式通过 `su` 创建会话后复现；前后差别是会话的 umask，不是容器是否使用普通用户。Host 补充的原始安装测试输出包含 CPython 下载完成、创建虚拟环境、安装 12 个包，随后 CLI 才返回 `LAUNCH_ACCESS_DENIED`，因此退出 1 并不来自下载。

macOS 使用指定整合检出的既有虚拟环境解释器（CPython 3.13.3），以 `PYTHONPATH=src <integration-checkout>/.venv/bin/python -m hey_my_buddy.cli.checks --jobs 2 <files>` 检查当前受管检出的源码。入口清除了继承的 `HMB_STATE_DIR`、`HMB_RUNTIME_ROOT`、`HMB_RUNTIME`、`HMB_RUNTIME_IDENTITY`、`HMB_AGENT_CREDENTIAL_FILE`、`HMB_AGENT_CREDENTIAL_FD`、`VIRTUAL_ENV` 和 `UV_PROJECT_ENVIRONMENT`；其余测试隔离由检查运行器负责。

两个平台的最终 `<files>` 相同，均为下列十个文件，新增组校验用例也在 `test_transport_attach.py` 内；没有运行完整检查，也没有手动启动 unittest 进程。

```text
tests/python/blackboard/service/test_service_environment.py
tests/python/protocol/test_transport_attach.py
tests/python/install/test_local_launcher.py
tests/python/install/test_distribution.py
tests/python/install/test_upgrade_host_cutover.py
tests/python/buddy/harnesses/test_inquiry_owner.py
tests/python/protocol/test_state_boundaries.py
tests/python/install/test_execution_install_fence.py
tests/python/install/test_packaging.py
tests/python/install/test_runtime.py
```

## 1、4. 私有组目录被拒，下载后的 CLI 随之退出

原始现象：`test_service_environment.py` 与 `test_transport_attach.py` 的冷启动预检报 `State directory is not owner-private`；安装相关测试在 CPython 3.12 下载成功、依赖安装完成之后返回 `LAUNCH_ACCESS_DENIED`。Host 补齐的完整输出将第 4 项与第 1 项关联到同一个冷启动预检，不能把下载进度行当成失败原因。

复现所得：在 `su tester` 的 `0002` 会话中，通过检查运行器跑未修复的 `test_service_environment.py`，两个冷启动用例报同样的权限错误。独立预检探针按运行器的方式建立 `<temp>/p000/tmp/state`：`state` 为 tester:tester、UID/GID 1000、`0700`，`tmp` 为同一属主属组的 `0700`，而自动创建的 `p000` 为同一属主属组的 `0775`；tester 组的成员列表为空。其上临时根为 `0700`，系统 `/tmp` 为 root:root 的 `01777`，`/` 为 root:root 的 `0755`。`_private_directory(state)` 返回 `True`，`_trusted_ancestors(state)` 返回 `False`，预检返回 `LAUNCH_ACCESS_DENIED`，原因为 `OSError('State directory is not owner-private')`；被拒的具体层是 `p000`。

读码所得：运行器为 `<private-root>/pNNN/tmp` 执行 `mkdir(mode=0o700, parents=True)`；Python 自动创建中间父目录时使用默认权限，再受调用者 umask 影响，故 `pNNN` 在 `0002` 下为 `0775`。旧 `_trusted_ancestors` 把非 sticky 祖先的组可写位与其他用户可写位一起拒绝，没有区分只有本人可用的私有主组；真实用户在相同 umask 下创建的 `~/.local`、`~/.local/share` 也会受该规则影响。该处需要表达的是能否被其他用户替换，不能仅由组可写位判断。

改法：`protocol/transport.py` 对非 sticky 的组可写祖先查询系统账户和组数据库，仅当目录组是当前有效用户的主组、组名与用户名一致、组成员列表不含其他用户时允许该组写权限。没有账户或组记录时不授予这项信任；共享组、非主组、含其他成员的组，以及非 sticky 的其他用户可写目录仍然拒绝，祖先属主仍须为本人或 root。冷启动仍将状态目录设为 `0700`，读端仍拒绝状态目录自身的组或其他用户权限；此次例外只适用于祖先。macOS 与 Linux 使用同一条 POSIX 规则，没有读取 login.defs 或 PAM 配置，没有修改运行器的建目录方式或 umask。

验证依据：新增用例模拟账户、组和目录元数据，覆盖私有主组的空成员列表及仅含本人时接受，共享组名、非主组 GID、含其他用户的成员列表及非 sticky 的其他用户可写目录时拒绝；另有用例确认即使组被判为私有，组可写的状态目录仍被拒绝。这些测试不依赖运行机器的实际组配置。旧实现使新增的两个私有组接受子用例失败，修改后通过；Linux 同一个 `su` 会话的独立预检在目录权限、属主和属组均未改变的情况下通过，`_trusted_ancestors` 返回 `True`。

解释器边界：共享技能的 POSIX 启动器在开发模式及 install/upgrade 入口明确选择 `--python 3.12`，产品物化命令同样指定 3.12；项目允许 `>=3.12,<3.15`，外层检查使用镜像中的 3.13、子命令另行获取 3.12 是现有明确选择。本次未改动该选择；首轮独立的 `uv python install 3.12` 也已成功下载并安装 CPython 3.12.15。

## 2. numpy 的共享库

现象与复现：基线 `test_upgrade_host_cutover.py` 报 `libscipy_openblas64_-f48b354e.so` 找不到；复用其 `private_runtime` 夹具的 `test_execution_install_fence.py` 三个用例也因替换 daemon 退出而失败。

读码所得：缺失发生在 `tests/python/install/test_upgrade_host_cutover.py::HostCutoverTests.private_runtime`。它创建合成虚拟环境，然后仅按模块的 `__file__` 复制模块目录与 `.dist-info`；Linux numpy wheel 放在包目录旁的 `numpy.libs` 因此被漏掉。产品的 `install/runtime.py::_materialize_locked` 使用 `uv sync --frozen --no-dev --python 3.12` 安装完整依赖，并没有该手工复制步骤。本次证据不支持“正式产品物化漏掉共享库”的推测，也未执行实际产品安装。

改法：夹具按 `importlib.metadata.distribution(...).files` 复制 site-packages 内的完整发行包文件，保留包旁的共享库和发行包元数据；不复制清单中指向 site-packages 外的命令脚本和字节码缓存。删去了按导入目录猜测发行包内容的旧实现，同一条规则用于两个平台。首轮修改后的 Linux `test_upgrade_host_cutover.py` 两个用例与 `test_execution_install_fence.py` 三个用例全部通过，证明私有运行时可以导入依赖并实际启动、切换 daemon。

## 3. 空 HOME 中出现 `.cache`

现象与复现：Linux 的 `test_inquiry_owner.py` 两个用例以及 `test_state_boundaries.py` 三个子用例因空 HOME 内出现 `.cache` 而失败，实际目录为 `.cache/rosetta`。

定位依据：在同一个普通用户的全新 HOME 中运行 `/usr/local/bin/python3 -S -c pass`，不导入本项目、C-Two、fastdb4py 或 numpy，也会创建同样的 `.cache/rosetta`。另一探针在导入 C-Two 前安装 Python audit hook，没有捕获 `os.mkdir`，但解释器退出后该目录已经存在；容器的 `ps` 同时显示 amd64 进程通过 `/run/rosetta/rosetta` 执行。这将写入来源定位到 Docker 的 Rosetta 模拟环境，而非产品或 Python 库的默认状态选择。

本轮决定：保留测试、空 HOME 断言与 Docker 的设置，既不忽略也不删除缓存来令断言通过。模拟容器内的这两个文件若仍只因 `.cache/rosetta` 失败，不计为本次产品缺陷。待 C-Two 去掉 fastdb4py 的硬依赖、能够在 Linux arm64 上原生运行后，再补充这两个文件的 Linux 通过证据；当前不声称已经原生验证。

## 5. 相等的 inode 编号

现象与复现：Linux 基线的 `test_runtime.py::RuntimeMaterializationTests.test_concurrent_installers_share_one_lock_until_ready` 失败，比较的两个 inode 编号都为 `33946373`，与此前 `33955697 == 33955697` 是同类现象。

读码所得：测试协调两个安装线程，证明等待者在原持有者删除锁文件后重新打开当前路径。产品 `installation_lock` 在持锁后比较打开文件与路径的 `(st_dev, st_ino)`，发现锁路径已消失或变化便重新打开；该逻辑发生在旧 fd 仍打开时。测试却在所有旧 fd 关闭后才比较仅存的整数编号，此时 Linux 文件系统可以立刻把已释放的 inode 编号分配给新文件，两个编号相同不等于文件对象相同。

改法：测试在第一次锁操作前，对锁路径进行一次独立的只读打开，将该 fd 保留到测试清理。旧 inode 因此仍被引用，新文件不能复用其编号；独立打开不复制持锁 fd，不会延长 flock 的寿命。原有的竞争协调、只调用一次 uv、等待者必须重开以及成功后删除锁文件等断言全部保留，产品锁代码无需修改。首轮修改后的 Linux `test_runtime.py` 已通过。

首次 macOS 修复验证中，测试夹具一度使用 `os.dup` 保留旧 inode；重复描述符也保留了 flock，导致锁测试阻塞。终止这一私有测试进程后，检查运行器清理了私有根；随后将其改为独立打开并重新运行同一组文件。该中间验证不计为通过证据。

## 最终检查和清理

macOS 最终同组十个文件全部通过，共 129 个测试、0 个跳过，检查运行器退出 0 并确认私有根已清理。新增的 6 个测试包含在内；`test_runtime.py` 的锁竞争用例和两个使用私有运行时夹具的文件也均通过。

Linux 最终同组检查在 `su tester` 的 `0002` 会话中运行，八个文件通过，两个文件失败，检查运行器退出 1 并确认私有根已清理。通过文件共报告 105 个测试；通过文件为 `test_service_environment.py`、`test_transport_attach.py`、`test_local_launcher.py`、`test_distribution.py`、`test_upgrade_host_cutover.py`、`test_execution_install_fence.py`、`test_packaging.py` 和 `test_runtime.py`。没有出现权限拒绝、共享库缺失、inode 编号误判或超时。

Linux 仅 `test_inquiry_owner.py` 的两个用例与 `test_state_boundaries.py` 的三个子用例保留空 HOME 内出现 `.cache` 的断言失败。为确认写入内容，再通过检查运行器只跑这两个文件，同时在容器内只读观察私有目录：三个失败 HOME 的 `.cache` 都只包含 `rosetta` 子目录，没有其他条目；该复查同样退出 1 并清理私有根。依本轮决定，这些失败归于模拟环境，不计为本次产品缺陷；两文件的原生 Linux 通过证据仍待后续补充。

容器内没有 Node.js；按任务范围，未运行 `cli/test_host_preview.py` 与 `buddy/harnesses/zcode/test_zcode_hooks_probe.py`，也没有用 Node 缺失解释本次其他文件的失败。源码使用真实克隆，避免 archive 缺少 Git 元数据的问题。没有启动、停止或重启日常服务，没有安装产品，没有访问用户 harness 配置或凭据，也没有调用模型；独立 Codex 子代理只做了指定规则的只读审查，未修改文件或运行测试，其报告不作为通过证据。

原始检查输出与探针输出留在忽略目录 `tmp/linux-portability/`，不作为公开仓库文件提交。

本次两轮创建的容器及系统临时目录内的克隆均已删除，没有操作其他容器或镜像；检查运行器的基线、最终、缓存复查及中间中断运行均报告私有根已清理。没有删除检出内的文件，没有使用 git stash，也没有改动分支、标签或版本。

与本轮要求不一致的情形：没有；Linux 两个缓存断言的保留和后续原生验证依照本轮明确决定记录。
