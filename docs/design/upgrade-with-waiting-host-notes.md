# 等 Host 的微任务与升级

本变更基于 `7e6a8191`（0.34.0，schema 18）。修改升级空闲查询和启动器的重试提示，保留存储形式、schema 和各命令已有的身份契约。以下记录源码检查与私有测试结果，Host 验收和日常安装另行处理。

## 空闲判断

`install/upgrade.py::idle_snapshot` 从全量任务中保留会自己推进的工作作为阻挡项：运行中、取消中、待对账的任务，以及排队中的普通执行、路由、辅助工作和续接工作。只有关联受管运行处在 `awaiting-host`，且没有 `recorded` 或 `queued` 续接的排队任务可以放行。辅助任务和路由任务各自参加全量检查。所有执行另行检查 `execution_state` 和 `shutdown_confirmed`；任一执行没有结束或停止未确认，都以 `unresolvedAttempts` 阻挡。判断依据持久运行与续接事实，忽略易变的 `queue_reason`，不取消任务。

`UPGRADE_NOT_IDLE` 保留既有 `active`（runId、state）和 `unresolvedAttempts` 结构，列出实际阻挡项。`test_upgrade_waiting_host.py` 覆盖 assistance、attention 边界放行，可认领的排队工作、运行中的执行、排队续接、Host 边界仍有 recorded 续接、停止未确认拒绝，并比较数据库快照以确认没有取消或改写。`test_upgrade_migration.py` 保留排队、运行中和停止未确认的路由拒绝断言。

## Host 命令与升级交接

升级期间的拒绝由现有 `blackboard/service/service.py::_guard` 统一执行：`upgrade.json` 存在时，九个 Host 入口在业务处理器之前返回 `UPGRADE_IN_PROGRESS`，消息要求验证结束后重试。启动器先检查同一标记，本次补充其错误消息，明确使用相同命令和身份重试。九项入口测试使用各自的参数形状，并在每次拒绝后比较数据库快照，确认任务、请求、事件和回执没有被写下。

重发沿用原生身份：decide、continue、takeover、cancel、scope-amend 使用 commandId；submit 使用 requestId、同一输入和 submissionToken；inquire 使用 inquiryId 和同一问题；accept 绑定运行和输出制品；conclude 使用运行、目标运行、说明和执行身份派生的回执键。没有新增字段。真实升级测试在写入 preflight 标记后，以同一个 cancel commandId 和同一个 submit requestId/submissionToken 发请求，确认拒绝和数据库不变；升级完成后重发成功，再次重发恢复原回执或原运行，提交记录只有一份。

升级前生效而回应丢失的代表场景使用 scope-amend：真实 RPC 已提交范围修订，将返回值仅留作测试对照；升级后带相同 commandId、原预期 revision 和原参数重发，得到原响应（duplicate 为 true）。数据库只有一个该 commandId 的回执、一条范围修订事件，以及初始范围加修订后的两个范围版本。重复请求没有再次生效。现有 commands 表、输入指纹与回执查询足以处理这个场景，无须新增安装层重放机制。

Host 边界保留测试使用真实 Git worktree 和真实控制文件。升级前有两个已确认停止、处在 awaiting-host 的受管微任务，主任务的求助请求仍未结；升级成功且保留值指纹验证通过。升级后主任务状态、完整 activeRequest 视图、revision、ownerGeneration 都与升级前相同，控制文件字节和 0600 权限不变，读取出的控制能力相同，检出仍存在且 HEAD 不变，workspace_reservations 各行也相同。随后用保存的控制能力执行 decide，再执行 continue，确认请求正常解决并排入一个续接。

跨升级 await 的现状是明确退出并保留恢复身份。测试在执行仍活动时启动真实 await，观察服务的 wait_capacity.admitted 为 1，证明服务已接纳等待；执行随后以 assistance 停在 Host 边界。测试延迟交付真实等待回应，直到升级的 preflight 标记已写入，再让 await 读取同一运行。后续读取被升级锁拒绝，await 返回 unavailable、UPGRADE_IN_PROGRESS 和同一 runId 的 recovery 信息，其中明确给出重新 await 的动作。没有无声挂住，也没有提交、续接或取消任务。

标记写入前已进入处理器的命令仍由现有协调流程交接：写标记后重新检查空闲，detach 取得 daemon 和 board owner 锁后又检查，随后才备份。已提交的持久事实纳入最终快照；产生可执行工作的命令使升级被拒绝。这个窗口的源码检查没有发现需要在安装调用处新增补丁的丢命令根因。

## 验证边界

`test_upgrade_host_cutover.py` 准备两个不同内容身份的私有运行时，使用各自的真实解释器和服务进程，实际走 probe、journal、detach、所有权锁交接、备份及验证、start、保留值校验和 active-runtime 发布。运行时准备使用 stdlib venv（不带 pip）并复制指定检查解释器已有的依赖及包元数据；runtime.materialize 被替换为返回已准备的私有运行时，避免安装依赖。原生发现复用现有离线 catalog 服务夹具，自动 WorkerPool 启动和补位关闭，并断言夹具转换的入口存在。受管执行使用合成的、通过正式 provenance 验证的停止回执；Git、服务 RPC、数据库、控制文件和升级流程均使用真实实现。

最终检查只运行五个文件：`test_upgrade_host_cutover.py`、`test_upgrade_waiting_host.py`、`test_upgrade.py`、`test_upgrade_service.py`、`test_upgrade_migration.py`。命令为 `env <清除继承的运行时、Worker、凭据与虚拟环境变量> PYTHONPATH=src <authorized-python> -m hey_my_buddy.cli.checks <上述五个 tests/python/install 文件>`。全部 46 项测试通过，0 项跳过，检查退出 0，私有根目录已移除；`git diff --check` 通过。

真实交接测试覆盖同一 contract 0.34.0、schema 18 的两个运行时内容代；既有迁移文件另行验证 schema 17 的备份、迁移和恢复。验证在本机 macOS 的私有环境中完成，自动 supervisor 的运行与退休、实际原生模型执行、Windows、断电恢复和日常安装不属于这份测试证据。回执重放仍遵守当前 Host 控制权限和 owner fencing；takeover 的原代能力重放规则沿用既有实现。
