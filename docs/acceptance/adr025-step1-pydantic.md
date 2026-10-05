# ADR-025 第一步：pydantic 瘦身与 Host 整合记录

2026-10-05，按用户转达的 Claude Code Host 对 `0c8822b` 的验收意见整改。先将 `socu/buddy-core` 的 `eeb5116` 合入为 `45d6d7e`（同时带入该提交之前的 ADR-021/backlog 文档更新）。本记录登记内部实现、审查与验证，第一步整步验收仍由 Claude Code Host 执行；第二步没有开始。

## 格式与公共实现

`pyproject.toml` 增加 `pydantic>=2,<3`，`uv.lock` 已更新。Host 在本实施检出运行 `uv run --frozen` 安装并独立核实 pydantic `2.13.5`。运行格式的 27 个模型及实时格式的 7 个模型均使用同一 `InternalModel`：strict、frozen、extra=forbid，驼峰别名来自 pydantic；字段边界由 Field/Annotated 和 pydantic 校验器描述。每类手写的 `__post_init__`、`to_payload`、`from_payload` 已删除，只保留公共基类的一份构造/wire 校验错误到 BoardError 的转换与 JSON 投影。Python 构造可省略默认值，wire 每一层都要求完整驼峰键集；NativeIdentity 继续采用非空已知键子集。tuple 的 JSON 数组、UTF-8 字节边界、UnknownEvents 的唯一名/总和、kind/payload 对应和事实包的规范投影也保留。FrozenJson 保留一份不可变规范文本与每次读取的新副本，通过 pydantic core schema 参加字段验证与序列化。

根包 `json_codec.py` 统一三项 JSON 机制：拒绝重复成员与非有限数的严格解码（parse_float 同时拒绝 `1e999` 溢出）、计算摘要的规范编码、JSON 解析前的整帧大小上限。原 store/db.py、roles/turn_io.py 的 canonical_json 均改为公共函数导入；Codex、Claude Code、ZCode 的 decode_json 为公共严格解码的别名，调用方既有 ValueError 错误映射保留。独立实测 pydantic 的 JSON 解析会把重复键的最后一个值保留下来，因此输入帧先严格解码再交给模型。源码检查只有一份 canonical_json、一个 object_pairs_hook/parse_constant 严格解码实现和一份解析前帧上限函数。公开 `protocol/schemas.py` 与 CLI 帮助生成没有改动；对本轮改动 Python 路径的 AST 导入比较没有新增跨两侧引用，原第零步历史登记保持原样。

## 删除与行数

删除 `legacy_facts.py`、LegacyMappingTests 的专属映射测试、没有使用方的三个 Catalog 类，以及只服务这些测试的 Claude/ZCode/DSH JSON 夹具。Codex 夹具仍供工具事实包的形状测试使用。角色控制器中的观察与服务类全部删除，保留现行 Worker/Router 准备与执行入口，以及第二步 ZCode 将使用的注册 run 调用点。HarnessRun 的 discover 声明和登记时的 discover 检查延后至发现使用方落地。

按与验收意见一致的 `wc -l` 换行数统计（旧 live.py 末行无换行，故此口径为 712）：

| 文件 | 原值 | 当前 |
| --- | ---: | ---: |
| run_contract.py | 1781 | 665 |
| live.py | 712 | 488 |
| legacy_facts.py | 454 | 0 |
| roles/controller.py | 644 | 195 |
| protocol/internal_models.py（新增公共基类/约束） | 0 | 356 |
| json_codec.py（新增公共 JSON 实现） | 0 | 90 |
| 合计（含新增公共文件） | 3591 | 1794 |

run_contract.py 为原来的 37.3%，比精确三分之一的约 594 行多 71 行。保留在该文件的工具事实包结构检查、五种已有事实包的规范投影及原生错误记录检查是保留原检查内容所需的代码；不能用一个普通字段类型替代这些语义检查。本轮没有将各类的手写 codec 挪入另一文件，公共基类及 JSON 文件的长度已计入总数。独立格式审查也核对了这一点。

## 固定交付与 Host 修正

Buddy 实现微任务 `2d76ac80-d9a1-4f0a-a90d-a7cd483377de` 从 `45d6d7e` 的独立 worktree 交付 `80be134`，sealed artifact 为 `f931221d-0dec-4f99-8bf4-dd6fbd9af598`，输出树 `2f00236` 与该提交字节相等。Host 将它整合为 `fce5469`；唯一冲突在 registry 的 run-only 登记，与 Host 的角色删减线同语义，保留固定交付内容。Host 自己的角色删除为 `d6716d8`，删除孤立测试辅助函数为 `908885f`，均已登记；没有修改 DSH ACP 源码。

预审发现过嵌套模型通过默认值或 snake_case 接受不完整 wire 的退化，实现方在固定交付中用 ContextVar 作用域与共享 before model-validator 修复；311 个结构探针确认修复。独立角色审查确认被删类没有生产引用，并指出的孤立 outcome 辅助函数已删除。独立格式审查又发现 countsByType 的数组被接受，旧格式要求对象。Host 先登记该产物 rejected，再在 `9382c44` 增加 wire 作用域查询与对象约束，保留 Python 构造的 tuple pair；扩展已有逐层 wire 测试，同时钉住 allowedDomains 的严格容器类型。将扩展测试放到修正前的 fce5469 运行得到 1 个明确 assertion failure、0 error；当前 61 个相关测试全部绿，未修改磁盘上的生产源码做故障注入。

## 编号与验证

重新加载 `0c8822b` 同代码的改动前输入：2532 个唯一 Python 编号、166 个模块、无 loader error；同时实读原始 2322 项基线清单，确认它完整包含于该输入。当前为 2497 个唯一编号、166 个模块，原 2322 项全部保留。变化是 36 项删除（21 项未使用角色观察/服务测试，15 项 legacy/Catalog 专属测试）、2 项迁移或改名、1 项新增逐层 wire 防护；对应全部 39 行变化见[测试对照表](adr025-step1-pydantic-test-map.tsv)。其中拒绝外来 usage 包的检查迁移出 LegacyMappingTests，保持断言；Protocol 用例随 discover 延后改名。

第一次完整检查输入为 fce5469，`uv run --frozen python -m hey_my_buddy.cli.checks --jobs 2` 退出 0，Python 2497（跳过 1）/166 模块、Node 110。countsByType 修正后 61 项聚焦检查退出 0；随后对与 `9382c44` 源码/测试字节相同的输入再次运行完整命令，退出 0，同为 2497（跳过 1）/166/110。两个检查根与聚焦根均由运行器正常收尾，Host 只读确认完整检查根不再存在，没有手动清理。实现方报告的 10 个基线环境性失败没有在这两次标准运行器完整检查中出现，不将其解释成源码修复。控制台前端代码没有变化，本轮没有重复前端检查或安装。

Host 保存旧实现的五类请求/结果/实时帧，并核对新 codec 的编码字节往返相等。311 个删键/键名结构变体与旧实现行为全部一致，其中旧实现接受的 2 个 NativeIdentity 合法子集继续接受。1764 个标量/容器变体中有 4 个明确类型收紧：旧 NetworkPolicy.from_payload 会把 allowedDomains 的空字符串、单字符字符串或空对象经 tuple(...) 隐式转成序列，新严格模型仅接受 JSON 数组/null；这些不符合声明形状的值现在被拒绝，其余 1760 个变体一致。该差异已显式记录并加断言，未宣称所有历史宽松输入完全等价。共享严格解码的重复键、NaN、Infinity 和 1e999 拒绝探针通过。

原始材料在 `<repo>/tmp/adr025-pydantic-host/`：before-test-ids.json、original-baseline-ids.json、after-test-ids.json、test-id-diff.json、before-wire-frames.json、wire-structure-probes.json、scalar-probes.json、wire-probe-results.json、counts-wire-before-fix-red.txt、两个完整检查 stdout/stderr、聚焦输出、model-policy-report.json、import-boundary-report.json 与 final-metadata.json。三个编号 JSON 文件的 SHA-256 分别为原 2322 清单 `bd41cc158331a88a5b2cf1899afaae148a57e06e09e215deee9371d7a13d8945`、改动前 `375b8f48f4828c345546420a1b653ba6dca7c2ebe12c36bbacbc0115bdf575ac`、改动后 `a9b2eb66523c5caa49fc5b3d0a426e6893cb4514ba090c65a9ad8a88267bd6db`。本轮开发验收没有真实 harness/model 冒烟；Buddy 的实现执行和两次独立只读代理审查与开发验证分开计。

## 使用方与后续边界

当前生产路径仍走第一步合并后的旧适配器外层；RunRequest/RunResult 及 Live* 尚无生产构造/解码调用，角色层只引用类型/登记边界。34 个模型和各字段是保留给首次 ZCode 使用方的内部格式，ExistingLiveChannel 的后端也尚未进入生产调用；它们不能以测试引用作为生产使用证明。run_harness 同样暂未有生产调用，作为第二步明确的唯一调用点保留。已删除的 Catalog、legacy 转换与角色观察/服务没有留兼容入口。ZCode 真实运行走通后须逐字段、逐类报告未使用内容并删除；之后才并行接 Codex、Claude Code 与 DSH，每步继续报告，到第四步结束不得剩余无生产使用方内容。

用户本次转达的 ACP 客户端 71 项检查和已安装 DSH 无模型握手已通过，已据此对原 ACP 微任务和既有精确整合记录完成内部 acknowledged accepted，详见[ACP Host 收尾登记](adr025-dsh-acp-host-fixes-host.md)。公共接线仍等待第一步整步验收。当前提交后停下等 Claude Code Host 复核行数、测试编号、重复与依赖；通过前不开始第二步。


收尾整合已登记 `int-4b4a1962-7523-49df-bc9f-8bc9d818bf05`，绑定固定 artifact 与目标提交 `72fcf1e`，明确列出 Host 修正的 run_contract.py、internal_models.py、test_run_contract.py 三条路径及四条 Host 文档。accepted 回执登记先拒收后的 Host 修正、红/绿及最终完整检查，并更正整合验证说明中的“21工具”笔误：实际为 canonical_json 与严格解码各一份实现。服务在内部 acknowledged accepted 后自动回收该受管实现 worktree；Host 后续读取 cleanup 记录确认 state=applied、removed=true，固定产物/引用/patch 保留，未再次删除。该内部收尾不代替第一步外部验收。
