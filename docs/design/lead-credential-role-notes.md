# Lead 凭据与角色存储

本次实现基于 `50521499`，产品版本与协议版本改为 0.37.0，当前 schema 改为 21；升级基点继续为 `6b385e53` 的 0.36.0、schema 20，未改基点文件或样板。

Worker 在私有凭据文件里写入执行令牌、执行身份和绝对 `stateDir`，Lead 的角色说明只给出该文件路径，并要求每次调用启动器时显式设置 `HMB_AGENT_CREDENTIAL_FILE`。启动器在选择运行时前读取文件里的看板目录，CLI 也使用同一目录，因此命令不需要继承状态目录或运行时环境；令牌没有写入提示词或回合输入。凭据文件继续按私有文件发布与读取，缺失或无效文件直接拒绝，不改用 Host 权限。

删除 `runtime_selection.worker_launcher_environment`、`HMB_LEAD_LAUNCHER` 与 Codex CLI、Claude Code CLI、ZCode CLI、DSH CLI 原生进程的四处环境合并。可信 Python 控制器仍拿到执行凭据来提供本回合的服务；原生 harness 的命令从角色说明取得文件路径，没有第二条 Lead 环境传递机制。Lead 的角色说明还要求提交名下微任务时省略归属字段，由执行凭据确定关系。

名下微任务创建时 `workflow_runs.host_id` 为 NULL，管理关系只存于 `workflow_lead_tasks`；普通 Host 的名字即使带 `lead:` 也不会取得 Lead 角色。黑板先验证执行凭据及其允许访问的关系，再把执行发起者记为 Lead；没有执行凭据的控制能力持有者仍记为 Host，未命名的控制能力以其微任务标识归因。控制文件明确保存 NULL 的 `hostId`，令牌、代次与控制能力校验继续保留。

`events`、`workflow_scope_versions`、`workflow_workspace_conflicts`、`workflow_integrations`、`workspace_cleanup_plans` 与 `workflow_host_conclusions` 的发起者分别存为 `actor_role`、`actor_id`，事件载荷不再重复存发起者。公开事件与审计视图按 `{role, id}` 投影，Lead 唤醒 SQL 比较两个存储字段。取消、接管、结论、整合、范围变更、清理与自动续做的写入点同步使用结构化角色。

安装升级先创建并验证 schema 20 备份，再在离线事务里重建改变形状的表，换算旧发起者、名下归属、请求指纹及产品自己的命令回执。旧归属只从 `workflow_lead_tasks` 及其辅助微任务关系认定，未建立名下关系的 `lead:...` Host 名称保留；实际 Host 接管后的名字也保留。初代名下微任务移除合成的 Host 名称；其请求指纹按不可变微任务、原始工作区意图与已存标题重新计算。历史 manifest、native 历史、提示词哈希与模型返回的不透明内容不重写，换算失败事务回滚。

删除 `attempt_legacy_starts` 当前 DDL、开机一小时余量、`legacy_boot_evidence`、`boot_started_at`、中断恢复对存量开始时刻的读取和传递，以及只测试这条规则的两项测试。升级只要发现带存量开始时刻的执行尚未确认结束便拒绝；已结清后删除该表，并在产品已知的中断证据及回执位置移除 `legacyStartedAt`。完整进程身份、native 子进程证据、实际停止确认与普通恢复保留。

Python 验证统一使用 `PYTHONPATH=src <prepared-python> -m hey_my_buddy.cli.checks <files>`，其中 `<prepared-python>` 是 Host 指定的既有解释器，进程数使用默认值，没有安装依赖或改写该解释器环境。未跑完整检查，完整检查留给 Host 整合后、安装前执行。

角色与归属一组涵盖 `blackboard/tasks/` 下六个 Lead 文件、`test_workflow.py`、`test_workflow_cancellation.py`、`test_host_workflow.py`、`test_scope_recovery.py`、`test_storage_orphans.py`、`test_macro_tasks.py`、`test_macro_task_stop.py`、`test_macro_task_summary_cache.py`、`test_workflow_stop_surface.py`，以及 `blackboard/routing/test_router_boundary_integration.py`、`blackboard/store/test_current_core.py`、`console/test_console_version_info.py`。初批 16 个文件 330 项通过，两个旧预期的测试文件修正后重跑，2 个文件 23 项通过，退出码 0；各文件最终均已通过。另跑 `blackboard/routing/test_decision.py` 与 `test_router_failover_integration.py`，2 个文件 114 项通过，退出码 0。

凭据一组涵盖 `blackboard/tasks/test_workflow_worker.py`、四个 harness 的指定测试、`buddy/roles/test_role_prompts.py`、`buddy/harnesses/test_harness_discovery.py`、`cli/test_cli.py`、`install/test_launcher.py` 与 `test_launcher_selection.py`，10 个文件 191 项通过，退出码 0。随后把启动器集成测试加强为由夹具 harness 读取提示词，再以仅含 `HMB_AGENT_CREDENTIAL_FILE` 的环境启动命令；`test_workflow_worker.py` 最终 7 项通过。Lead 角色文字补充后，`test_role_prompts.py` 最终 5 项通过，退出码均为 0，检查器确认私有根已清理。夹具覆盖无环境继承时仍建立名下关系，也覆盖令牌不出现在提示词。

升级与中断恢复一组涵盖 `blackboard/store/test_upgrade_fixture.py`、`test_interruption_recovery.py`、`test_interruption_native_tree.py`、`test_worker_orphan_settlement.py`，以及 `install/test_upgrade.py`、`test_upgrade_archive.py`、`test_upgrade_service.py`、`test_backup.py`，最终整组 8 个文件 58 项通过，退出码 0，检查器确认私有根已清理；其中已包含旧事件类别、已结清存量回执与普通执行取消回执换算，样板文件单独复测也有 6 项通过。样板覆盖 schema 21 与新建看板结构完全相同、无关系的同名前缀 Host 保留、实际 Host 接管身份保留、未结清存量拒绝且原件字节不变、模型不透明内容与历史文件不改写。

与要求不一致的情形：控制台前端的角色消费尚未同步。`apps/console/src/task-activity.tsx` 仍从取消归因的字符串前缀解析角色，`host-workflow.ts` 只接受字符串发起者，整合显示及相关类型仍使用字符串；新结构会使部分身份显示丢失或显示为对象文本。`apps/console/` 不在本微任务写入范围内，需 Host 处理或授权补齐。`docs/decisions/030-harness-layer-scope.md` 中 2026-10-14 追加的 Lead 环境传递说明也被本次实现取代，该文件不在文档写入范围内，交由 Host 同步。未运行前端或浏览器测试，未使用真实 harness 或模型，未安装产品，也未操作日常服务与 Worker。

`git diff --check` 最终通过；本微任务没有提交、分支或标签改动，也未操作共享 stash。
