# ADR-010 实施接口约定

这是已接受修缮计划的实现协作输入。Host 在各独立工作树集成这些接口，最终统一发布 contract 0.8.0；开发中的命名和 DTO 不代表日常 0.6.1 契约已经升级。原计划见 [ADR-010](010-production-workflow-repair-plan.md)。

## 用户与评价

保留短时 writer gate 的 begin/renew/abort。服务端从认证请求上下文验证 writer kind：human 仅限真实 console 会话，maintenance 仅限 Host 服务调用；attempt-scoped Worker 均不得写全局表。grant 的续租、发布和撤销也检查该边界，不只检查申请时的 kind。普通 Host 不能自报 human 或借一个 human grant 发布人工数据。

用 `user_policy_publish` 和 `assessment_publish` 替换公共 `evaluation_write_publish`。前者只开放给受信任 console 路径；后者供维护 Harness 使用，HTTP console 不开放。内部共同提交逻辑可复用，不能留下可由 Host 调用的全表写入口。

人工发布除 writer 身份、commandId、expectedRevision 外，仅接收 `profileSettings: [{profileId, enabled}]`、`preferenceChanges: [{profileId, mode, reason}]`（mode 为 null 表示移除）、`annotationChanges: [{profileId, text}]`（空文本表示清除）及可选 `configuration: {decisionProfileId}`。均是补丁，遗漏保留原值。程序维护的 provider/model/effort/available/catalog 字段不能作为人工写入输入。自动评价发布只接收原有 cards 补丁，不能写上述人工字段。

人工自由意见单独存储，snapshot 增加 `annotations: [{profileId, text, revision, updatedAt}]`；自动 cards 维持证据关联与代码派生 sampleCounts。维护包可读取人工意见作为有来源的上下文，不能覆盖它。人工意见上限 4000 字符，每次变更最多 200 项；重复 profileId 拒绝，不接受派生字段。

所有具体发布字段先按调用者身份校验再提交。用户关闭不可用配置或修改另一条人工意见不依赖修正 available。已失效的旧 pin/决策配置可以提示需要处理，但不能阻止无关补丁；新启用或主动改选配置仍须当前合法。目录刷新承担程序资料的发现与更新，新增配置默认未启用，保留已有用户设置和证据。

任务输入增加有界 `routingPreferences`，格式为最多 8 条 `{match: {adapter?, provider?, model?, effort?}, reason}`，每条至少一个 match 字段。它只作用于该目标及显式继承的 helper，不写全局 preferences。软偏好候选不可用时允许合法备选，硬约束无解时请求 Host。所有完整配置选择也记录来源；Host 主动覆盖需要显式原因，不把补全四元组当成未记录的绕过入口。

## 恢复与活动

Worker 从续租响应的 uncertain 状态识别恢复需求；仍持有同一执行句柄时用原 attempt/generation/nonce/workerInstance 调用 worker_reconcile。确认恢复后清除过期 queueReason，并恢复真实执行/资源状态；有完成收据则提交原收据。不同进程实例或丢失句柄不能凭旧 PID 接管。

继续使用 `worker_progress` 的 data 字段传递 `activity`，不增加另一套调度器。activity 为受限对象：`phase`、`observedAt`、`eventSeq`、可选 `nativeSessionId`、`lastNativeActivityAt`、`lastToolActivityAt`、`toolName`、`waitingReason` 和 `counts`。phase 只允许 starting、waiting-model、streaming-model、tool-running、waiting-external、waiting-host、finishing、unknown。counts 仅允许 modelTurns/toolCalls 非负整数；未知保留 null/缺省，不捏造计数或百分比。字符串有明确上限，未知键拒绝，禁止正文、工具参数、凭据和推理内容。

按 attempt 保存有界的最新活动投影，task view 返回 activity 或 null。活动收据单调、幂等，绑定原 attempt/generation，不跨 attempt 继承；常规心跳不伪装成原生活动。原生 controller 可以通过 attempt 私有的 activity.json sidecar 交给真正的 Worker 转发，文件包含 version、taskId、attemptId、generation 和上述 payload。文件更新原子且节流；Worker 校验绑定，不把 sidecar 路径写进公开响应。公共 helper 放在 `buddy.activity`，供后续 ZCode/Codex/DSH 接入。

已有执行时限不变；结果须区分 user-cancel、deadline、harness-error、transport-error、completed 等真实终止原因。完成先于取消的合法结果不能被超时标签覆盖。进度元数据不替代原生结果和停止证据。

## 工作区生命周期

新增 workflow_scope_amend、workflow_workspace_resolve、workflow_integration_record、workspace_cleanup_plan、workspace_cleanup_apply 五个命名操作。均使用现有 Host control 能力、owner generation、commandId/expectedRevision；Worker 不获得对应权限。scope/resolve 只在相关 attempt 和 descendant 确认结束后生效。参数与持久化的最终细节由对应实现提交列明，并与契约和 CLI 一并验证。

scope amendment 接收新的 writeScope、原 scopeVersion 和原因；不改写原始提交或旧轮次权限。resolve 接收已记录的冲突身份、动作 restore/adopt/abandon、选定路径与观察到的工作区指纹；机械恢复做 compare-and-swap，不能把失败现场自动提升为授权 baseline。所有 Git 行为在事务外执行，提交前重检 owner、revision、workspace 身份与操作结果。

integration record 绑定 source artifact、目标仓库/checkout/分支、目标前后 commit/tree、策略和验证摘要；not-required 需要明确原因。accepted 必须绑定该产物的 verified integration 或 not-required，rejected 无此要求。集成证明不能只相信客户端提交的任意 target SHA，必须验证目标和固定产物关系；允许明确记录 Host 调整后的整合，并保留对应差异及验证。

cleanup plan 返回精确受管 checkout、状态、证据与保留原因；apply 必须引用未过期的该计划并重检身份/占用/结果/依赖/dirty 状态。仅删除 checkout，保留 outputs、manifest、Git 固定 refs、验收与整合收据。重复请求不重复执行。当前新 schema 无需兼容旧运行时调用；历史整理是 Host 单独验证的操作，不能扩大到其他项目或 CodeBuddy 工作树。

## 轻量 RPC

`buddy.rpc_config` 提供 configure_server()/configure_client()，分别在第一次 register/connect 前调用。用 C-Two 的公开 Python overrides 设置 Buddy 私有配置，不向原生 coding child 注入全局 C2 内存参数。首选待测 profile 为 pool_segment_size 16 MiB、max_pool_segments 2；重组池分别测试，再按最大合法 JSON 和并发控制操作确定默认值。配置输出只报告白名单参数，不声称配置容量就是 RSS。安装版 0.5.1 的 pool_enabled=False 不足以关闭所有 SHM，因此不提供虚假的 off 模式。

## 分工边界

Host 收口用户权限、人工 DTO、任务局部偏好、共有接口和版本、前端连接及最终验收。恢复 Buddy 实现 worker/store/client/activity；生命周期 Buddy 实现 workspace/workflow/对应持久化和接口；RPC/分发 Buddy 实现 rpc_config、daemon/transport 接线、打包与首次安装资料。db.py 的不同领域改动由 Host 逐段整合；Worker 不修改版本、安装源、日常数据库、全局偏好或其他分支。
