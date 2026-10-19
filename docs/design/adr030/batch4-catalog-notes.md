# ADR-030 批次 4：模型目录读取状态传递

基于 `3a4a9637`（0.30.0）的受管检出，2026-10-13 完成 Worker 范围内修改与测试；本记录不代表 Host 已验收。依据 ADR-027，只有读取状态为 `complete` 且账户状态已确认或不适用的观察才能替换目录；ADR-030 第 7 条要求把无法识别的原生形状如实记成事实。

## 修改

`src/hey_my_buddy/blackboard/catalog/catalog.py` 的 `discover`：聚合本 harness 的观察时使用它自己的 `status`，`reason` 值不是 `None` 时一并传递，继续保留原有 `accountStatus`。原来的硬编码 `complete` 会把部分目录送进可信读取路径，丢掉原因；这里删除了该覆盖，并把只描述账户状态的旧注释改成说明读取状态、原因与账户事实的传递。

`src/hey_my_buddy/blackboard/service/service.py` 的 `_refresh_harness_catalog`：送入 `catalog_store.record` 的本 harness 观察同样使用原生 `status` 并按存在的值传递 `reason`。删除了这里的硬编码 `complete`；健康版本围栏、观察身份、运行绑定、账户字段和既有错误处理沿用原路径。

两个消费方都继续按 adapter 名选择本 harness 的观察；找不到时仍送出 `status: complete` 与 `accountStatus: unknown`，不会借用其他 harness 的观察。供应方、模型、来源、版本与警告的处理没有变动；既有 `canonical_payload` 仍会在归一化结果里把缺省原因写成 `reason: None`。没有删除文件，也没有改 harness、`catalog_store.py` 或存储形式。

## 对应测试

- `blackboard.catalog.test_catalog.CatalogTests.test_discovery_preserves_each_harness_status_and_reason`：四个合成 harness 同时聚合，覆盖 `unknown`、`complete`、有原因与无原因，并检查供应方身份、来源、版本和警告。
- `blackboard.catalog.test_catalog.CatalogTests.test_discovery_without_own_observation_keeps_complete_and_unknown_account`：本 harness 没有观察时保留原有处理。
- `blackboard.catalog.test_catalog_trust.CatalogTrustTests.test_unknown_discovery_retains_catalog_without_confirming_model_absence`：先确认含 `alpha`、`beta` 的目录，再把账户已确认但状态为 `unknown` 的部分目录经 `catalog.discover` 和真实 `catalog_store.record` 写入；在超过确认窗口后再次读取，确认原 discovery 绑定、读取时间、模型可用性和 revision 不变，不发布额外模型，不产生待确认或缺席事件。
- `blackboard.service.test_service.CatalogRefreshServiceTests.test_refresh_preserves_native_status_and_optional_reason`：刷新路径原样传递状态、账户事实和可选原因；用 `wraps` 观察真实 `catalog_store.record` 调用，并检查实际存储状态，确认无原因时不在传入观察里补原因字段。
- `blackboard.service.test_service.CatalogRefreshServiceTests.test_unknown_refresh_retains_catalog_without_confirming_model_absence`：先经服务刷新确认目录，再经同一路径两次写入未知的部分目录，第二次跨过确认窗口；断言目录绑定、读取时间和完整模型行保留，没有待确认状态或模型状态事件。
- `blackboard.service.test_service.CatalogRefreshServiceTests.test_complete_refresh_updates_catalog_and_confirms_model_absence`：完整读取立即发布新增模型，缺席模型先待确认，再于窗口之后记为确认缺席；读取时间随完整读取更新。
- `blackboard.service.test_service.CatalogRefreshServiceTests.test_refresh_without_own_observation_keeps_complete_and_unknown_account`：其他 harness 的观察不能代替本 harness；原有缺省观察仍由存储层记为账户状态未知，不发布模型。

原有 `test_confirmed_account_fact_publishes_the_catalog` 与 `test_missing_or_unknown_account_fact_replaces_nothing` 保留；目录信任测试文件中的完整读取、消失确认、重现恢复、effort 变化与乱序观察测试也保留并运行。

## Worker 验证

先写回归测试再修改消费方：旧聚合路径在状态与原因传递、真实存储保留目录两项上失败；旧服务路径在对应两项上失败。修复后，三个修改过的测试文件完整运行，共 38 项通过（1.656 秒）；相邻的目录观察与 harness 服务测试文件共 26 项通过（2.047 秒）。总计 64 项通过。

使用 Host 指定的只读 Python 解释器，源码路径为 `PYTHONPATH=src:tests/python`，禁用字节码写入；先清除 `AGENTS.md` 列出的继承变量，再用现有 `hey_my_buddy.cli.checks.child_environment` 为 unittest 子进程建立临时 HOME、harness 配置路径与运行根。没有安装依赖或修改该解释器环境。实际执行的模块为：

```text
python -m unittest blackboard.catalog.test_catalog blackboard.catalog.test_catalog_trust blackboard.service.test_service
python -m unittest blackboard.catalog.test_catalog_observations blackboard.service.test_harness_service
```

## 未运行与 Host 验收边界

修改过的三个测试文件均完整通过，没有未运行的测试项。完整 `hey_my_buddy.cli.checks`、真实 harness 回合与故障注入按任务约定交由 Host 运行；本批未启动服务或 Worker，未使用日常看板、用户 harness 配置、账户凭据或付费模型调用。前端没有变更，因此未运行前端检查。

原生控制器与停止证据的测试文件 `blackboard.catalog.test_native_discovery_stop` 未运行，属于完整检查与故障注入的 Host 验证范围；其中需要实际控制器的 `CatalogNativeStopTests` 方法如下，Worker 未尝试绕过套接字、共享内存或启动器限制：

- `test_controller_log_emfile_without_spawn_preserves_stop_evidence`
- `test_controller_preparation_failure_preserves_stop_evidence`
- `test_controller_partial_log_open_failure_closes_fd_and_preserves_stop_evidence`
- `test_controller_created_fd_failure_without_native_receipt_preserves_unconfirmed_stop`
- `test_controller_native_false_after_outer_stop_keeps_native_child_alive`
- `test_controller_invalid_native_receipt_after_outer_stop_preserves_unconfirmed_stop`
- `test_controller_native_true_and_outer_stop_preserve_stop_evidence`
- `test_controller_native_false_after_normal_exit_preserves_unconfirmed_stop`
- `test_controller_native_true_after_normal_exit_preserve_stop_evidence`
- `test_controller_created_fd_failure_with_unknown_cleanup_preserves_unconfirmed_stop`
