# ADR-025 第三、四步准备：真实 ZCode Worker 回合

2026-10-06，Host 在固定源码 `90900d7` 上直接调用共享 `worker_executor("zcode")` 的 prepare/start/collect，使用安装的 ZCode 0.16.9，配置为 `zai-api / GLM-5.3-Flash / max`。这是一次真实 Worker 角色回合，走 `hey_my_buddy.buddy.roles.run_controller` 和登记的运行模块；没有另启动黑板宏任务，因而不把本次签收写成黑板的权威验收。模型运行 1 次，耗时 41.351 秒；该项属于用户已批准的计划内冒烟。

任务是在本次私有目录读取唯一的 `smoke.txt`，确认内容 `ADR025_WORKER_SMOKE_OK`，完成检查点后调用本次完成工具。实际完成的六字段结果为 `disposition=completed`、上述短摘要、空 remaining/decisions/artifacts 与 `request=null`。RunRequest、RunResult 和持有者冻结的完整 identity 三者相等，结果由公共格式真实编码与解码。

签收来源核对通过：`receiptVerified=true`、`rootSessionMatched=true`、`toolResultSuccess=true`、`toolResultTruncated=false`，完成工具是 `buddy_finish_turn`。原生根会话为 `sess_1f69257b-1ed7-4195-90c1-278c74e63108`，回合为 `turn_b993b17c-c305-4c30-9151-6b8abdb97401`；完成调用 `call_ba2db0692d584484b3e2109f` 在 provenance 与 completionEvidence 中一致，签收编号 `5a7b9c11b6d90cc7e950e3b34969e623`。工具调用／结果序号为 54/56，回合结束序号 69；控制器观察的 turn-completed、prompt-completed、session-close 顺序为 720、721、723，settlement 为 `session-closed`。签名校验使用实际会话服务与驱动，不使用模拟收据。

普通工具事实保留 1 次 `Read`，同一调用 `call_773d38c4f1f141fcadd6725d` 有配对 start/end，类别 read，原生会话和回合与完成来源一致；`streamComplete=true`、`unsettledToolCalls=0`、`truncated=false`。完成服务调用依据已验证来源单独报告，不混入普通任务工具计数。未为了补证据再运行模型。

两层停止证据分别成立：运行结果上报 native `groupState=gone`、`started=true`、`leaderExited=true`、`exitCode=0`、`observationBasis=owned-process-group`；外层实际 ProcessHandle 的 `shutdown_confirmed()` 返回 true；共享 collect 的合并结果 `shutdown_confirmed=true`。本次正常结束，没有发出中断。原生用量报告是 partial/attempt：inputTokens 79,620（含缓存 60,672），outputTokens 712，nativeRecords 4，来源 `zcode/v4-telemetry-usage-delta`；不把 partial 改写为完整账单。

运行使用 Host 即时登记的 `<host-task-root>/m/worker-smoke/`，私有 state、runtime、cwd 与日志均位于其中，TMPDIR 与 BUDDY_CHECKS_TMPDIR 指向 `<host-task-root>/t/`。原生设置只使用项目既有私有快照机制，Host 没有读取凭据文件内容或改动登录与日常配置。现有 inquiry_paths 的短套接字回退目录由 Host 事先以本次唯一 attemptId 创建并登记，收尾只回收这一确切目录。脱敏请求、结果、摘要与探针源留在本检出被忽略的 `tmp/adr025-host/step34-20261006-105245/`；包含私有凭据的原生目录不作为证据归档。
