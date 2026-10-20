# 各角色共用一套档位：执行计划（Router 的不用工具与只读两档）

2026-10-20，整合 Host。依据是 [ADR-023](../../decisions/023-harness-integration-principles.md)“各角色共用一套档位”一节（2026-10-13 定，尚未实现）与 [ADR-021](../../decisions/021-router-buddy-planes-and-routing-evidence.md) 第 4 条。开发者于 2026-10-20 定：让 DSH 与 ZCode 能担任审阅模式的 Router（ADR-021 的 L6），提前到首次发版之前做。按 ADR-023，L6 由共用档位取代，所以这一步就是把 Router 的两种运行并进档位。只联网一档随画像面再做。本文只安排实现，不表示已经实现。

## 现状（读 `8751dea4` 的源码所得）

- **Worker 的档位声明：** `protocol/capabilities.py` 的 `WORKER_PERMISSIONS`。四个 harness 都声明了只读、写检出与放开，并各写明强制还是约定。
- **Router 的运行另有一套：**
  - 快速路由看适配器的 `no_tool_structured`：Codex、DSH、ZCode 有，Claude Code 没有。
  - 审阅看 `read_only_structured` 与 `local_read_only_check()`：Codex、Claude Code 有；DSH、ZCode 返回 `harness.readonly-worker-carrier-unimplemented`。
- **审阅的原生配置与 Worker 的只读一档不同：**
  - Codex 用专门的权限配置（`harnesses/codex/config.py` 的 `read_only_config`）：关闭联网搜索与网络，只许读冻结副本。
  - Claude Code 另加专用参数（`harnesses/registry.py` 的 `review_request_controls`）：禁掉联网、MCP 与子代理。
  - ZCode 的 Router 运行不经档位：`_session_mode` 在没有 Worker 档位时用 `yolo`，并不是只读。
  - DSH 的 Router 只读用它自己的只读预设（`tool_scope_launch`）。
- **判定：** 发布时 `protocol/tool_evidence.py` 的 `judge_tool_evidence` 按模式与有无系统沙盒取允许的类别：有沙盒允许读取、搜索、执行，没有的只允许读取、搜索。有无系统沙盒来自路由记录里冻结的 `toolPolicy.systemSandbox`（`blackboard/routing/decision.py`）。
- **资格的其他出处：**
  - 目录给能审阅的 buddy 加 `decision` 能力（`blackboard/catalog/catalog.py`）；
  - harness 的健康状况带 `systemSandbox` 与 `readOnlyStructured`（`blackboard/service/harness_health.py`），控制台读它们（`apps/console/src/api.ts`）。

## 要做的

1. **一张档位声明表，各角色共用。**
   - 每个 harness 对每一档声明做不做得到、强制还是约定。在 Worker 的三档之外加上“不用工具”。
   - 它取代 `WORKER_PERMISSIONS`、`no_tool_structured`、`read_only_structured` 与 `local_read_only_check()` 各自的判断。
   - 声明只写原生上已经落实的档：Claude Code 现在没有不用工具的运行，就不声明这一档。
2. **运行请求只带档位。**
   - Router 的快速运行带“不用工具”，审阅运行带“只读”，Worker 带 Host 选的一档。
   - 各 harness 的运行模块按档位落原生配置，同一档不分角色，用 Worker 已有的落法（ADR-023 的表）。
   - 删掉审阅专用的原生配置：Codex 的 Router 权限配置，Claude Code 的审阅专用参数。
   - ZCode 与 DSH 的 Router 运行改按档位：ZCode 只读即 `plan`，DSH 只读即它的只读预设。
3. **资格就是声明。**
   - 能不能担任快速或审阅的 Router，看 harness 有没有声明那一档。
   - 目录的 `decision` 能力、健康状况里的相应字段与控制台的读法，改为由声明推出；没有读者的就删掉。
4. **判定按档位，一张表，只用于 Router 的运行。**
   - 不用工具：不允许任何调用。
   - 只读：允许读取、搜索、执行命令与联网（档位不管网络，ADR-023 第 6 条）；出现修改或归不了类的调用，这次答案作废。用户配置的 MCP 带进来、用了不属于只读的工具，同样作废。
   - 不再按有无系统沙盒分两套。路由记录里冻结的策略事实改为这次运行的档位。
   - Worker 的运行不加判定，仍由各家原生拒绝并报给 Host。
5. **审阅已有的防护不变：** 只提供冻结副本，结束后核对副本未被改动，预算上限照旧。
6. **存储与契约。**
   - 路由记录里策略事实的形式变了。照 AGENTS.md 处理：受摘要约束或已封存的内容不改写；改了存储形式，就在同一次改动里开始新的一次升级（25 → 26），否则说明两种形式怎样并存且没有歧义。由实现方定，交付里写明理由。
   - 运行请求与回执跨执行进程与服务的边界，契约随之变到 0.48.0。
7. **顺带：** `roles/texts/router.txt` 里画像一项的标签 “Buddy:” 改为 “Profile:”。

## 检查

- **不调用模型，不碰真实 harness。** 用各 harness 已有的假代理与夹具：
  - DSH 与 ZCode 的审阅运行交回结构化答案；
  - 只读之外的调用使答案作废；
  - 冻结副本被改动即作废；
  - Codex 与 Claude Code 的审阅改用只读一档之后，结构化答案与判定照旧，联网不再被拦。
- **受影响的测试：** 四个 harness 的运行模块、路由与发布、目录、harness 健康状况、工具证据、角色层，以及钉着档位声明的测试，用检查运行器跑。
- **控制台：** 改到控制台的读法，跑前端全量与浏览器检查。
- **Host 验收时：** 在私有服务上用真实的 DSH 与 ZCode 各跑一次审阅模式的路由。这是付费的原生调用，由开发者逐次批准。

## 交付里写明

- 档位声明表的新形状，以及它取代、删掉的各处；
- 每个 harness 在审阅时的原生配置，并指出与 Worker 只读一档相同；
- 存储与契约的处理及理由；
- 改为由声明推出、或删掉的读者。
