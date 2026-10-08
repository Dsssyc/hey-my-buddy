# ADR-025 第五步：会话收据验证合并为唯一公共实现（continue-1 修订）

2026-10-07，源码基线 `43d8d6326d3af3cc6aca1db879febccfaeea41eb`（第五步开头清理微任务，同一 run 的 continue-1）。上一轮固定交付被拒：其"判断口径并轨"改变了 DSH 的 detail 非空/禁 NUL 判断、inquiryId 的 strip 规则与 ZCode 非字符串工具名的错误消息，未被授权；变异三首轮日志被同名复写属任务根内违规。本轮在同一 writeScope 内修正：以一份公共实现原样承接两侧实际存在的判断差别，登记参数表达差别且被核心真实读取，核心不按 harness 名分支，不恢复重复实现；错误码、消息与拒绝顺序逐侧保留；不采用任何未授权的更严口径。两个原生协议模块仍然只引用 `session_receipts.py` 这一份实现；`native_run.py` 零改动（其本就不导入这些名字）；不开始 C-Two 切换。测试编号对账见 [test-ids TSV](adr025-step5-receipts-test-ids.tsv)。任务根确切路径只登记在任务根内私有台账，入库记录一律用 `<任务根>` 占位符。

## 设计：登记规则束，差别作为数据

`session_receipts.py` 的公共验证节含 `verify_receipt`（完成回执，两侧原判断逐字相同，无参数）、`verify_tool_refusal` 与 `verify_inquiry_receipt`（各带关键字参数 `rules`）、私有助手 `_refusal_payload`/`refusal_shaped`/`_valid_question_sha`（两侧原判断逐字相同）与 `_valid_inquiry_id`（按 `rules` 承接空判差别）。差别以 `ReceiptRules`（NamedTuple，四字段）登记为两个模块常量：`BOUNDED_REFUSAL_DETAIL`（ZCode 注册：detail 有界——非空、`MAX_TOOL_REFUSAL_DETAIL_BYTES`、禁 NUL，记录段消息"failed its bounded reason validation"；非字符串原生工具名不做信封段前置拒绝，落到工具绑定段报"different session tool"；inquiryId 按字节判空）与 `TYPED_REFUSAL_DETAIL`（DSH 注册：detail 仅类型检查，记录段消息"carries an unusable refusal record"；非字符串原生工具名在信封段即报"no bounded JSON refusal envelope"；inquiryId 按剥离后内容判空）。每个字段都被核心真实读取；两协议模块的导出名（ZCode `verify_receipt`，DSH `verify_finish_receipt`，两侧 `verify_tool_refusal`/`verify_inquiry_receipt`）是薄接缝：传入本侧登记束、仅把 `ReceiptError` 按同码同消息转为本协议自己的 `NativeError`，`refusal_shaped` 为共享函数本身。控制流逐段对照原文重排核验：信封段（含登记的门）、字段集、签名、身份绑定、工具绑定、记录段的先后与两侧原文一致，任一阶段的码、消息、 fatal 性不变。

## 原基线逐项复核结论

对 `43d8d63` 两个协议模块原文逐函数比对：`verify_receipt` 与 `verify_finish_receipt` 逐字相同（仅函数名与 `validate_outcome` 注解差异，无运行时差异）；`_refusal_payload`、`refusal_shaped`、`_valid_question_sha`、`verify_inquiry_receipt` 代码体逐字相同（docstring 措辞不同）；`verify_tool_refusal` 有两处真差别（非字符串工具名的拒绝阶段与消息；detail 是否有界及记录段消息）；`_valid_inquiry_id` 有一处真差别（字节判空或剥离判空）。除上述三簇外不存在其他判断差别。三簇差别由两个登记束原样承接，任务根 `m/` 的对照实验（下节）给出逐行一致的可复现证明。

## 原基线/共享结果对照（可复现，非猜测）

方法：`git archive 43d8d63 -- src` 提取未改动原树；同一驱动脚本两种模式跑同一输入集——baseline 模式以原树的两侧原函数执行，shared 模式以当前合并核心按各侧登记束执行；逐案例输出"接受值 / 码 / 消息"JSON 行，两侧输出逐行 diff。v1（52 行）覆盖完成回执 6 案例、拒绝信封 12 案例、问询回执 8 案例双侧执行，diff 为空；其中 blank/oversized/NUL detail、whitespace inquiryId、非字符串工具名、未知 reason 各案例同时呈现两侧各自保留的结果（如 blank detail：ZCode 侧 invalid-tool-refusal "bounded reason validation"，DSH 侧接受并原样返回；whitespace id：ZCode 侧接受，DSH 侧"answer binding"拒绝）。v1 的 `refusal-non-string-detail` 命名不精确（篡改为 int 后在签名段即被拒，未达记录段），v2（58 行）更正该案例命名、新增签名时即为 int detail 的真 isinstance 分支（ZCode 报 bounded reason validation，DSH 报 unusable refusal record）与检查点条目 id 空判两分支，diff 仍为空。两轮实验分别在新目录（含提取命令、驱动、双侧原始输出与 diff），未覆盖任何旧文件。

## 变异证据（隔离单点，全部在新目录以新文件名重跑）

三项必需变异各为 `session_receipts.py` 单点改动，先红后绿，命令与源码改动说明存各目录：变异一（完成回执身份与 inputSha256 比较移除）命中公共 `ReceiptTests.test_bridge_receipt_is_bound_to_input_identity_and_outcome`、zcode 接缝见证与 DSH 完成接缝见证，3 项全红、恢复后全绿（`<任务根>/m/s5c-mutation-1-finish-identity/`）。变异二（拒绝信封签名比较移除）命中公共 `ToolRefusalEnvelopeTests.test_tampered_foreign_or_unbounded_envelopes_stay_fatal` 的 changed detail/changed signature 用例（两登记束下均失败）、zcode 接缝见证与 DSH 拒绝接缝见证（`<任务根>/m/s5c-mutation-2-refusal-signature/`）。变异三（问询回执身份比较移除）命中公共 `InquiryReceiptTests.test_forged_stale_or_malformed_receipts_are_rejected`（两登记束的"同钥异身份"隔离用例均失败）、zcode 接缝见证与 DSH 问询接缝见证（`<任务根>/m/s5c-mutation-3-inquiry-identity/`）。同一公共实现守住 DSH 和 ZCode：每个变异的红日志同时含两个 harness 的接缝见证（各自的 `NativeError` 路径）与公共防护。上一轮变异三首轮日志被同名复写的违规披露原样保留：旧目录未恢复、未删除、未追查其他对象，本轮证据全部来自新目录的新文件；自本轮起每次实验均用新目录与新文件名。

## 测试迁移与编号对账

共同契约测试 14 项在 `tests/python/buddy/harnesses/test_session_receipts.py`（类名与方法名不变）：ReceiptTests 3（完成回执，无规则参数）、InquiryReceiptTests 3 与 ToolRefusalEnvelopeTests 4（主体调整——公共阶段在两登记束下以 subTest 双跑；原"blank detail"/"oversized detail"两案例移入新增的差别钉子类）、RefusalWireBudgetTests 4（按预算归属在 bounded 束下运行）。新增 `RuleDifferenceTests` 3 项：detail 各自登记的界、非字符串工具名各自的拒绝阶段、inquiryId 各自的空判。未变集合 36 项两侧集合相等（zcode 28 项原生流/根身份/tool 关联/流完整性见证、dsh 8 项工具事实投影），4 项接缝见证保留，`test_zcode_inquiry` 46 项未改未移；装载错误为零。全仓编号收集不执行（只做聚焦验证）。

## 聚焦验证

uv 冻结锁、`PYTHONPATH=<检出>/src:<检出>/tests/python`、`PYTHONDONTWRITEBYTECODE=1`、`TMPDIR`/`BUDDY_CHECKS_TMPDIR` 指任务根 `t/`；未运行完整检查、未调用真实模型、未运行安装版 harness。结果：单元四模块 103 项通过（common 17 + zcode 29 + dsh 11 + inquiry 46）；受影响的端到端消费方 `dsh/test_native_run` 55 项与 `zcode/test_zcode_tool_refusals` + `test_zcode_tool_evidence` 35 项通过；无环境敏感失败。上轮已通过且本轮未触及的运行未重复（对照实验与变异为本轮新增，全部实跑）。

## 孤立项与清理

两协议模块各 7 个重复函数与 `import hmac`、9 个共享常量导入移除（常量本身仍被公共实现与角色铸造侧使用）；`test_zcode_protocol` 不再使用的 4 个预算常量导入与 `refusal_shaped` 导入移除。`ReceiptRules` 四字段全部被核心读取，两登记束被两侧接缝、公共测试与对照实验真实使用，无死字段。如实登记：`dsh/protocol.py` 的 `decode_json` 别名现仅作为 `native_run.py` 的既有导入面保留（模块内已无读取点），未删以免越出可写范围。未使用任何通配符删除，未删除或重建任何旧实验副本，其他会话对象一律未动。

## 边界与声明

未验证项：本项未重复任何原生模型冒烟与安装版 harness 运行；真实 DSH/ZCode 进程内路径由上述端到端 fake/fixture 流覆盖；Linux/Windows 未验证。范围外如实报告：`decode_json` 再导出的保留；第一轮 `s5-mutation*` 旧目录与其中的复写缺口原样留存（本轮证据以 `s5c-*` 新目录为准）；`native_run.py`、`test_zcode_inquiry.py` 等不在可写范围的文件零改动。C-Two 切换未开始。清理：一次性材料全部位于 `<任务根>/m/`（第一轮 `s5-*` 原样保留；第二轮 `s5c-run/`、`s5c-boundary/`、`s5c-boundary-2/`、`s5c-mutation-1-finish-identity/`、`s5c-mutation-2-refusal-signature/`、`s5c-mutation-3-inquiry-identity/`、`s5c-ledger/` 私有台账与两个基线模块参考副本），`<任务根>/t/` 为两轮 `TMPDIR` 与 `BUDDY_CHECKS_TMPDIR`；确切路径只在私有台账与本交付的目录说明中；任务根整体留待 Host 验收后回收，Worker 未做任何清理。
