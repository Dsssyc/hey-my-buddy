# 等 Host 的微任务与升级

本记录对应基线 `7e6a8191`（0.34.0，schema 18）。当前交付是范围内的部分实现和接口阻塞证据，尚未完成这个微任务的全部升级验证，也不构成 Host 验收或安装事实。

## 空闲判断

`install/upgrade.py::idle_snapshot` 从全量任务中保留会自己推进的工作作为阻挡项：运行中、取消中、待对账的任务，以及排队中的普通执行、路由、辅助工作和续接工作。只有关联受管运行处在 `awaiting-host`，且没有 `recorded` 或 `queued` 续接的排队任务可以放行。辅助任务和路由任务仍各自参加全量检查，不能借父任务的 Host 边界被放行。所有执行另行检查 `execution_state` 和 `shutdown_confirmed`；任一执行没有结束或停止未确认，都以 `unresolvedAttempts` 阻挡。该判断不读取易变的 `queue_reason` 作为许可，也不取消任务。

`UPGRADE_NOT_IDLE` 保留既有 `active`（runId、state）和 `unresolvedAttempts` 结构，列出实际阻挡项。只修改查询，不改数据库形式、schema 版本、版本号或分支。

## Host 命令与回执

第一项：现有 `blackboard/service/service.py::_guard` 在 `upgrade.json` 存在时，在业务处理器之前以 `UPGRADE_IN_PROGRESS` 拒绝这九项 Host 操作；没有写下任务、请求、事件或回执。启动器也先检查该标记；本次把启动器错误消息补成明确要求升级验证或恢复结束后，使用相同命令和身份重试。新测试覆盖九项入口拒绝、数据库快照不变及启动器重试提示，但尚未走真实切换后的九项重发。

发现了冻结要求的接口阻塞：任务要求这九项在升级后都用同一个 `commandId` 重发成功，而 `workflow.accept` 和 `workflow.conclude` 的 `reject_unknown` 明确不接受 `commandId`（`blackboard/tasks/workflow.py::accept`、`conclude`）；`inquire` 的入口只接受 `inquiryId` 等字段（`blackboard/tasks/inquiry.py::observe`）；`workflow.submit` 的字段集合也没有 `commandId`（`protocol/schemas.py::WORKFLOW_SUBMIT_FIELDS`）。测试确认这四项在没有升级锁时，带 `commandId` 得到 `INVALID_ARGUMENT`，数据库仍不变。它们原本各有身份：accept 绑定运行和输出制品；conclude 根据运行、目标运行、note 和执行身份派生回执键；inquire 使用 inquiryId 和同一问题；submit 使用 requestId、同一输入及 submissionToken。没有自行把原生身份解释成冻结要求里的 commandId，也没有越界修改这些接口。

第二项：现有 `commands` 表保留命令输入指纹及响应，升级的备份、恢复与数据指纹验证包含该表。大部分回执重放仍要求有效的当前 Host 权限；takeover 专门以原代控制能力验证其回执，并要求该回执颁发的代仍为当前代。已读源码，尚未补齐真实升级前丢回应、升级后重发的测试；不能据此声称本项完成。

第三项：现有升级在 detach 后取得 daemon 与 board owner 锁，然后再检查空闲、备份和验证保留值；备份也保留 controls 等声明的持久文件，检出目录不属于被替换的运行时。新测试确认 assistance、attention 停止后可通过空闲检查，未确认停止仍被拒绝，并通过现有受管任务夹具验证这些边界不可被 Worker 继续认领。尚未在真实切换中断言等待请求、控制文件和真实 Git 检出占用保留，亦未验证切换后 decide、continue。

第四项：现有 `cli/blocking.py::_wait_until_terminal` 只读同一个运行；服务拒绝或断连后尝试重读同一 runId，失败则返回 `unavailable`、错误码和同一运行的 recovery 信息。服务升级锁也挡住这些读取，因此可能明确退出等候。已读源码，尚未补真实升级跨越 await 的测试，不声明其运行验证通过。

命令在标记写入之前进入服务的窗口由现有升级协调器处理：写标记后再查空闲，detach 取得所有权锁后又查空闲，随后才备份。提前进入的命令若产生待执行工作，升级拒绝；若已提交静态事实，其记录应随最终快照备份。这个窗口没有被当作已证实的丢命令缺陷，也没有新增安装调用层补丁。

## 验证与尚未守住的边角

新增 `tests/python/install/test_upgrade_waiting_host.py` 使用现有受管流程夹具；执行回执和工作区模块有明确替身，没有运行真实模型或原生 harness。测试覆盖 assistance、attention 空闲，普通排队和运行中的任务、排队续接、Host 边界仍有 recorded 续接、未确认停止拒绝，拒绝不取消或改写数据库，九项入口的升级拒绝，以及上述四项 commandId 参数阻塞。既有 `test_upgrade_migration.py` 继续覆盖排队和运行中的旧路由、停止未确认的路由拒绝。

测试仅经授权解释器执行检查命令：`env <清除继承的运行时、Worker、凭据与虚拟环境变量> PYTHONPATH=src <authorized-python> -m hey_my_buddy.cli.checks tests/python/install/test_upgrade_waiting_host.py tests/python/install/test_upgrade.py tests/python/install/test_upgrade_service.py tests/python/install/test_upgrade_migration.py`。检查命令为测试文件创建私有状态、运行时和主目录，未运行完整检查，未安装依赖或切换日常服务。首轮退出 1：新增测试误用了取消字段名，且错误详情新增字段破坏了旧断言；已改为实际的 `cancel_requested_at` 并保留原错误详情结构。

最终检查：上述四个测试文件全部通过，共 46 项测试，0 项跳过，检查命令退出 0，私有根目录已移除。`git diff --check` 通过。这个结果验证的是已运行测试的边界，不包含新增的真实升级跨越场景。

需要 Host 明确四个接口的原生身份是否满足本任务的重发要求；若必须提供外部 commandId，则需要扩大源码写入范围到对应工作流、inquiry 和协议字段验证，并由 Host 明确完整的身份契约。真实升级成功、升级前丢回应的重放、切换后控制/请求/检出占用保留及跨升级 await 尚未补齐。因为接口要求的根因在写入范围之外，本轮停止推进这些依赖该决定的工作。
