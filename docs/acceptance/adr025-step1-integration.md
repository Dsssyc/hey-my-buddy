# ADR-025 第一步：Host 整合登记

2026-10-04，Host 在 `1de0168` 整合 1-D 两个新防护测试与编号表后集中运行完整检查。该次退出 1：成功汇总为 Python 2,475 项（跳过 1）/165 个成功文件，全部调度 166 个文件；唯一失败文件 `blackboard.store.test_blackboard` 自身运行 57 项、报 12 个 error，Node 110 项通过。失败都是八个方法（其中一个含五个 harness 子项）仍 patch 已消失的 `worker.get_adapter`；这不是被漏发现的测试。原始日志与汇总保留在 `<repo>/tmp/adr025-host/step1-full-xxpcrnvc/`，该运行器自建根 `<system-tmp>/buddy-checks-08h9kmew/` 已正常收尾并确认不存在。

该测试文件不在 1-C 的授权写范围内：1-C 只获四个黑板路由测试文件及角色/runtime 测试的写权限，未获黑板 store 测试写权限。Host 按用户“范围之外由你处理、自己改须登记”的规则修这一整合接缝，只修改 `tests/python/blackboard/store/test_blackboard.py`：八处注入点改为 Worker 实际消费的 `worker_module.role_seam.worker_executor`，八个模拟执行体补 `name=claim["task"]["spec"]["adapter"]`，适配新的准备结果所需标识。未补旧入口，未改生产源码、错误注入、断言、编号或行为。除这八个目标与八个标识外，正规化 AST 与修正前整文件完全相等，证据为 `<repo>/tmp/adr025-host/step1-integration-repair-t0dp7hoa/test-migration-ast-proof.json`。

聚焦 `blackboard.store.test_blackboard` 57 项全部通过，退出 0。为确认这批迁移的停止防护仍能发现错误，在本次隔离 source 副本把 Worker 异常处理的“只有 command 可凭外层 handle 确认停止”改为所有 harness 都可确认；既有 `TestCrashWindows.test_native_controller_exit_does_not_prove_native_stop_after_collect_failure` 的 Codex、ZCode、Claude、DSH、decision 五个子项全部红（退出 1），按字节恢复后该用例绿（退出 0）。生产源码没变，恢复 SHA-256 为 `984d022c284f6bf2b9b8a3e2cc92e297d469273c954b7d0661199d215a9dd397`，变异 SHA 为 `de2c363e72d3f937a1d4765388f36e5b9d7150da6ad02356a1c03c765ca93822`；脚本、红/恢复绿、汇总及 SHA 留在上述 repair 目录。三个自建检查根已由检查函数正常收尾：`<system-tmp>/buddy-checks-xg60vf18/`、`buddy-checks-eg9hkyuz/`、`buddy-checks-v_ne54vn/`，各自创建时即登记确切路径，无通配符，未屏蔽错误，其他会话对象没动。聚焦与变异只用合成 harness，真实 harness/model 检查 0 次。

另按用户新相称性规则登记 Host 的小文档更正：`1de0168` 更正 1-D 原始编号清单的可见范围（原清单在 Host 实施检出存在，重建集合与它相等）、忽略 `.venv/` 不出现在普通 `git status` 的表述、普通测试 fixture 操作与 Worker 手动清理的区分；`0492dd8` 采用已发出的文档回合记录并补规则生效时序说明。文档 continue 已在新规则到达前发出，此后没有再为同类小更正打回或重跑；没有更改做过的检查、验证结论或违规披露。黑板整合登记 `int-822a8508-853e-4d5d-a5c3-40c73de6b2d8` 已列该文档调整及原因，最初请求缺 reason 被拒后仅补 reason 重发，未产生另一份整合或运行。

至此 Host 修改的实施测试文件仅上述一份，生产源码由各固定交付原字节整合。这次失败和测试修正构成重跑完整检查的具体理由；控制台与构建树没有变化，已经通过的 659 项测试、类型检查和构建不重复。整步完整检查的最终成绩及外部验收边界由 `adr025-step-1.md` 记录，不在此提前宣告通过。旧的暂停、越界披露、原因撤回和失败材料保持原样。
