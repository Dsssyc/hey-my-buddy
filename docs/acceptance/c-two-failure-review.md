# C-Two 0.7.4：完整初跑失败的 Host 复核材料

用户决定先交 Claude Code Host 复核失败记录，扩大实施继续暂停。本记录整理已固定的初跑、后续聚焦证据与尚未定位的问题；没有提交新的修复微任务，没有重跑完整检查，也没有执行四个付费 harness 冒烟。复核结果由用户转达。

## 提交与原始证据

实施分支为 `socu/c-two-073`，检出为 `~/.codex/worktrees/c-two-073/hey-my-buddy`，没有推送。完整初跑绑定 `0dc618ed61412c97179c5b2a7c936f4c2669046d`，已合入的 core 为 `1a9decd95736a15a5ec4b4db6526e4cb48666326`；当前代码候选为 `9b73fe42a43e83245decad7f54c0912d273748dc`。后者包含 Host 自己的凭据清理异常路径修正和一处重启夹具的 `state_dir` 补传，不能用其聚焦结果替代前者的完整初跑结果。

初跑命令为 `uv run --frozen python -m hey_my_buddy.cli.checks`，使用默认并行数，退出码 1，用时 909.486 秒。203 个 Python 模块中 177 个通过、26 个失败；运行器对通过模块汇总为 2,816 个测试、跳过 1 个。3,270 是此前独立 loader 收集的编号总数，不能写成全部通过。日志在 Python 阶段失败后结束，未取得后续完整检查阶段的通过证据；`apps/console` 无改动，没有另跑前端测试。

下表中的位置都相对于 `<实施检出>/tmp/c073-host/`。原始日志与检查私有根保留，复核期间不回收；`owned-root.json` 与 `final-check-owned-root.json` 记录本次自建根的确切位置。入库记录使用占位符，原始本机位置只在被忽略的材料里。

| 材料 | SHA-256 |
| --- | --- |
| `final-check.log` | `6f2aa7086a00973f9bda27ce51de050a43f76d4d695b7ac213643946bac5456f` |
| `final-check-result.json` | `ec0bf0e598fabc6185c8c2cf985b0db9aefde594fc9604162207661a8817266a` |
| `final-first-failure-inventory.json` | `63ed252b4f39bb88dcbdef82439b9843a72616a4d2b3b17c330d5f017e48dabd` |
| `worker-restored-mutants.json` | `3f6fa0669f9c6ad1f29fecbaeeb91f1bcf5fdec9d818d00f8d34a943db423a0a` |

## 26 个失败模块逐项登记

“F/E”是 unittest 报出的 failure/error 数，包含子测试，不能与用例数直接相加。行号指固定 `final-check.log` 中该模块输出的起点。“已定位”只表示日志或源码能指出具体接口遗漏，尚未修改或取得聚焦绿灯；控制器缺结果的多项失败只登记共同症状，不推断它们有同一原因。

| 失败模块 | 初跑 F/E；日志行 | 当前证据与复核边界 |
| --- | --- | --- |
| `blackboard.evaluation.test_other_native_observations` | 10/2；360 | 原生角色失败或无结果，随后用量、quota、消息断言失败；控制器失败原因未定位。 |
| `blackboard.service.test_daemon` | 0/1；802 | 直接 RPC 调用未传私有 `state_dir`，报 `Private IPC directory is missing`；未修。 |
| `blackboard.service.test_harness_startup` | 0/4；624 | Mock 客户端的 `state_dir` 被当作路径传给 WorkerLiveRuntime，`Path(Mock)` 报 TypeError；未修。 |
| `blackboard.service.test_liveness` | 0/1；765 | request 桩不接受新增的 `state_dir` 关键字；未修。 |
| `blackboard.store.test_blackboard` | 1/1；3 | 两处已在 `9b73fe42` 修正：凭据清理的异常路径第二次机会，以及重启夹具的 `state_dir`。只重跑了 14 个 CrashWindows 用例和 1 个真实重启用例，没有重跑本文件全部 57 个用例。 |
| `blackboard.tasks.test_host_workflow_worker` | 4/0；539 | 三项得到 `shutdown.selfConfirmed=false`，另一项等待 daemon 健康失败且日志为空；未定位，不降低停止证据要求。 |
| `blackboard.tasks.test_workflow_worker` | 1/0；1003 | 原生角色返回失败，场景未完成；控制器失败原因未定位。 |
| `buddy.harnesses.claude.test_claude` | 1/0；165 | 取消用例得到 failed；空闲时独立重跑整文件 42 项通过，测试与停止判定未改。负载因素尚未证明。 |
| `buddy.harnesses.claude.test_claude_tool_evidence` | 7/0；874 | `invalid-native-result` 或 failed 出现在工具事实断言之前；控制器失败原因未定位。 |
| `buddy.harnesses.codex.test_codex_tool_evidence` | 7/0；1043 | `invalid-native-result` 或 failed 出现在工具事实断言之前；控制器失败原因未定位。 |
| `buddy.harnesses.codex.test_no_tool_codex` | 17/8；1158 | 多个子测试无有效原生结果，预期的禁止工具与期限事实未取得；控制器失败原因未定位。 |
| `buddy.harnesses.test_adapter_usage` | 4/1；1483 | 回合先失败或无结果，随后用量断言失败；控制器失败原因未定位。 |
| `buddy.harnesses.test_inquiry_owner` | 2/0；1560 | 预期 queued，实际 unavailable；父进程的私有通信域是待核对项，尚未取得实际定位证据。 |
| `buddy.harnesses.test_private_adapter_invariants` | 4/0；1618 | 原生选择或角色回合失败；需要检查各夹具的私有状态域及控制器 stderr，尚未定位。 |
| `buddy.harnesses.zcode.test_no_tool_zcode` | 10/0；1679 | 控制器无结果，模拟原生回合未收到 prompt；尚未定位。 |
| `buddy.harnesses.zcode.test_zcode` | 1/0；121 | 零期限下显式取消得到 failed；空闲时独立重跑整文件 25 项通过，测试与停止判定未改。负载因素尚未证明。 |
| `buddy.harnesses.zcode.test_zcode_checkpoint` | 2/0；2386 | 同一解释器内两个不同私有状态域并发，控制器端点未就绪；需核对 SDK 进程全局设置与夹具隔离，尚未取得修复证据。 |
| `buddy.harnesses.zcode.test_zcode_native` | 3/2；279 | 安装版 ZCode 的 app-server 在结算前关闭，报 `native-disconnected`；这是现有离线原生夹具，需独立核对运行器和本机前提，未定位。 |
| `buddy.harnesses.zcode.test_zcode_tool_evidence` | 8/0；1827 | 控制器无结果，模拟原生回合未收到 prompt；尚未定位。 |
| `buddy.roles.test_registered_run_wiring` | 3/0；1997 | 注册接线用例的原生角色失败；控制器失败原因未定位。 |
| `buddy.runtime.test_live_lifecycle_integration` | 0/1；1953 | Mock 客户端的 `state_dir` 被当作路径，报 `Path(Mock)` TypeError；未修。 |
| `cli.test_cli` | 5/0；2062 | 私有看板 transport 桩不接受 `state_dir` 关键字；未修。 |
| `cli.test_cli_views` | 6/0；2155 | request 桩不接受 `state_dir` 关键字；未修。 |
| `cli.test_host_cli` | 8/0；2268 | 私有看板 transport 桩不接受 `state_dir` 关键字；未修。 |
| `cli.test_host_preview` | 1/0；2239 | 本检出缺 console 开发依赖，前端解析器入口不存在；记录环境前提，未安装依赖或修改产品。 |
| `console.test_console` | 1/0；229 | 模型目录预期文件夹具，实际来自原生观察；夹具的继承 `BUDDY_MODEL_CATALOG_FILE` 被子进程环境隔离丢弃，需要显式传递，未修。 |

## 已取得的后续聚焦证据

`9b73fe42` 修的是 Host 在整合中引入的清理回归：原来的第一次凭据清理仍可抛入执行异常分支，异常分支保留两层停止事实并给第二次清理机会；端点回收在绑定材料删除之前，只调用一次，finally 只作端点收尾。没有把 unknown 改成 stopped，也没有重试删除 busy、stale 或 unverifiable 的目标。这是 Host 公共整合归属，先前错误与初跑失败仍保留在历史记录中。

| 证据文件前缀 | 实际范围 | 退出码；测试耗时 / 命令耗时 |
| --- | --- | --- |
| `host-restore-worker-cleanup` | `TestCrashWindows` 14 项 | 0；31.961 / 32.388 秒 |
| `host-private-restart` | 真实 daemon 重启并重新连接的 1 项 | 0；15.171 / 15.456 秒 |
| `host-restore-endpoint-chain` | 私有无模型端到端 2 项 | 0；10.929 / 11.209 秒 |
| `final-first-isolated-zcode` | `buddy.harnesses.zcode.test_zcode` 全文件 25 项 | 0；30.499 / 30.822 秒 |
| `final-first-isolated-claude` | `buddy.harnesses.claude.test_claude` 全文件 42 项 | 0；41.636 / 41.900 秒 |

各前缀都有原始 `.log` 与命令绑定 `.json`。两份点变异分别恢复“先删除绑定材料再回收端点”和去掉凭据清理异常路径的第二次机会，目标断言实际失败：退出码均 1，命令分别 6.505 秒与 0.829 秒；见 `worker-restore-order-run.{log,json}`、`worker-restore-cleanup-retry-run.{log,json}` 和 `worker-restored-mutants.json`。这些证明对应修正的范围，没有证明余下 23 个模块通过。

## 请 Host 先复核的边界

请核对初跑与后续聚焦证据的提交绑定、完整清单与统计口径，以及 `9b73fe42` 的两处 Host 修正是否保持原有行为。已明确的 RPC 参数与 Mock/环境夹具遗漏可以单独划定补充微任务；控制器无结果、并发状态域、安装版 ZCode 和 Host 回合的失败仍需先定位，不能统称为 0700 路径问题或机器负载。请在复核意见里明确哪些属于夹具适配、哪些需要继续调查，以及允许继续的写入范围；现有计划中的补充范围尚未获准实施。

四个真实 harness 的最短回合脚本只完成静态准备，真实回合数为 0，尚无逐次批准。现有完整检查中的原生离线夹具不构成这些付费冒烟的完成证据。日常服务和 Worker 未重启、停止或替换，日常运行时与登录凭据未改，默认公共端点命名空间未清扫。复核等待期间保留失败检查的私有材料；本次只新增复核记录和计划索引，按约定只运行仓库卫生检查。
