# Linux 可移植性调查（2026-10-16）

本次调查以 `275ed6af7db919734c6011c837eeb3ca896d10a5` 为基线，只修改测试夹具，没有修改产品代码、权限规则、超时或版本。第 2、3、5 项的根因已经复现并定位，其中第 2、5 项已修复；第 1、4 项的原始失败未复现，不能仅凭此前错误摘要认定其根因。

## 环境和复现边界

Linux 使用 Docker Desktop 的 `python:3.13-slim`、`--platform linux/amd64`，容器内为 CPython 3.13.16、uv 0.13.1，并安装 git、procps、ca-certificates；检查由 UID 1000 的普通用户运行。先将当前检出 `git clone --no-hardlinks` 到系统临时目录，再将该克隆挂进容器，容器内复制到普通用户所有的目录运行；没有挂载受管检出。修改后的两个测试文件单独同步到容器里的克隆副本，未提交、未切换分支或标签。

macOS 使用开发者指定的整合检出的既有虚拟环境解释器（CPython 3.13.3），以 `PYTHONPATH=src <integration-checkout>/.venv/bin/python -m hey_my_buddy.cli.checks --jobs 2 <files>` 检查当前受管检出的源码。入口清除了继承的 `HMB_STATE_DIR`、`HMB_RUNTIME_ROOT`、`HMB_RUNTIME`、`HMB_RUNTIME_IDENTITY`、`HMB_AGENT_CREDENTIAL_FILE`、`HMB_AGENT_CREDENTIAL_FD`、`VIRTUAL_ENV` 和 `UV_PROJECT_ENVIRONMENT`；其余测试隔离由检查运行器负责。

Linux 的基线与修改后检查均使用 `uv run --frozen python -m hey_my_buddy.cli.checks --jobs 1 <files>`。两个平台的 `<files>` 相同，均为下列十个文件，没有运行完整检查，也没有手动启动 unittest 进程。

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

## 1. 状态目录权限

现象：此前记录包含 `State directory is not owner-private`，以及启动器的 `LAUNCH_ACCESS_DENIED` 与预期 `SERVICE_UNAVAILABLE` 不符。本次 Linux 基线中 `test_service_environment.py`、`test_transport_attach.py`、`test_local_launcher.py`、`test_distribution.py` 全部通过，修改后也全部通过，没有复现该现象。

读码所得：`protocol/transport.py` 的 `_private_directory` 校验状态目录为当前有效用户所有、没有组或其他用户权限、路径不含链接；`_trusted_ancestors` 要求祖先由当前有效用户或 root 所有，并要求组或其他用户可写的祖先带 sticky 位。该规则没有 macOS/Linux 分支，并且已经允许 root 所有、模式 `01777` 的 `/tmp`。sticky 位不能替代属主检查，因为目录如果属于其他用户，该属主仍可删除或替换子条目。

复现所得：本次容器的 `/` 为 UID 0、`0755`，`/tmp` 为 UID 0、`01777`，临时状态目录为 UID 1000、`0700`；直接调用 `_trusted_directory` 返回 `True`。这证明当前实现接受本次实际 Linux 路径，不能证明此前容器中失败路径的属主和权限。未放宽校验；尚需原始容器的运行命令、有效 UID、失败目录及全部祖先的 lstat 结果，才能解释此前拒绝。

## 2. numpy 的共享库

现象与复现：基线 `test_upgrade_host_cutover.py` 报 `libscipy_openblas64_-f48b354e.so` 找不到；复用其 `private_runtime` 夹具的 `test_execution_install_fence.py` 三个用例也因替换 daemon 退出而失败。

读码所得：缺失发生在 `tests/python/install/test_upgrade_host_cutover.py::HostCutoverTests.private_runtime`。它创建合成虚拟环境，然后仅按模块的 `__file__` 复制模块目录与 `.dist-info`；Linux numpy wheel 放在包目录旁的 `numpy.libs` 因此被漏掉。产品的 `install/runtime.py::_materialize_locked` 使用 `uv sync --frozen --no-dev --python 3.12` 安装完整依赖，并没有该手工复制步骤。本次证据不支持“正式产品物化漏掉共享库”的推测，也未执行实际产品安装。

改法：夹具按 `importlib.metadata.distribution(...).files` 复制 site-packages 内的完整发行包文件，保留包旁的共享库和发行包元数据；不复制清单中指向 site-packages 外的命令脚本和字节码缓存。删去了按导入目录猜测发行包内容的旧实现，同一条规则用于两个平台。修改后的 Linux `test_upgrade_host_cutover.py` 两个用例与 `test_execution_install_fence.py` 三个用例全部通过，证明私有运行时可以导入依赖并实际启动、切换 daemon。

## 3. 空 HOME 中出现 `.cache`

现象与复现：Linux 基线与修改后均有 `test_inquiry_owner.py` 的两个用例以及 `test_state_boundaries.py` 的三个子用例因空 HOME 内出现 `.cache` 而失败。容器内进一步查看得到实际目录为 `.cache/rosetta`。

定位依据：在同一个普通用户的全新 HOME 中运行 `/usr/local/bin/python3 -S -c pass`，不导入本项目、C-Two、fastdb4py 或 numpy，也会创建同样的 `.cache/rosetta`。另一探针在导入 C-Two 前安装 Python audit hook，没有捕获 `os.mkdir`，但解释器退出后该目录已经存在；容器的 `ps` 同时显示 amd64 进程通过 `/run/rosetta/rosetta` 执行。这将写入来源定位到 Docker 的 Rosetta 模拟环境，而非产品或 Python 库的默认状态选择。

处理边界：没有在测试中忽略或删除该目录，没有放宽空 HOME 断言，也没有修改 Docker Desktop 或模拟器设置。写入源头在授权的仓库范围之外；需要 Host 决定模拟环境的后续处理，再验证这两个文件。现有失败只能说明 Rosetta 写入了 HOME，不能据此认定产品选择了默认状态目录。

## 4. 下载 CPython 3.12 后失败

现象：此前输出以 `Downloading cpython-3.12.15-linux-x86_64-gnu` 开头并退出 1。该行是下载进度，单凭它无法判断下载、解包、证书、网络还是后续启动失败；本次没有原始完整 stderr。

读码所得：共享技能的 POSIX 启动器在开发模式以及 install/upgrade 入口显式选择 `--python 3.12`，产品运行时的物化命令同样指定 3.12；项目允许 `>=3.12,<3.15`，因此外层检查使用镜像中的 3.13、子命令另行获取 3.12 是现有明确选择，并非 Linux 误选了解释器。未修改解释器选择。

复现所得：普通用户执行 `uv python install 3.12`，完整输出包括 32.7 MiB 下载完成与 `Installed Python 3.12.15 in 23.18s`，退出 0；基线及修改后的 `test_local_launcher.py`、`test_packaging.py` 均通过。基线 `test_execution_install_fence.py` 在启动合成运行时时失败，修复第 2 项夹具后通过。因此尚不能为此前下载后的退出 1 确定根因，需原始完整 stderr；当前环境没有复现 CPython 下载失败。

## 5. 相等的 inode 编号

现象与复现：Linux 基线的 `test_runtime.py::RuntimeMaterializationTests.test_concurrent_installers_share_one_lock_until_ready` 失败，比较的两个 inode 编号都为 `33946373`，与此前 `33955697 == 33955697` 是同类现象。

读码所得：测试协调两个安装线程，证明等待者在原持有者删除锁文件后重新打开当前路径。产品 `installation_lock` 在持锁后比较打开文件与路径的 `(st_dev, st_ino)`，发现锁路径已消失或变化便重新打开；该逻辑发生在旧 fd 仍打开时。测试却在所有旧 fd 关闭后才比较仅存的整数编号，此时 Linux 文件系统可以立刻把已释放的 inode 编号分配给新文件，两个编号相同不等于文件对象相同。

改法：测试在第一次锁操作前，对锁路径进行一次独立的只读打开，将该 fd 保留到测试清理。旧 inode 因此仍被引用，新文件不能复用其编号；独立打开不复制持锁 fd，不会延长 flock 的寿命。原有的竞争协调、只调用一次 uv、等待者必须重开以及成功后删除锁文件等断言全部保留，产品锁代码无需修改。修改后的 Linux `test_runtime.py` 通过。

## 检查结果和后续

Linux 基线的十个文件中五个通过、五个失败；修改后的十个文件中八个通过、两个失败，检查运行器退出 1 且确认私有根已清理。通过文件为 `test_service_environment.py`、`test_transport_attach.py`、`test_local_launcher.py`、`test_distribution.py`、`test_upgrade_host_cutover.py`、`test_execution_install_fence.py`、`test_packaging.py` 和 `test_runtime.py`；仅 `test_inquiry_owner.py` 和 `test_state_boundaries.py` 保留第 3 项 Rosetta 缓存断言失败，没有出现超时。

macOS 最终同组十个文件全部通过，共 123 个测试、0 个跳过，检查运行器退出 0 且确认私有根已清理；其中 `test_runtime.py` 的锁竞争用例和两个使用私有运行时夹具的文件均通过。该结果来自最终测试实现，未以中间运行代替最终证据。原始输出留在忽略目录 `tmp/linux-portability/`，不作为公开仓库文件提交。

首次 macOS 修复验证中，测试夹具一度使用 `os.dup` 保留旧 inode；重复描述符也保留了 flock，导致锁测试阻塞。终止这一私有测试进程后，检查运行器清理了私有根；随后将其改为独立打开并重新运行同一组文件。该中间验证不计为通过证据。

容器内没有 Node.js；按任务范围，未运行 `cli/test_host_preview.py` 与 `buddy/harnesses/zcode/test_zcode_hooks_probe.py`，也没有用 Node 缺失解释本次其他文件的失败。源码使用真实克隆，`test_distribution.py` 的 Git 元数据检查通过。未启动、停止或重启日常服务，未安装产品，未访问用户 harness 配置或凭据，也未调用模型。

本次创建的容器及系统临时目录内的克隆均已删除；没有操作其他容器或镜像。检查运行器的基线、最终以及中间中断运行均报告其私有根已删除。

与要求不一致的情形：第 1、4 项的原始失败未复现，根因仍待原始证据；第 3 项已查明写入源头，但源头属于授权范围之外的模拟环境，尚未修复，因此 Linux 同组检查未全通过。没有放宽超时、权限或路径检查，没有运行完整检查。
