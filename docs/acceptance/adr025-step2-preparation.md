# ADR-025 第二步准备（微任务 2-P）：格式保护测试与公共解码收拢

2026-10-06，Worker 由 ZCode 承担。输入基线 `c48d31c9cdbab9e8eb61b6333f6deb4388a848c1`（第一步与 DSH ACP 客户端已由 Claude Code Host 验收），交付分支 `socu/adr025-step2-preparation`：第一轮代码与测试为提交 `ea70cd2`（记录初版随 `2aaed02` 提交），第二轮为验收修正提交（内容见「第二轮修正」）。任务与对应交付：补第一步验收指出的两处缺失测试（解析前整帧大小门、普通字段严格类型），并清理过时的严格解码参数。执行计划 §5 的既有结论不变：本步只补测试与等价收拢，不改任何原生行为；ZCode 抽取属后续微任务。

## 范围与清理

改动只落在 writeScope 八条之内：`buddy/harnesses/controller.py`、三个 adapter、三个对应测试文件与本记录。只读公共值（`buddy/harnesses/run_contract.py`、`buddy/harnesses/live.py`、`protocol/internal_models.py`、根包 `json_codec.py` 及角色/registry/runtime 生产源码）零改动，`git status` 为证。任务根 `/private/tmp/a252p-_p2_ihkl`：`t/` 承载每次运行的 `TMPDIR` 与 `BUDDY_CHECKS_TMPDIR`；`m/` 放一次性材料（运行环境脚本、探针、两份变异副本、测试 ID 清单与原始日志）；`h/`、`d/` 未使用（本微任务未启动 DSH 或任何原生，不需要探针 HOME）。本 Worker 未删除任何对象。

## 交付 C：唯一公共严格解码

核实：三个 harness 的 `protocol.decode_json` 自第一步瘦身起就是根包 `decode_strict_json` 的纯别名，`read_strict_result` 的 `decode` 参数已无差别可言。`read_strict_result` 现在直接调用根包 `decode_strict_json`，`decode` 参数删除；controller 模块 docstring 里"三个严格 decoder 真正不同（Claude 拒绝溢出到无穷大的浮点数）"的旧差异说明改正为同一公共实现（重复成员与全部非有限数字、含 `1e999` 溢出，三条路径一致拒绝）。512 KiB+1 读取上限、非对象拒绝、原异常折叠集合（`OSError`/`ValueError`/`RecursionError`）、Router 普通 256 KiB 读取与 DSH 末行读取全部未动。

调用方同步：Claude 两处、Codex 两处、ZCode 两处 `read_strict_result(path, decode=decode_json)` 改为 `read_strict_result(path)`；ZCode adapter 的 `decode_json` import 随之冗余删除（该文件再无其他使用），Claude 与 Codex 的 `decode_json` 因各自由其他路径使用而保留。三个 adapter 除此之外无任何改动。全仓检索确认 `read_strict_result` 没有这三个 adapter 与 `test_controller.py` 之外的调用方，无兼容参数残留。

测试同步：原以 `decode=explode` 注入故障的 512 KiB 读取门测试，改为在真实公共调用点注入（`mock.patch.object(controller, "decode_strict_json", …)`），超限文件必须从未到达解码器；原 `strict_decode` 替身删除，重复成员与 `NaN`/`Infinity` 拒绝测试改用真实公共解码器，并补充 `1e999` 溢出用例（第一步合并后的行为）。一个测试 ID 改名（见编号表）。

## 交付 A：解析前整帧大小门

构造原则：被拒帧的每个字段都在各自上限之内——同一 payload 去掉填充即为一帧合法输入、解码成功——只有原始 JSON 帧字节数越界。第一轮曾以拒绝消息作为"解析前拒绝"的证据，这一表述过强、已在第二轮修正（见下文）：先解析再限长的实现同样给出这条消息，解析顺序的证明现在来自对真实根包 `decode_strict_json` 调用点的 spy——超限帧拒绝时解析器零次被调，合法对照帧恰被它解析一次。每个用例仍断言错误是"帧"的 `byte bound` 消息（与字段校验器的拒绝区分），文本与原始字节两种形式都覆盖。具体：run 请求与 run 结果取完整合法 payload，在首两个 token 之间插入一段空白推过 16 MiB / 2 MiB 门；live 请求与 reply 同法推过 64 KiB 门；live snapshot 直接用合法字段的规范编码超过 64 KiB（32 条各 4000 字节的有界回答，正是现有分页器真实承载的集合）。

规范编码能否触发的计算与依据：run 请求帧各字段取最坏转义（控制字符每字节膨胀 6 倍）后，探针实测最大合法规范帧 14,281,093 字节（13.62 MiB），低于 16 MiB 门——合法规范请求永远到不了帧门，只有非规范的空白填充或超限原始传输字节才触发它，这正是该门作为解析前防线对接收方的意义。live 请求与 reply 的有界字段合计只有几 KiB（问题文本最坏 6×4000 字节），同理到不了 64 KiB。live snapshot 则相反：合法内容本就可以超过一帧，由分页器消化，门的拒绝是既有设计（`test_the_full_answer_set_is_reachable_through_paged_observation` 未变）。

## 交付 B：普通字段严格类型

覆盖选择：只测类型完全由共享 strict 配置把守的字段（自有校验器把守的字段如 `Text`/`Identifier` 与 strict 开关无关，不作为本交付的证据）。Python 构造入口：`capture_evidence=1`、`NetworkPolicy(requested=1)`（int 当 bool）、`RunBudget(timeout_seconds="60")`、`RunIdentity(generation=True)`、`RunEnd(native_exit_code="0")`（str/bool 当 int）。wire 入口：请求帧的 `captureEvidence=1`、`timeoutSeconds="60"`、`generation=true`，结果帧的 `modelStarted=1`、`nativeExitCode="0"`；live 侧构造入口 `LiveCapabilities(activity=1)`、`InquiryState(seq=True)`，wire 入口 snapshot 的 `sequence="3"`、`truncated=1`、`inquiries[0].seq=true`。`formatVersion` 的精确整数（`true`/`1.0` 拒绝）第一步已有测试，未重复。

## 变异证明（隔离副本）

变异在 `<task-root>/m` 下的两份独立 `src` 副本上做，worktree 生产源码零改动；每次运行经 `PYTHONPATH` 前置验证确被导入（打印 `__file__` 属变异副本）。三份测试文件共 93 个测试。

| 组 | 变异做法 | 变异副本 SHA（sha256） | 红 | 恢复后 | 恢复副本 SHA |
| --- | --- | --- | --- | --- | --- |
| 帧门 | `json_codec.py` 去掉 `decode_bounded_frame` 的字节数比较 | `d7e41917abb9aae55454bf0de67319a74e2e3b29dec1103f337811e6c45ec9d2` | 93 中恰好 4 失败，全是新增整帧门测试 | 93 全过 | `a2989e47c2ce9aa2cb883ced1c0007fed9984ef3eb583e2ca4ea5caeeccc561f`（等于基线字节） |
| 严格 | `internal_models.py` 共享配置 `strict=True`→`False` | `dd87df9ae101025b624fb05e257db41648e8993d1c2fc0512bd9eed0241b4752` | 93 中恰好 4 失败，全是新增严格类型测试 | 93 全过 | `f42ffe23d36375f418fa6163fea77d4aa68c4a5471d20c0fc1090c9e8c1dfc11`（等于基线字节） |
| 先解析再限长（第二轮） | `json_codec.py` 把 `decode_strict_json` 挪到限长判断之前，拒绝消息与拒绝结果保持不变 | `03edae662f3e73de964a48c9649849e6db126374bd0c0a87c2f37be528cd33fb` | 93 中恰好 4 失败，全是 spy 的"解析器零次被调"断言（见第二轮修正） | 93 全过 | `a2989e47c2ce9aa2cb883ced1c0007fed9984ef3eb583e2ca4ea5caeeccc561f`（等于基线字节） |

两组红各自的 4 个失败正是本交付新增断言，其余 89 个测试在变异下不受影响，与第一步 Host 验收"去掉帧门/关掉 strict 都没有测试失败"的记录吻合。日志在 `<task-root>/m/logs/`（`mutant-gate-red.log`、`mutant-strict-red.log`、`mutant-gate-restored-green.log`、`mutant-strict-restored-green.log`）。

## 第二轮修正（turn 2）：解析顺序的故障见证

Host 验收发现：把根包 `decode_strict_json` 挪到 `decode_bounded_frame` 限长判断之前后，第一轮的四个整帧门测试仍 4/4 通过——它们只断言拒绝消息与合法对照，而"先解析、再限长"的实现给出完全相同的消息，消息证据分不出先后。本轮只修这一个缺口，不改生产代码、不放宽帧上限：四个测试（测试 ID 不变）改为对真实根包调用点做 spy（`mock.patch.object(json_codec, "decode_strict_json", wraps=…)`，包住真解析器）：超限帧（str、bytes 与 mapping 形式）被拒时必须 `assert_not_called()`，合法对照帧必须被恰好在场的一次真实解析接收（`assert_called_once_with` 规范文本）——这同时证明 spy 挂在真实调用点、正常大小仍能解析，不再是只看错误文本。

隔离副本重现 Host 的"先解析、再限长"（拒绝消息与拒绝结果逐字不变，仅调换顺序）：93 个测试中恰好 4 个失败，且失败点是 spy 断言（`Expected 'decode_strict_json' to not have been called. Called 1 times.`），消息断言在该变异下依旧全过——与 Host 观察一致，现在由 spy 抓住。恢复到固定字节（SHA 等于基线 `a2989e47…`）后 93 全过；worktree 固定源码上 93 全过（`logs/mutant-parse-first-red.log`、`logs/mutant-parse-first-restored-green.log`、`logs/worktree-green-93-turn2.log`）。四个测试的类 docstring 与本记录中"只有解析前门能拒绝"的过强表述一并改正；其余第一轮内容（两组变异、编号映射、命令与数量）原样保留，按本轮边界未重跑 harness/usage 套件、完整检查与探针。

## 测试编号

三个测试文件由 `unittest.TestLoader` 实装枚举：改动前 85 个 ID，改动后 93 个。新增 8、改名 1、删除 0；未列出的 84 个 ID 前后集合相等（清单在 `<task-root>/m/ids-before.txt`、`ids-after.txt`）。

| 处置 | ID |
| --- | --- |
| 新增 | `test_run_contract.WholeFrameGateTests.test_an_over_limit_request_frame_is_refused_before_parsing` |
| 新增 | `test_run_contract.WholeFrameGateTests.test_an_over_limit_result_frame_is_refused_before_parsing` |
| 新增 | `test_run_contract.StrictScalarTypeTests.test_python_construction_refuses_int_for_bool_and_str_for_int` |
| 新增 | `test_run_contract.StrictScalarTypeTests.test_the_wire_entries_refuse_int_for_bool_and_str_for_int` |
| 新增 | `test_live_channel.WholeFrameGateTests.test_over_limit_request_and_reply_frames_are_refused_before_parsing` |
| 新增 | `test_live_channel.WholeFrameGateTests.test_a_snapshot_of_legal_bounded_answers_that_exceeds_one_frame_is_refused` |
| 新增 | `test_live_channel.StrictScalarTypeTests.test_python_construction_refuses_int_for_bool_and_str_for_int` |
| 新增 | `test_live_channel.StrictScalarTypeTests.test_the_wire_entries_refuse_int_for_bool_and_str_for_int` |
| 改名 | `test_controller.StrictReadTests.test_a_complete_object_result_is_decoded_through_the_calling_decoder` → `…decoded_by_the_common_strict_decoder`（"经调用方 decoder"的前提随参数删除而消失） |

## 实际验证命令与数量

每次运行以 `<task-root>/m/focused-env.sh` 镜像检查运行器的子进程环境（`PYTHONPATH` 先 `<worktree>/src` 再 `<worktree>/tests/python`，清理 `BUDDY_*`/`VIRTUAL_ENV`/`UV_PROJECT_ENVIRONMENT`/第三方覆盖变量，私有 `BUDDY_STATE_DIR`/`BUDDY_RUNTIME_ROOT` 落在 `<task-root>/m`），并显式 `TMPDIR=<task-root>/t`、`BUDDY_CHECKS_TMPDIR=<task-root>/t`，命令形如 `uv run --frozen python -m unittest <模块名…>`。未跑完整检查、控制台、打包、真实 harness CLI 与模型调用，符合微任务边界。

| 命令对象 | 结果 | 日志 |
| --- | --- | --- |
| 三个改动测试文件 | 93 全过 | `logs/worktree-green-93.log` |
| 三个改动测试文件（第二轮 spy 版） | 93 全过 | `logs/worktree-green-93-turn2.log` |
| `claude.test_claude`+`test_claude_worker`（adapter 调用点受影响） | 45 全过 | `logs/claude-green-45.log` |
| `codex.test_codex`+`test_codex_continuation`+`test_no_tool_codex` | 55 全过 | `logs/codex-green-55.log` |
| `zcode.test_zcode`+`test_no_tool_zcode` | 34 全过 | `logs/zcode-green-34.log` |
| `test_adapter_usage`+`test_private_adapter_invariants` | 27 全过 | `logs/usage-invariants-green-27.log` |

保护可触发性：是。填充帧与超限合法 snapshot 在解码入口触发帧门（编码入口 `encode_run_*` 对同一越界 payload 同样显式拒绝，既有测试未变）；严格类型在构造与 wire 两个入口触发；三组变异证明这些拒绝正是由被测保护产生——帧门的解析顺序保证由第二轮的 spy 断言守护，而非字段校验、手工类型检查或错误文本的副作用。

## 公共接口缺口（报 Host 决定，本微任务未改）

- **结果帧界与转义膨胀。** `RunValue.raw` 字段界 512 KiB 按 UTF-8 字母计，控制字符规范编码膨胀 6 倍后超 2 MiB 结果帧门：字段合法的一帧结果可以被帧门显式拒绝（探针复现于 `<task-root>/m/probe-frame-bounds.py`，`encode_run_result` 与 `decode_run_result` 两侧一致拒绝、无静默截断）。对照：请求帧界 16 MiB 的注释明确承载 6× 最坏转义（实测 13.62 MiB 成立），结果帧界注释只承诺"容纳旧 512 KiB 严格读取"——旧路径的 512 KiB 上限本来就作用于整份结果 JSON，新帧仍覆盖旧范围，只有逼近 `raw` 字段上限且重转义的值受影响。是否放宽 `MAX_RUN_RESULT_BYTES` 或记录为接受，由 Host 统一改公共值时决定。
- **`read_strict_result` 签名变化。** 纯 harness 内部接口，调用方已全部同步；若后续步骤有仓外脚本引用旧签名需注意（未发现）。
- live snapshot 规范内容可达 64 KiB 属分页器设计，已在现有测试覆盖，无缺口。
