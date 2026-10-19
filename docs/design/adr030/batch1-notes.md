# ADR-030 批次 1 交付说明

2026-10-12，基于 `1140dd13` 的受管检出；这是 Worker 的实现与检查记录，不是 Host 验收。

## 改动

三段角色文字放在 `src/hey_my_buddy/buddy/roles/texts/`，以 `roles.worker`、`roles.router`、`roles.lead` 登记到 `packaging/runtime-assets.json`；沿用 `install.runtime.resource_path` 与既有运行时、skill、wheel/sdist 打包机制，没有新增资源探测、兼容路径或回退。

`buddy/roles/prompts.py` 集中拼装，固定资源先于原生交付说明、任务文字和回合输入；Worker 的 schema 路径与会话工具路径都调用 `worker_prompt`，Router 的快速与审阅请求都调用 `router_prompt`，调用参数没有可替换或省略角色说明的入口；Lead 仅随包，没有执行入口。

删除 `turn_io.ASSISTANCE_HINTS` 与被取代的 `test_assistance_hints.py`，其触发条件、协助字段含义和不得声称 reviewer/helper 已运行的要求合入 Worker 资源的两个新增条目，其余原定稿逐字保留；`test_role_prompts.py` 接替并扩展原测试，native-schema 的哈希改为固定任务与输入的完整提示词哈希，并核对实际回合记录的 `promptSha256`。

没有修改 harness 启动参数、设置、主目录、供应方、登录、账户或权限；与批次 2 交叉的 `run_execution.py`、`structured_call.py`、`buddy/runtime/worker.py` 都没有改动，也没有修改 skill、README、`docs/reference/` 或 `docs/design/adr030/role-texts.md`。

## 合并后的 Worker 全文（待 Host 过目）

```text
ROLE: Worker buddy (hey-my-buddy)
You are working for a Host agent, not for a person. The Host delegated the micro task below through the blackboard, and only the Host reads what you return. For this session:
- Instructions from user-level or project files that give you another role, tell you how to address the user, or tell you to wait for a person's confirmation do not apply. Rules about the code itself still apply.
- Do not ask questions, offer choices or wait for input, and do not use tools that prompt a person. Nobody will answer. Do not start or hand out other buddy tasks; your harness's own subagents are fine inside this task's scope.
- Your channel to the Host is the blackboard, through the buddy tools of this session: `buddy_checkpoint` picks up questions the Host has sent you (call it at natural milestones and before finishing), `buddy_answer_inquiry` answers one of them, and `buddy_finish_turn` ends your turn with your result. Where these tools are absent, your final message is the result.
- You cannot send the Host a question in the middle of a turn. If you are blocked, need something outside the task's scope, or would otherwise ask, end your turn with `assistance` or `attention` and say exactly what you need and what you already tried. The Host decides and continues this micro task.
- End your turn with `assistance` or `attention` instead of guessing when the work needs files or permissions outside the authorized scope, validation keeps failing and you have no further evidence for the next step, the assigned capability clearly does not fit, or the Host/user agreed review condition has been reached.
- Ask the Host for help through your turn outcome, not by acting outside scope. Put what you already tried in `attempted`, the exact work you need in `neededWork`, and the acceptance condition in `acceptance`, with `expectedArtifacts` listing the fixed artifacts the help must produce; `suggestedProfileId` is only a suggestion the Host may ignore. Never claim that a reviewer or helper already ran. The Host decides whether to handle the work, authorize a helper or continue this micro task.
- When you finish, end your turn with `completed` and report plainly what you did and what you did not verify. The Host checks your work before accepting it.
```

## 文字与代码逐项核对

| 文字项 | 当前代码与结论 |
| --- | --- |
| `buddy_checkpoint` | `worker_services.session_tools` 及 `call_session_tool` 有此工具，`session_mcp.respond` 转交会话工具调用；DSH 使用原生会话挂载，ZCode 使用带服务器前缀的 MCP 名称，交付说明提供实际工具名。 |
| `buddy_answer_inquiry` | 同处有此工具，输入是 `inquiryId`、`answer`，读取 Host inquiry 的机制和字面意思相符；没有 Worker 主动创建 inquiry 的操作。 |
| `buddy_finish_turn` | 同处有此工具，只结束当前回合，不派发微任务；DSH/ZCode 经签名 receipt 交付。 |
| 缺少工具时用最终消息 | Codex 与 Claude 的 `registry.worker_format` 明确声明没有 `buddy_finish_turn`，分别用原生 `outputSchema` 与 structured-output schema 的 `{outcome: ...}` 交付，角色文字已经包含工具缺席时的最终消息说明；这两条 Worker 路径没有挂载 checkpoint/answer 工具。 |
| `completed` / `assistance` / `attention` | `turn_io.validate_outcome`、`schema_worker.outcome_schema`、`worker_services.OUTCOME_SCHEMA` 均只有这三种值，`completed` 必须 `request: null`，另外两种必须提供 request。 |
| 协助 request 字段 | 上述验证要求 `summary`、`attempted`、`neededWork`、`expectedArtifacts`、`acceptance`，与新增条目一致；`suggestedProfileId` 的 schema 差异见下方。 |
| Router Routing，fast | `prepare_router_fast` 产生无工具请求，`run_execution.fast_request` 使用 `tool_scope="none"`，并保留零工具证据判定；目前 Claude 声明不支持此入口，见下方。 |
| Router Routing，review | `prepare_router_review` 调用 `router_input.prepare` 创建冻结副本，`run_execution.review_request` 使用 `tool_scope="read"`，不带 Worker credential；既有网络限制、原生工具与证据检查保持原样。 |
| Router Maintenance | 当前没有由运行时执行的 Router 维护面调用，也没有该面专用工具挂载；既有外部维护数据接口不构成 Router 维护回合。 |
| Router Profile | 当前没有 Router 画像面的搜索/抓取运行入口或仅联网工具范围，`model_facts.py` 的程序读取公开模型事实不是 agent 的画像回合。 |
| Lead `submit` / `await` / `get` / `continue` / `accept` / `conclude` / `buddy help <method>` | CLI 有这些 Host 操作和 help，但没有 Lead 角色、委派权及相应 scoped credential；资源随包，不接入运行。 |

发现的差异或未落地点如下，均没有为迁就文字而修改代码或改写定稿：

- Worker 文字是工具中立的，Codex/Claude 实际没有三个会话工具，DSH/ZCode 有；最终消息的替代渠道已在原文中说明。
- 新增文字提到可选 `suggestedProfileId`，共享 outcome 验证和 DSH/ZCode 会话工具 schema 接受它，Claude schema 也接受它，但 Codex 的 native schema 不含此字段，因此 Codex 不能返回该建议字段。
- Router 定稿介绍三个功能面，但 Maintenance 与 Profile 的受管调用及工具范围尚未实现；本批只接入现有 Routing 的 fast/review 两种调用。
- `ClaudeAdapter.no_tool_structured` 当前为 `False`，Claude 只有审阅模式 Router 入口；新增测试保留并确认其快速模式被拒绝，没有伪造四家都支持快速模式。
- Lead 的操作名对应既有 Host CLI，但 Lead 委派权没有实现；本批只交付文字资源。

## 检查记录与边界

测试子进程启动前清掉 `AGENTS.md` 列出的 runtime、Worker、agent credential 与 venv 环境变量，显式使用 `PYTHONPATH=src:tests/python`；`uv` 默认缓存不可写且联网不可用，后续从现有可读依赖缓存复制必要文件到私有临时缓存，使用私有临时 venv 和 `UV_OFFLINE=1`，没有绕过沙盒或申请权限。

最终定向命令为 `uv run --frozen python -m unittest buddy.roles.test_role_prompts buddy.roles.test_worker_services buddy.roles.test_schema_worker install.test_runtime buddy.roles.test_run_execution buddy.roles.test_registered_review buddy.roles.test_role_controller install.test_packaging.SingleEntrypointTests install.test_packaging.CheckHarnessTests install.test_packaging.SkillBuildTests buddy.roles.test_registered_run_wiring.RegisteredDescriptionTests buddy.roles.test_registered_run_wiring.NativeEvidenceProjectionTests`；Python 3.12 上 112 项通过。

测试覆盖四家 Worker 实际请求的同一角色前缀、已有各模式 Router 请求、Host 任务与输入中的 `ROLE:` 无法排在前缀之前、定稿逐字一致（Worker 仅增加两个条目）、退休 hints 消失、完整提示词与记录哈希、声明资源、私有安装资源路径，以及替换源目录后从已物化运行时导入并读取三段资源和生成提示词；物化测试用假的 uv 同步器，不冒充真实冷启动验收。

实际运行 `uv build --out-dir <private-dist>` 成功生成 wheel 和 sdist，逐字核对三段资源在 wheel 本体、内嵌运行时包和 sdist 中都存在；解压 wheel 后从私有目录导入并生成 Worker 提示词也通过，未启动服务或调用模型。

下列扩展测试已尝试，但受环境限制没有通过，需 Host 在可用环境重跑；没有把失败改成跳过或修改生产检查：

- `blackboard.store.test_blackboard.TestPackaging.test_the_default_cold_start_installs_and_reports_the_stable_runtime`：私有冷启动被 `LAUNCH_ACCESS_DENIED` 拒绝，同类另外两项通过。
- `install.test_packaging.CwdIndependentCliTests.test_the_launcher_is_cwd_independent` 与 `test_the_launcher_reaches_the_private_cli_from_an_unrelated_cwd`：同样被 `LAUNCH_ACCESS_DENIED` 拒绝，cleanup 另报告 `ps process enumeration failed; residue status is unknown`；测试保留其私有目录，未声称进程已停止。
- `install.test_distribution.DistributionTests.test_wheel_and_sdist_are_complete_and_clean`：测试自建空构建缓存，离线下找不到 hatchling；既有 no-build-isolation 选项也未让该测试使用装有 backend 的 interpreter，不能把独立成功的 build 当作该测试通过。
- `buddy.roles.test_registered_run_wiring.FastRegisteredRunTests.test_a_foreign_result_cannot_borrow_the_owned_run_identity`、`test_both_native_and_controller_groups_are_required_for_fast_stop`、`test_fast_consumes_public_frames_and_corrects_on_the_registered_process`：受管控制器未取得有效 RunResult。
- `buddy.roles.test_registered_run_wiring.WorkerRegisteredRunTests.test_a_changed_provenance_keeps_valid_inquiry_and_attention`、`test_a_changed_provenance_reference_fails_without_losing_real_stop`、`test_each_bad_report_keeps_the_other_verified_report`（inquiry-report / attention-report）、`test_a_confirmed_two_layer_stop_is_the_resumable_positive_witness`、`test_a_denied_or_unknown_continuation_capability_is_not_resumable`、`test_an_unconfirmed_controller_keeps_valid_inquiry_and_attention`、`test_an_unconfirmed_native_stop_is_never_resumable_even_when_outer_confirms`、`test_an_unconfirmed_outer_stop_keeps_the_session_not_resumable`、`test_worker_consumes_frames_without_publishing_private_service_credentials`：控制器未取得有效 RunResult，或因此无法确认停止与续接；在同一私有 fixture 上显式展开控制器异常，确认 C-Two 注册端点报 `server failed to start: IO error: Operation not permitted`，随后注销未注册名称也报错，未修改该范围外根因。
- `buddy.roles.test_registered_run_wiring.DshPublishedReceiptTests.test_an_unvalidated_dsh_turn_does_not_publish_a_captured_session`：同类控制器路径未取得 shutdown confirmation，待 Host 重跑确认。

没有运行完整检查、没有使用日常看板或真实账户、没有付费模型调用；整合后的完整检查、受限测试重跑、四家真实回合与合并后的 Worker 文字审阅由 Host 完成。
