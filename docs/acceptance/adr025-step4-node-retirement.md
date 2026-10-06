# ADR-025 第四步 4-C：删除项目 DSH Node 集成、迁移其验证意图

本记录是 ADR-025 第四步微任务 4-C 的交付材料：在基线 `68c03c0` 的受管 worktree（唯一可写范围为 `harnesses/dsh/`、`src/hey_my_buddy/buddy/harnesses/dsh/yaml_bridge.py`、`tests/python/buddy/harnesses/dsh/` 与本记录及其编号表）内，整树删除 DSH 的 Node 集成（23 个文件、6,634 行：run.mjs 运行器、五个进程内插件、模型目录脚本、共享库与全部 Node 测试及支撑夹具），连同其唯一 Python 消费方 `yaml_bridge.py`（108 行）一并删除，不留兼容层。项目 DSH 集成自此只有 Python：`dsh/acp/`、`dsh/native_run.py`、`dsh/protocol.py`、`dsh/adapter.py`（4-B1/4-B2 已验收并接线的主体），本任务未改其中任何一行生产代码。110 个 Node 叶子测试的意图逐项核定并迁出，见[编号表](adr025-step4-node-retirement-test-ids.tsv)：63 行覆盖（covered/covered-retired/已接受差异）指向实际存在的 Python 新旧见证、38 行随实现细节退役并写明理由、1 行为本轮新增 Python 见证。本微任务不声称第四步整体完成：真实模型冒烟、模型/安装版原生检查、公共面整合（下文清单）归 Host。

## 边界与方法

- 全程零模型调用、零安装版 DSH 启动：全部验证经本 harness 的假 ACP 程序与私有 HOME/DSH_HOME；没有运行完整检查套件（按任务书归 Host 整合后统一执行）。未写 ADR、SKILL、Host 指南、README/参考文档；未动 pyproject/lock、packaging、cli.checks、roles、registry、discovery（这些归 Host，见整合清单）。
- 任务根 `<4-C 任务根>/`（Host 创建登记）；所有命令 `TMPDIR` 与 `BUDDY_CHECKS_TMPDIR` 指其 `t/`，本轮一次性材料全部在新目录 `m/`（`m/ids-before.json`、`m/ids-after.json`、`m/node-baseline/`、`m/node-leaf-tap.txt`、`m/mirror4c/`、`m/mutation-a/`、`m/mutation-b/`），未覆盖任何旧实验，未手动删除任何对象（含任务根内），未使用 rsync --delete 或 rm 清理材料；记录中路径均为仓库相对、`~` 或占位。
- 解释器是本检出自己的 `uv run --frozen` 环境（基线 `68c03c0` 的 pyproject/uv.lock，本任务未改）；正式证据一律来自全新构造的组合副本（下节），检出内运行只作发育期快跑。

## 交付内容（删除清单）

- `harnesses/dsh/` 整树 23 个文件：`package.json`、`plugins/`（activity、inquiry-bridge、no-tool-structured、turn-result、usage）、`scripts/run.mjs`、`scripts/model-catalog.mjs`、`scripts/lib/turn-contract.mjs`、`scripts/lib/yaml.mjs`、`tests/` 九个测试文件与四个支撑夹具，共 6,634 行。
- `src/hey_my_buddy/buddy/harnesses/dsh/yaml_bridge.py`（108 行）：唯一消费方是已删的 `scripts/lib/yaml.mjs`（Node settings/patch 解析），删除后本项目不再有任何 YAML 解析入口。
- `tests/python/buddy/harnesses/dsh/` 内三个驱动已删 Node 生产代码的测试模块/类：`test_dsh_activity.py`（6 项，驱动 activity.mjs 与 run.mjs 旗标）、`test_dsh_usage_plugin.py`（7 项，驱动 usage.mjs）、`test_dsh_session_storage.py` 的 `DshSessionRootTests`（7 项，驱动 run.mjs）；以及零消费方旧夹具 `fixtures/mock_dsh.py`（旧快速路由协议回放，其消费方测试已随 4-B2 删除，全仓库无引用）。
- 保留的夹具：`fixtures/native-usage-dsh.json`（协议测试与本树外共享消费方仍在用）、`fixtures/mock_turn_runner.mjs`、`fixtures/mock_quota_turn_runner.mjs`（跨目录黑板测试仍经 `BUDDY_RUNNER_PATH` 引用，等 Host 迁移那批测试时一并退役，见整合清单）。
- 新增 Python 见证两处：`test_native_run.py` 的 `FastSeamTests.test_a_non_end_turn_stop_is_a_real_native_failure_not_an_answer`（承接旧 no-tool 直连流"非 stop 终止保持真实原生失败"的唯一存活意图，含假 ACP 程序的 `--stop-reason` 注入开关）；`test_dsh_role_wiring.py` 的无兼容层见证扩一行（`dsh.yaml_bridge` 模块不存在）。`test_dsh_session_storage.py` 模块说明改写为其余两类的现状语义。

## 验证证据

- 组合副本（`m/mirror4c/comb2`，全新目录一次构造）：`git archive 68c03c0` 按 `git ls-tree` 路径表展开、构造时即排除全部六条已删路径（`harnesses/dsh/`、`yaml_bridge.py`、`test_dsh_activity.py`、`test_dsh_usage_plugin.py`、`fixtures/mock_dsh.py` 及其相互包含关系），再覆盖本任务 scope（`tests/python/buddy/harnesses/dsh/` 全树与本记录两个文件）；副本内确认已删路径不存在、scope 树与检出逐字节一致。首次构造 `m/mirror4c/comb` 因路径表漏掉三个 Python 侧删除文件而作废，原样保留未复用。在其上以 `BUDDY_DEV_SOURCE=1`、任务根 `t/` 私有临时目录、`PYTHONDONTWRITEBYTECODE=1` 运行：`tests/python/buddy/harnesses/dsh` 全树 155 项通过（acp 71、native_run 55、role_wiring 11、tool_evidence 8、session_storage 4、no_tool 6），`buddy.harnesses.test_run_contract` 31 项通过；镜像上的 unittest 装载集合与检出 after 清单逐项相等（`m/ids-mirror.json` 对 `m/ids-after.json`）。
- 测试编号对账（真实 unittest 装载集合，无装载错误）：基线 174 项 → 本轮 155 项；删除 20 项（`test_dsh_activity` 6、`test_dsh_usage_plugin` 7、`DshSessionRootTests` 7，全部是驱动已删 Node 生产代码的见证，见编号表逐项去向）、新增 1 项；其余模块按集合相等证明未变。原始清单 `m/ids-before.json`、`m/ids-after.json`。
- 迁移防护故障注入（最小两项，均由组合副本 `comb2` 复制的全新变异副本，均"变异 FAILED→原件 OK"且失败原因命中目标断言而非仅非零退出）：
  - 变异 A（`m/mutation-a`）：`native_run._settle_stop_reason` 改为对任意停止返回 `None`（把非 end_turn 停止当成功）→ 新增停止原因见证 FAILED 于 `reason_code`（'native-max-tokens' ≠ 'ok'）与 `value is None` 断言；原件 OK。
  - 变异 B（`m/mutation-b`）：在包内放回 `yaml_bridge.py` 兼容桩 → `DescriptionSeamTests.test_the_description_has_no_legacy_execution_entries` FAILED 于 yaml_bridge 的 `find_spec` 断言；原件 OK。
  - 前轮 m1–m4（4-B2）与 M1–M5（4-B1）防护未改动，未重跑。
- 发育期快跑（检出内）：删除后立即全树 156 项时曾 1 失败（下节偏差二的治理载体停止原因遮蔽，据此把新见证收窄到 final-message 载体并复跑 155 项全绿）。

## Node→Python 意图迁移（摘要，逐项见编号表）

- **已覆盖（63 行）**：插件语义（活动、工具事实、用量投影、完成回执、会话存储、无工具范围）在 4-B1/4-B2 已迁入 `dsh/protocol.py`、记录读模型与共享角色/问询桥，本轮只做逐项对号；Python 读侧共享格式（`protocol/test_usage.py`、`test_activity.py`、`test_inquiry_transport.py`、zcode 问询套件）承载归一与传输意图。
- **随实现退役（38 行）**：Node 载体自身的面——argv/CLI 契约、启动器解析、settings/YAML 解析、32000 字节旁路、stdout 摘要、socket 三件套与 =value 形式、Cordis 工具作用域、steer 生命周期、sidecar 写侧合并、以及"非 EPERM 观察错误当作组消失"的旧口径（ADR-025 第 8 条统一为保守口径的例外随本删除消失）。
- **已接受差异承载（1 行显式标注）**：fast 无工具调用改走 ACP 会话，多出 DSH 系统提示词；问询只在合作检查点送达。五项已接受差异不变，本轮未发现需报告的新行为差异。
- **新增见证（1 行）**：非 end_turn 停止（max_tokens/refusal）在 final-message 载体上保持真实原生失败。
- **发现并上报（未改，Host 所有文件）**：治理载体上，根回合以非 end_turn 停止且尚无完成回执时，`dsh/protocol.py` 的 `evidence.settle` 先于 `_settle_stop_reason` 抛 `missing-finish`，停止原因被遮蔽；如需"停止原因优先"，把 `_governed_round` 内 `_settle_stop_reason` 提到 `evidence.settle` 之前即可（`native_run.py` 两行内改动）。本轮见证不钉该优先级，仅在编号表 reason 中如实记录。

## 公共整合清单（Host）

1. `src/hey_my_buddy/cli/checks.py`：`dsh_node_tests()`（约 213 行）在 `harnesses/dsh/tests` 为空时 `SystemExit`，完整检查会因此失败；请随 Node suite 退役一并删除该函数与 node 套件调度（319–430 行附近的 node 任务、计数与失败分支）。
2. `packaging/runtime-assets.json`：删除 `harnesses/dsh/scripts`、`harnesses/dsh/plugins` 资产行与 `dsh.runner`、`dsh.catalog`、`yaml.bridge` 三个资源键。
3. `pyproject.toml` 第 25 行 sdist include 中的 `/harnesses/dsh/scripts`、`/harnesses/dsh/plugins`。
4. `src/hey_my_buddy/buddy/roles/turn_io.py` 第 28 行注释仍指向已删的 `harnesses/dsh/plugins/turn-result.mjs`（纯注释，建议改为指向共享 governed prompt）。
5. `tests/python/buddy/roles/test_assistance_hints.py::test_dsh_prompt_section_states_the_same_triggers` 读已删插件源码，现已无法装载文件；DSH 治理提示词已与 ZCode 同出 `worker_services.governed_prompt`，一致性由同模块的 ZCode 嵌入见证按构造承载，建议删除该测试（其余三项不受影响）。
6. 跨目录旧测试迁移（4-B2 已登记的两处装载错误与本轮新增）：`tests/python/blackboard/routing/test_stage2_review_scope.py`（import dsh.runner）、`tests/python/buddy/harnesses/test_adapter_usage.py`（import dsh.adapter 旧符号）、`tests/python/buddy/harnesses/test_private_adapter_invariants.py` 与 `tests/python/buddy/harnesses/test_controller.py` 的旧接缝用例（同 4-B2 清单）；另 `tests/python/blackboard/tasks/test_workflow_worker.py` 与 `test_host_workflow_worker.py` 仍引用本树 `fixtures/mock_turn_runner.mjs`、`mock_quota_turn_runner.mjs`，迁移时把两夹具一并删除。
7. 公共 discovery 的 DSH 版本探针强制私有 DSH_HOME（Host 并行线）；共享格式剩余无业务消费者字段的精简（usage/activity sidecar 读侧）按任务书等全部测试迁移稳定后由 Host 统一处理。
8. 冒烟缺口（与 4-B1/4-B2 记录一致，归 Host）：只读预设下"用命令写文件"、真实会话记录上的用量与实际生效工具集、统一 patch 新组合（源指路行+记录根钉住+工具范围行+两项始终关闭行）的真实核对。

## 无生产使用方与实现说明

- 本轮删除项均无生产使用方：`yaml_bridge.py` 仅被已删 `yaml.mjs` 调用；`mock_dsh.py` 全仓库零引用；被删测试模块仅自含。`--stop-reason` 是假 ACP 程序的新增注入开关，有本轮新见证消费；除此之外无新增无读写方字段。
- 行数：Node 树 −6,634（23 文件）、`yaml_bridge.py` −108；`test_native_run.py` +18（一个见证与注释）、`fake_agent.py` +5/−1（`--stop-reason` 参数与 final 载体一行替换）、`test_dsh_session_storage.py` +15/−187、`test_dsh_role_wiring.py` +1、删除三个测试模块/类与一个夹具。生产源码净变化为 −108（仅 `yaml_bridge.py`）。
- 提交即本线固定交付（提交哈希随结构化结果报告）；任务根未回收，等 Host 按登记确切根整体回收。
