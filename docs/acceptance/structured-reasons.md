# 有结构的原因：验收记录

第 4 步批次二的一部分，见[第 4 步的执行计划](../design/step4-remaining-plan.md)与[有结构的原因的执行计划](../design/structured-reasons-plan.md)。各微任务都在宏任务“第 4 步：控制台余下的事”里，实现都在 `socu/batch2` 上，随批次二的 0.46.0 安装。

## 执行计划（2026-10-17）

- **第 1 版：** 微任务“有结构的原因的执行计划”（run `e2dcfd09`，Codex CLI `gpt-6.1-sol / high`），只写计划。
- **两份审查：** Host 与一个独立审查者分别审，互不看对方的结论。独立审查是微任务“有结构的原因的计划：独立审查”（run `99401c99`，Claude Code CLI `claude-opus-5-5 / high`），审查全文留作 Host 的证据，没有进仓库。两份的方向一致：第 1 版铺得太大。独立审查者另外指出存储形式与升级被拆在不同的微任务里，违反 `AGENTS.md`；同一个事实两套代码；等待原因可以由状态推出；黑板写的请求照 Worker 的样子填四个字段；同一个路由原因存了好几份。入口失效页采用独立审查者的办法（由主应用按 meta 标记画），比 Host 原想的双语静态页少一份文案。
- **第 2 版（`654dd475`）：** 接受，入库 `2410631a`。它用源码纠正了独立审查的一处说法：`router-unavailable` 没有被逻辑当作分类使用，可以直接换成细的代码。Host 改了一句（`04fe1805`）：升级里必须匹配的旧中文句子照原文写，服务源码的守卫明确豁免 `upgrade.py`，不用 Unicode 转义绕过；这一次升级被消耗之后，转换器连同这些句子一起删除。
- **消耗：** 独立审查输入 674 万，全部命中缓存，输出 4.3 万。

## 微任务 0：最小的原因接缝

- **微任务：** “原因0：最小的原因接缝”（run `05528030`，DSH CLI `deepseek-flash / max`，两个回合）。
- **基点与合并：** 基点 `cb88a28c`。第一回合 `f65239e1` 停在一个边界上：写入范围之外的一个冷启动测试写死了运行时资源的集合，实现方报告而没有越界去改；Host 把那个文件加进写入范围，第二回合 `7b3feceb` 只补了那一项。合并在 `socu/batch2` 上（`629ad512`）。
- **做了什么：** `protocol/reasons.py` 的 `reason(code, **params)`；空的代码表 `protocol/reason-codes.json`，作为运行时资源打包；一条静态测试按 AST 检查源码里对 `reason()` 的每次调用：代码必须是已登记的字面量，或来自值全是字面量的映射表，参数名与登记的一致；反例写在临时源码上。
- **核对：** 差异读过；合并后 protocol、打包、分发、运行时与冷启动的 22 个文件 357 项通过。
- **消耗：** 两个回合输入 408 万，其中命中缓存 384 万，输出 3.6 万。

## 微任务 1：健康与目录

- **微任务：** “原因1：健康与目录”（run `9de38568`，Codex CLI `gpt-6.1-sol / high`，三个回合）。
- **基点与合并：** 基点 `629ad512`。第一回合停在边界上：读这些字段的地方有四处在写入范围之外（`evaluation/evaluation.py`、`routing/router.py`、`service/service.py`、`buddy/runtime/execution.py`），Host 补进范围。第二回合 `9ba19878` 在日常看板的副本上升级失败：“Buddy unavailable reason lacks catalog provenance”。日常看板有 16 个 buddy 存的是目录确认缺失的那句原文，而当初写下它的发现记录已被后来的读取替换，转换器找不到出处就拒绝。Host 把日常数据的分布交给实现方后打回；第三回合 `e0cd47b5` 让与常量逐字相同的句子不凭出处直接转换，并补了回归测试。合并在 `socu/batch2` 上（`4c573258`）；`codex/native_run.py` 与运行控制器的发现测试同时带着会话删除的改动，验收时声明为合并的结果。
- **做了什么：** `buddies.unavailable_reason` 拆成 `unavailable_code`、`unavailable_params_json`、`unavailable_content` 三列，两条 CHECK 约束：代码与参数同有同无，内容只跟着原生声明的不可用。健康记录、候选、最近一次探测与账户读取失败里的 `reasonCode`、`remedy` 与旧说明合成一个 `reason`；账户视图的 `reasonCode`、`guidance` 同样合成 `reason`。目录的警告与发现原因改成有结构的原因，原生文字是内容；`catalog_current.reason` 存原因的 JSON；刷新成功的 note、可由状态推出的固定警告、额度警告的 message 删除。登记 48 个代码。
- **核对：** 差异读过。升级在日常看板的只读备份副本上跑过：0.035 秒，schema 25，完整性检查通过，外键检查为空；buddy 122 个没有原因，16 个是 `catalog.model-absent`；52 条目录读取记录删去 note、补上空的不可用内容，39 条固定警告删去（三种各 13 条）；4 条健康记录的 `reasonCode`、`remedy` 换成空的 `reason`。副本里含 `history.reason-unavailable` 字样的 10 个单元格都是 Host 的任务书与 Worker 的结果里引用这个代码的原文，属于内容，没有一条是转换出来的。合并后的 184 个文件 3101 项里只有 `blackboard/routing/test_stage2_review_scope.py` 失败，原因是会话删除那个微任务改了运行控制器，见[删除宏任务的验收记录](macro-task-deletion.md)，与本微任务无关。
- **顺带项（下一个原因的微任务）：** 目录刷新失败时健康记为 `harness.launch-failed`，应记 `catalog.discovery-failed` 并带上错误码；`read_health` 读出时、探测合并时各补一次空的 `reason`，所有写入都已带着它，两处默认值删除；`run_execution.discover_models` 等待到期时仍写字符串原因 `native-metadata-wait-expired`，登记代码之后，`canonical_payload` 接受字符串原因与字符串警告的分支一并删除；`capability_report` 对从未探测过的 harness 编出 `harness.probe-failed`，应取健康记录的原因或 `harness.not-checked`。
- **消耗：** 三个回合输入 1706 万，其中命中缓存 1680 万，输出 3.3 万。
