# ADR-021 L18 R8：账户操作升级边界与夹具

2026-10-11，夹具生成与初次功能验证的执行者为 Codex Worker R8；分配输入 `5d98d5d328a5c5937b0553d44e367620e466a404`。后续服务复跑由 Codex 实现 Host 在固定提交 `b69ca676ccb2e9370feb3e3844190cb094982882` 上完成；本记录保留 Worker 初次沙盒失败与 Host 实际通过的各自证据，最终整合 Host 验收待进行。

固定基点为 `e4fbc91696f354991252ea2abddc73d45f286aa5`（0.27.0 / schema 15），源树 `2a22fa49324f1315676fbb05d79df014edb5071b`。真实运行[生成器](../../../packaging/generate-upgrade-fixture.py)，在四个样本微任务完成后调用归档基点自己的 `buddy.accounts.native_operation`，以合成 Codex native 账户和 `shutdownConfirmed=false` 产生 `account-operation:00000000000000000000000000000021`，状态为 uncertain。账户 provenance 位于 metadata 的 `accountOperation`，四个 workflow stableIds 不变；未直接向导出库插行或手改 SQL。

实际生成命令如下；`<prepared-python>` 是 Host 提供的只读解释器，完整展开命令保留在 `tmp/adr021-l18-rework/r8-fixture-command.txt`。生成目录 `tmp/adr021-l18-rework/r8-fixture-01/` 内的基点源码、私有 HOME、Git 仓库、四个工作区、原始数据库、raw.sql 和临时索引全部保留。

```sh
UV_CACHE_DIR=$PWD/tmp/adr021-l18-rework/uv-cache uv run --no-project --no-sync <prepared-python> packaging/generate-upgrade-fixture.py --python <prepared-python> --work-dir tmp/adr021-l18-rework/r8-fixture-01 --output-dir tests/fixtures/adr021-l18
```

生成退出 0；SQLite integrity 为 ok，外键违反 0，49 张表、37 个事件，四个状态仍为 delivered / accepted / awaiting-host / cancelled。[metadata](../../../tests/fixtures/adr021-l18/metadata.json) 的 108 个基点源摘要逐个与该基点 Git 对象相符，生成器摘要和所有资源绑定均相符；catalog 重新导出后字节不变。源摘要审计在 `tmp/adr021-l18-rework/r8-fixture-audit.json`，原始生成日志为 `r8-fixture-01.log`。

| 生成物 | SHA-256 |
| --- | --- |
| generator | `7c99b1f79a7ff97882646c2fe97e5cb40f35d2659cb47cc51861f59ede54f76d` |
| board.sql | `bfacb3460f46bad402558bf9efc55abfc4e85259e9ac5f60822c72c9a1ac92a8` |
| metadata.json | `62e13b68ac0e355e933d2536e7b5ba93e8f639d85018e11905753d5fa5cf4aea` |
| operations.json | `baea5c9ad062a7808a34d1a94c0ca6bb72ccffb7327f553b7667e4daa018f8e8` |
| workspace-resources.json | `233b3006b1ec78a0bd51c3a6adcd451436791ba4bbe4d12e410a0a346d82453e` |
| catalog.json | `97adfc1ebcf05769ba1447a8fdde2bbed248847b1bb3e36b1e07af5bdfa44336` |

产品改动仅在 `upgrade.idle_snapshot` 与 `store.reconcile_startup`：前者允许 uncertain account-operation 并仍拒绝 active 操作、所有 account-mutation 和未确认的任务/attempt；后者沿 Daemon 已有的启动所有权、事务和提交后通知边界，只把旧 active 操作的 status 改为 uncertain。凭据占用判断保持原实现；没有新增了结入口或调用 `native_operation_stopped`。owning 契约更新在 [operations](../../reference/operations.md)。

Worker 初次测试使用 Host 提供的 `worker-run-focused.py`，其 `checks.child_environment` 隔离状态、运行时、HOME 与凭据。入口为 `UV_CACHE_DIR=$PWD/tmp/adr021-l18-rework/uv-cache uv run --no-project --no-sync <prepared-python> <Host-focus-runner> <label> <module-or-test-id> ...`；各标签的 `.json` 保存准确测试选择与私有根，`.log` 保存完整 unittest 输出。按同一夹具的实际依赖运行以下模块，没有运行完整检查。

| 标签 | 实际选择与功能结果 |
| --- | --- |
| r8-baseline-core | store.account_operation_recovery、store.upgrade_fixture、install.upgrade_migration：31 项中 27 通过；4 项新增构造数据未提交的测试失败，已修其连接事务 |
| r8-baseline-corrected | 上述 4 个失败 ID：4/4 通过，子进程退出 0 |
| r8-baseline-dependent | store.migrations、store.upgrade_column_order、install.backup，以及 install.upgrade 的非空闲/异 schema 两项：39/39 通过，子进程退出 0 |
| r8-post-mutations-green | 5 个新恢复用例、1 个新夹具用例、5 个新升级用例：11/11 通过，子进程退出 0 |
| r8-baseline-service | Codex Worker 初次运行 service.account_operation_recovery、install.upgrade_service：2 项均在真实 Daemon 启动时因 `CONSOLE_PORT_IN_USE` 未通过；socket 探针确认 `PermissionError` / errno 1 |
| r8-host-service | Codex 实现 Host 在上述固定提交复跑 blackboard.service.test_account_operation_recovery、install.test_upgrade_service：unittest 2/2 通过，用时 10.030 秒；包装器退出 0，用时 10.6975 秒 |

最终相关源码上 70 个不重复的非服务功能 ID 已通过，涵盖真实夹具备份、15→16、身份换算、保留值验证及拒绝凭据更换的实际 admission 边界；该保留链用例仅替代运行时检查调用，未 mock 整条 upgrade。13 个独立变异均使指定功能用例转红，源码已逐字还原，相关 11 项复绿；[精简矩阵](l18-r8-mutations.tsv)与忽略目录中的 `r8-mutations.py`、`r8-mutation-sources/`、`r8-mutation-results.json`、逐项 `r8-mut-m*.log/json` 可审查。初次残留 `ps` 枚举在 Worker 沙盒被拒绝，所有 focused 包装命令因此退出 1；当时已观察的 daemon/supervisor 锁为空，进程残留状态未知，未把功能子进程退出 0 表述为完整清理成功。后续 Host 的实际清理观察记录如下。

相对本项输入新增 12 个 test ID，消失 0，改写 1；这 12 个本项新增 ID 在 `8d5a5888`、`df9318f3` 中也不存在。准确 AST 差集在 `tmp/adr021-l18-rework/r8-test-id-diff.json`，没有重新清点宏任务其他项的累计编号。新增编号如下。

```text
blackboard.service.test_account_operation_recovery.AccountOperationServiceRecoveryTests.test_r8_real_daemon_restart_recovers_previous_active_and_keeps_current_active
blackboard.store.test_account_operation_recovery.AccountOperationRecoveryTests.test_r8_account_only_recovery_notifies_after_commit
blackboard.store.test_account_operation_recovery.AccountOperationRecoveryTests.test_r8_account_recovery_rolls_back_with_existing_startup_transaction
blackboard.store.test_account_operation_recovery.AccountOperationRecoveryTests.test_r8_current_service_operation_survives_stable_reads_and_lightweight_opens
blackboard.store.test_account_operation_recovery.AccountOperationRecoveryTests.test_r8_startup_changes_only_active_operation_status
blackboard.store.test_account_operation_recovery.AccountOperationRecoveryTests.test_r8_startup_preserves_original_uncertain_and_all_mutations_raw
blackboard.store.test_upgrade_fixture.UpgradeFixtureTests.test_r8_base_native_operation_provenance_and_raw_uncertainty_are_restored
install.test_upgrade_migration.UpgradeMigrationTests.test_r8_active_base_mutation_is_not_idle_and_is_not_rewritten
install.test_upgrade_migration.UpgradeMigrationTests.test_r8_active_base_operation_is_not_idle_and_is_not_rewritten
install.test_upgrade_migration.UpgradeMigrationTests.test_r8_uncertain_base_mutation_is_not_idle_and_is_not_rewritten
install.test_upgrade_migration.UpgradeMigrationTests.test_r8_uncertain_base_operation_survives_backup_migration_identity_and_verification
install.test_upgrade_migration.UpgradeMigrationTests.test_r8_upgraded_uncertain_operation_refuses_credentials_before_native_action
```

改写 ID 为 `install.test_upgrade_service.UpgradeServiceTests.test_real_private_service_reads_the_fixed_upgrade_and_uses_public_host_conclusions`，增加启动与 delivered 结论后的原 JSON 保留断言。四个微任务 stableIds 没有新增、消失或改写；单独新增的账户操作 ID 如上。六份历史固定输出与输入提交逐字相同，没有文件删除或退休清单。

Codex 实现 Host 在固定提交 `b69ca676ccb2e9370feb3e3844190cb094982882` 的私有归档 `<Host-checkout>/tmp/adr021-l18-rework/candidate-r8` 完成两个正式服务模块的复跑。真实 Daemon 启停及再启、真实 C-Two RPC 均通过：旧 active 操作只改 status 为 uncertain，原 uncertain 操作和 account-mutation 的原 JSON 保留；当前 active 操作不被稳定读取或轻量 Database 打开改变；15→16 及身份换算后的 delivered 结论成功且幂等。

Host 原始证据为 `<Host-checkout>/tmp/adr021-l18-rework/r8-host-service.json` 及同名 `.log`，私有根为 `<Host-checkout>/tmp/adr021-l18-rework/buddy-checks-6xblv0z3`，子进程日志保留在该根的 `suite/tmp/{r8-account-recovery-service,adr021-l18-service}.log`。包装器记录的 `drainErrors`、`processes`、`observationErrors` 和 daemon/supervisor locks 均为空。Host 另观察 Worker 保留的 18 个既有私有根，各根的进程、观察错误和 daemon/supervisor locks 也均为空；证据为 `<Host-checkout>/tmp/adr021-l18-rework/host-worker-observation-r8.json`，其中绑定上述固定输出提交并核对 15 个产物文件。

原 Worker 交回的正式复跑命令（占位形式）如下，精确展开版本保留在 `tmp/adr021-l18-rework/r8-host-service.sh`。本轮仅根据上述 Host 证据修正文档，没有重新执行测试、变异或生成器；没有新增、消失或改写 test ID。服务复跑与清理观察已完成，最终整合 Host 验收仍待进行。

```sh
UV_CACHE_DIR=$PWD/tmp/adr021-l18-rework/uv-cache uv run --no-project --no-sync <prepared-python> <Host-focus-runner> r8-host-service blackboard.service.test_account_operation_recovery install.test_upgrade_service
```
