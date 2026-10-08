# ADR-025 第五步微任务 5-C3：公共实时测试迁移

本次在基线 `cda461533eca0d9e824fd79c747008ef38d2ae5c` 的独立受管检出交付三测试模块及本记录、编号表，生产源码逐字保持基线。范围内测试迁移已有实测证据，公共接线尚未验收：最终三模块共 59 项，58 通过、1 失败；额外映射验证的 38 项中，36 项 C2 单元与 1 项 stored-request 测试通过，1 项 held-handle 测试的 7 个子项失败。没有完整检查、真实模型调用、安装版 harness 启动、安装升级、用户配置或凭据操作，也未改 Git 元数据或切换分支。

本记录中的 `<task-root>` 指 Host 分配并已知的微任务材料根；原始材料位于 `<task-root>/m/`，临时 fixture 位于 `<task-root>/t/`。证据及实验均保留，Host 验收后可精确回收这个任务根；未删除或覆盖旧实验。测试现有 fixture 的 command 子进程及临时状态按原生命周期收尾。

## 实际测试边界

`test_live_channel.py` 使用真实 `CTwoLiveChannel`、已有 Wire DTO 和真实 `CTwoLiveEndpoint`。唯一 SDK 网络替身是 `cc.connect` 的上下文连接，它把三个命名 RPC 送到实际 handler；没有另写传输协议或复制旧 backend。询问由实际 owner 队列 consume，fixture 把 synthetic journal record 追加到真实本地 JSONL、flush/fsync，经保留的 `_journal_states` 发布状态，再 settle；这证明测试 owner 的本地 commit 顺序，不证明厂商 SDK、原生 journal 或 harness 已签收。其余 snapshot 用例直接发布 synthetic owner fact，事实保留、分页与选择由实际 endpoint/channel 执行。

`test_inquiry_transport.py` 通过实际 channel 的三个命名 RPC 和实际 endpoint handler 或已有 `StubPeer` 验证相关性、拒绝、坏 DTO、整帧限制及 bounded stall。owner settlement 是明确的测试模拟；native commit 证据不来自此模块。两个并发 requestId/questionId 在实际 consume 后反序 settle，关联仍匹配；旧 raw `reply.id`/header 协议随 raw 客户端退休，SDK 调用回复相关性依赖成熟 C2 合同，不按 ADR-023 第 7/8 条静态证明厂商代码。旧 closed `BRIDGE_ERRORS` 归一化、raw timeout clamp 等入口不留兼容；新 DTO 保留 bounded source error code，严格拒绝窗口范围外值。

`test_activity.py` 使用实际 `ActivityPublisher` callback，覆盖 monotone/throttle/phase-change 和 False/raising callback 不前移。Worker 用例仅替换 `role.handle_live_binding` 返回实际有界 channel，以及 SDK 网络连接；实际 `_Renewal._forward_activity`、client progress、私有 BoardStore/SQLite、nonce 与 generation fence 均执行。command fixture 只启动 `/bin/sleep`，其真实 cancel/wait 后才发布 failed result 并 retry；既不启动安装版 harness，也不将 synthetic activity 或这个 command 的停止当成原生模型完成。去重、不造 prose heartbeat、失败可重试、变代不继承与 unknown 不等于 stopped 均有断言。

## 编号审计

[编号增量表](adr025-step5-live-test-migration-ids.tsv) 只列 changed/removed/added：原 65 项，现 59 项；16 项同编号且函数 AST 相同，26 项同编号迁移，23 项删除，17 项新增，共 66 行增量。live 为 44→36，activity 为 13→15，transport 为 8→8。每个删除项单独列退休原因或具体等价编号；没有用笼统“C2 已覆盖”代替映射。保留旧编号不代表旧入口仍存在，例如 observed/status 与严格 scalar 用例已消除旧额外字段使断言提前失败的遮蔽。

完整 before/after 集合及函数 AST 比对来自指定基线与最终文件：`<task-root>/m/root-q3yv1lo3/before-ids.txt`、`after-ids.txt`、`id-audit-verified.json`。`unchanged-before-final.txt` 与 `unchanged-after-final.txt` 是分别从两边提取的相同集合，集合相等且 16 个函数 AST 相等；增量表恰好等于 changed ∪ removed ∪ added。以下为不列入增量表的全部未变化编号：

- `tests.python.buddy.harnesses.test_live_channel.CapabilityTests.test_an_unknown_delivery_mode_is_refused`
- `tests.python.buddy.harnesses.test_live_channel.CapabilityTests.test_each_harness_declares_its_existing_facilities_only`
- `tests.python.buddy.harnesses.test_live_channel.LimitTests.test_a_multibyte_question_at_the_byte_bound_is_accepted`
- `tests.python.buddy.harnesses.test_live_channel.LimitTests.test_the_live_limits_are_the_existing_inquiry_limits`
- `tests.python.buddy.harnesses.test_live_channel.ObserveSelectionTests.test_live_event_bounds_are_the_producers_character_truncations`
- `tests.python.buddy.harnesses.test_live_channel.ObserveSelectionTests.test_observation_payload_roundtrip_with_their_new_fields`
- `tests.python.buddy.harnesses.test_live_channel.ObserveTests.test_model_payload_roundtrip_on_the_actual_interface`
- `tests.python.buddy.harnesses.test_live_channel.StrictScalarTypeTests.test_the_model_boundary_refuses_int_for_bool_and_str_for_int`
- `tests.python.protocol.test_activity.ActivityProjection.test_a_heartbeat_alone_never_fabricates_native_progress`
- `tests.python.protocol.test_activity.ActivityProjection.test_activity_is_bound_to_one_generation_and_never_inherited`
- `tests.python.protocol.test_activity.ActivityProjection.test_invalid_or_tampered_activity_is_rejected`
- `tests.python.protocol.test_activity.ActivityProjection.test_progress_stores_one_monotone_idempotent_projection`
- `tests.python.protocol.test_activity.ActivityPublisherTest.test_live_publication_coalesces_without_losing_failed_or_new_phase`
- `tests.python.protocol.test_activity.ActivityValidation.test_invalid_activity_is_rejected_instead_of_stored`
- `tests.python.protocol.test_activity.ActivityValidation.test_the_whitelist_accepts_the_bounded_projection`
- `tests.python.protocol.test_activity.ActivityValidation.test_timestamp_recency_uses_utc_instant_without_rewriting_display_value`

## 聚焦验证与原始证据

使用 `uv sync --frozen --offline --no-install-project` 在新私有环境准备锁定依赖，源码由显式 PYTHONPATH 提供。最初网络下载被沙盒拒绝，复制本机只读 wheel 缓存中的依赖到新私有缓存后离线安装成功；未升级运行时。同步日志 `uv-sync.log`、`uv-sync-offline*.log`、`uv-sync-dependencies.log` 均在 `root-q3yv1lo3/`，仅最终依赖安装日志是环境就绪证据。

每个测试 subprocess 清除继承的 `BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT`、`BUDDY_RUNTIME`、`BUDDY_RUNTIME_IDENTITY`、`BUDDY_WORKER_STATE`、`BUDDY_WORKER_ID`、`BUDDY_AGENT_CREDENTIAL`、`BUDDY_AGENT_CREDENTIAL_FILE`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`；设置任务内 TMPDIR/BUDDY_CHECKS_TMPDIR、`PYTHONDONTWRITEBYTECODE=1` 与源码/测试 PYTHONPATH。Worker child fixture 另使用自身私有 environment；不读取凭据内容。命令与 exit code 保存在 `aggregate-final-result.json` 和 `mapped-unit-final-result.json`。

| 本次命令选择 | 实际结果 | 原始日志，相对 `<task-root>/m/` |
| --- | --- | --- |
| `<private-python> -m unittest -v tests.python.buddy.harnesses.test_live_channel tests.python.protocol.test_activity tests.python.protocol.test_inquiry_transport` | 59 项；58 pass，1 failure，0 errors，exit 1 | `root-q3yv1lo3/aggregate-final.log` |
| `test_c_two_live.WireFrameTests / EndpointAdmissionTests / EndpointObservationTests / ChannelUnitTests` 与两个明确 role identity 编号，完整选择见 result JSON | 38 项；37 项通过，held-handle 的 7 个 subtest failure，0 errors，exit 1 | `root-q3yv1lo3/mapped-unit-final.log` |
| `test_c_two_live.RichReplayIntegrationTests.test_owner_delivery_replay_is_a_json_value_and_keeps_the_published_state` | 1/1，exit 0 | `root-q3yv1lo3/rich-replay-final.log` |
| live 最终独立模块 | 36/36，exit 0 | `live-ka3jbhdt/module-final.log` |
| activity 独立模块 | 14/15，唯一 command-sidecar failure，exit 1 | `activity-epdjgkkc/activity-run-2.log` |
| transport 独立模块 | 8/8，exit 0 | `transport-ec3zz9vl/original-module.log` |

activity 首次自测的 fixture handle 属性错误已修复，`activity-run-1.log` 原样保留，不作为验收；live 的早期实验与日志也保留，采用下表固定 hash 的最终单点实验。所有跨进程 `test_c_two_live.SubprocessLifecycleTests` 编号仅作已存在的合同映射，本次未运行；Host 另线证据不写成本任务结果。

## 单点变异

四个实际 guard 均在新 scratch source 副本中变异，checkout 生产文件不动；固定基线 commit、原/变异 source SHA256、目标编号和完整日志。每个原件 exit 0，变异 exit 1，均为真实 assertion failure、0 import errors；仅导入失败不计为 guard 捕获。

| guard / 唯一变异 | 目标编号 | 结果与证据，相对 `<task-root>/m/` |
| --- | --- | --- |
| committed request digest 比较改为 `if False` | `test_live_channel.RequestBindingTests.test_a_changed_payload_under_a_committed_request_id_conflicts_before_the_bridge` | 原绿、变异将 changed payload 返回 queued 导致断言红；`live-ka3jbhdt/live-committed-digest-nupyto6r/{original,mutant}.log`，hash 在 `live-ka3jbhdt/mutations.json` |
| `_journal_states` 未知 state 默认 unknown→delivered | `test_live_channel.ObserveTests.test_an_unknown_journal_state_stays_unknown` | 原绿、变异 delivered != unknown 断言红；`live-ka3jbhdt/live-journal-unknown-hghm27da/{original,mutant}.log`，hash 在同一 mutations JSON |
| callback=False 分支提前插入 `_written_at = now` | `test_activity.ActivityPublisherTest.test_failed_callback_does_not_advance_recency_or_throttle` | 原绿、变异错误 throttle 跳过实际 retry，RuntimeError not raised 断言红；`activity-epdjgkkc/publisher-mutation-eplpfr44/{original,mutant}.log`，hash 在其 `manifest.json` |
| channel reply decoder 整帧上限 65536→131072 | `test_inquiry_transport.TransportTests.test_oversized_complete_reply_frames_are_refused_before_dto_decoding` | 原绿、变异把超帧 DTO 当 queued 接受导致断言红、原件复跑绿；`transport-ec3zz9vl/reply-bound-{mutant,original-after-mutant}.log`，hash 在 `reply-bound-mutation.json` |

## 公共接口缺口与退休项

最终唯一迁移模块失败为 `tests.python.protocol.test_activity.WorkerActivityForwarding.test_unextracted_command_never_reads_an_activity_sidecar`：基线 `_forward_activity` 的 unextracted 分支实际 `os.open(<attempt-dir>/activity.json)`，断言要求零次打开，观测到一次。没有 mock 整个 forward、没有 expectedFailure、没有改生产实现让它通过。Host 删除 command sidecar fallback 后必须重跑；command 继续只保留已有 DB projection，不应新增活动通道。

映射中的 `tests.python.buddy.roles.test_role_live_seam.HandleBindingTests.test_each_held_identity_component_and_harness_must_match_the_request` 对 task/attempt/generation/turn/invocation/inputSha256 以及 harness 共 7 个子项都实际返回 `(unextracted, None)` 而非 `(unavailable, None)`；基线 registered run 的公共 live 绑定尚未接通。stored-request 的逐项 6identity 测试以及 C2 endpoint 的 6identity/token/instance 实际守卫通过，不能以这些绿项声称 held-handle/public 已接通。该原有测试不在五路径写范围内，Host 公共整合后复验。

Host 指定的 `roles.live._ready_descriptor` 及 hostPid/path/instance、symlink/FIFO/超大素材保护在此基线尚不存在，因此编号表明确等待 Host 整合，未将旧 activity 文件格式留作无人调用的守卫。现有 role identity 两编号已逐项映射并披露红绿；素材守卫的最终具体编号、固定 Host source commit/hash 与实际运行证据由 Host 补入。未复制别处生产文件来冒充本检出验证。

旧 activity 懒读异常产生的 `activity-unavailable` 码随 reader 退休；新 publisher/cache 没有该同名 source failure 字段，`publish_snapshot` 也不消费 `snapshot.unavailable`。替代用例验证 owner observation reason/error 与最后 activity 缓存保留，不称错误码等价。若 Host 要继续承载该 activity 源错误码，需要其明确合同并接线；当前交付只退休旧 reader 对应的事实入口。

可供 Host 删除的仅旧 backend helper 是 `live._observe_fields`、`live._answer_state`、`live._bounded_frame`、`InquiryState._carried_facts` 及该模块旧 `_REPLY_STATE_STATUSES`。保留 `_journal_states`，其真实依赖 `_record_facts`、`_bounded_bytes`、`_bounded_word` 也须保留；C2 使用自己的实际 frame decoder/paging，不引用旧 helper。本次三模块已无 `ExistingLiveChannel`、raw `bridge_request`、activity `ActivitySidecar/read_sidecar/validate_sidecar/sidecar_path` 或这些旧 helper 的调用。范围外 `tests/python/buddy/harnesses/zcode/test_native_run.py` 仍引用 `_bounded_frame`，须由其所属微任务/Host 迁移；本任务未改它。

旧 atomic replace 断言只验证即将退休的 activity 更新文件，现注明随该文件机制退休；没有为不存在的文件保留格式，也没有捏造 usage 调用方。本回合未删除或修改 `write_json_atomic`。Host 后续检索确认没有其他生产调用方，撤回早先要求保留的假设，并随旧 activity 文件机制删除；此处只更正 Host 给出的用途说明，不改变本回合做过的代码或测试。

## 交付与 Host 接续

固定交付仅五路径：三个测试模块、本 Markdown 和增量 TSV。测试源码 hash、before/after 集合、TSV 审计、scope/生产不变检查与原始材料清单见 `<task-root>/m/root-q3yv1lo3/` `final-scope-verified.json`、`final-material-manifest-verified.json`；最终材料根为 `<task-root>`。全部 raw logs 留在该根，不另将机器绝对 home 路径写入跟踪文件。

Git 共享元数据在本 turn 仅可读且不能申请提升，未执行 add/commit；系统可以封存这五路径，Host 可只执行 Git 命令记录交付。实际公共整合仍需 Host：删除 command fallback、接通 public/role live binding、补 descriptor guard 的具体编号和固定验证、确认旧 activity read-error 事实退休边界，再对本记录所列聚焦选择复验。达到该 Host 接续边界后停止，不声称整合已完成或全部通过。
