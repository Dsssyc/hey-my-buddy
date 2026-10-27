# 宏任务项目身份补修：源码核对（2026-10-11）

本记录是从 `6758626d` 出发的源码与私有测试证据，等待 Host 验收，不是安装或日常看板恢复的记录。

宏任务的 `project_id` 来自执行 manifest 的 `repositoryId`，旧算法是 canonical Git common-directory 路径、设备号和 inode 的 JSON 数组摘要；新算法去掉设备号。`project_path` 是提交 cwd 的展示值。旧迁移从 `workflow_runs.workspace_manifest_json`、children/continuations 的同类字段、artifacts 的 `manifest_json` 和 turns 的 `input_json.executionWorkspace` 取固定 manifest，却先检查其中的 `path`，即执行 cwd；受管检出通常位于 `<state>/workspaces/<workspaceId>/checkout`，并非源仓库的 common Git directory。accept、conclude 和 storage 的受保护回收可以移除这个检出，保留 manifest、输入、引用和收据；于是源仓库仍在，旧迁移也会把该 manifest 关联的仓库身份一并记成 `path-missing`。私有回归保留源仓库、移走执行检出，在修复前得到相同的宏任务提交冲突，在修复后接受原 `objectiveId`。没有读取日常看板，不能指出其中某条路径的实际删除收据或断言它具体由哪个回收动作移除。

第一版的 `fixed-evidence-unverified` 是宽泛的失败出口：Git inspection、固定输入或引用检查等抛出的其他 BoardError，以及未单列的文件系统/结构异常都会落到这里；它本身不证明仓库 inode 变了。`no-recorded-evidence` 是已收集的索引/manifest 身份没有成功候选、也没有明确失败记录，例如只有 `sourceCheckoutId` 而没有可用的独立 source inspection。新报告区分 common directory 缺失与执行 cwd 缺失，补充固定输入和执行检出失败的原因；参考文档解释各原因。

修复在 `migrate_workspace_identity` 的同一个离线事务步骤内完成：有效且绑定校验通过的 manifest 提供 `snapshot.repositoryPath`，用现存目录的 canonical path/inode 和 `legacy_device` 的设备搜索精确重建旧摘要，再登记原格式的别名并规范宏任务索引。执行检出仍走固定输入、引用、分配与锁的证明；提交的身份比较和 Host 检查未放宽。删除旧报告的永久 `path-missing` 否决，让下一次显式升级重新取证；没有摘要匹配就保留并拒绝。别名和报告仍是同一 version-1 格式，SQL schema 不变，没有第二套读法或读取时换算。`install/upgrade.py` 已在 verified backup、可选的 15 → 16 SQL 转换之后、私有目录迁移和新服务验证之前，无条件调用这一身份步骤；因此已经 schema 16 且有第一版报告的看板也会被补修，不需要添加历史 SQL 转换或升级入口。

首轮源码上运行以下受影响测试，75 条全部通过（111.201 秒，退出码 0）。覆盖原宏任务再次提交、第一版 schema-16 报告补修、无法重建摘要及无记录证据时的拒绝，并保留原有 inode 替换、绑定损坏、引用/分配锁、竞争写入、事务回滚、不可变记录保留与幂等性保护。冻结 schema-15 fixture 的转换仍与新建 schema 一致。运行中出现未关闭测试数据库的 `ResourceWarning`，没有测试失败；未扩展到测试基础设施清理。首次联网获取依赖受限，最终使用本地缓存与 `--no-sync`，已核对全部实际依赖版本与 `uv.lock` 相同，且身份迁移模块导入自本检出的 `src/`。

```sh
env -u BUDDY_STATE_DIR -u BUDDY_RUNTIME_ROOT -u BUDDY_RUNTIME \
  -u BUDDY_RUNTIME_IDENTITY -u BUDDY_WORKER_STATE -u BUDDY_WORKER_ID \
  -u BUDDY_AGENT_CREDENTIAL -u BUDDY_AGENT_CREDENTIAL_FILE \
  -u VIRTUAL_ENV -u UV_PROJECT_ENVIRONMENT \
  UV_CACHE_DIR="$PWD/.uv-cache" PYTHONPATH=src:tests/python \
  uv run --offline --frozen --no-sync python -m unittest \
  install.test_workspace_identity_migration \
  blackboard.tasks.test_workspace_identity blackboard.tasks.test_objectives \
  blackboard.store.test_migrations.Schema15UpgradeTests.test_fixed_fixture_migrates_to_the_complete_fresh_schema_before_any_opener
```

首轮没有跑完整检查，没有安装、启动/停止服务或 Worker、调用真实 harness 或模型，也没有读日常看板或核对整代切换。题给的 42 个宏任务共用两个旧仓库身份：若它们能用各自保存的 common-directory 路径、当前 inode 和搜索范围内的设备号重建，42 个会一起接上；固定引用或执行分配的失败不再阻断仓库证明。但旧标签没有指出实际失败点，不能单凭它保证两个摘要必然可重建，尤其不能排除 inode/路径变化或设备号超出搜索范围；确认依据是后续获授权升级中这两个身份的实际映射结果。

## Host 打回后的返修核对

Host 接受身份修复方向，打回 `test_upgrade_fixture` 的旧接口用法。本回合先复现其 8 项错误，再把 `proof` 辅助函数改为解包 `_proof` 的证据与拒绝原因，并新增拒绝原因为空的断言；原有摘要、inode、固定引用、源输入和非空证明断言保留。同一文件另有一处仍期待 `_proof` 在固定引用损坏时抛异常，改为明确断言执行检出不获证明、拒绝原因为 `fixed-evidence-unverified`、仓库仍获独立证明；原有 `_fixed_refs` 异常断言保留。单独运行该模块，6 条全部通过（17.523 秒），随后指定模块批次中再次通过（18.309 秒）。本回合没有再改生产代码。

搜索了整个 `src` 与 `tests/python` 中 `_proof`、`_plan` 及迁移报告的使用处。其余 `_proof` 使用处是正确解包的迁移计划与只统计调用的测试包装；`_plan` 仍返回四项，调用处一致。报告仍为 version 1 的 `entries`/`kept` 结构，Python、SQL 和缓存读取处没有旧形式要替换；故未新增兼容接口。

按 Host 指定运行 14 个模块，每个模块使用独立 unittest 子进程，最多并行两个，清空首轮命令列出的继承凭据与运行时变量，另设 `TMPDIR="$PWD/.venv/test-tmp"`，所有状态、运行时与临时路径均在检出内。共执行 180 条；9 个模块的 139 条全部通过：`blackboard.store.test_upgrade_column_order`、`blackboard.store.test_upgrade_fixture`、`blackboard.tasks.test_objective_summary_cache`、`blackboard.tasks.test_workspace_identity`、`install.test_source_identity`、`install.test_upgrade`、`install.test_upgrade_migration`、`install.test_workspace_identity_migration`、`cli.test_repository_hygiene`。其中身份迁移模块 46 条通过，退出码 0；若干模块仍有测试数据库 `ResourceWarning`。

其余 5 个模块没有通过。`blackboard.tasks.test_console_objective_fixture`、`blackboard.tasks.test_objective_stop`、`console.test_console_objective_cache` 在私有 HTTP 端口的 `socket.bind` 收到 `PermissionError: [Errno 1] Operation not permitted`；上层将它转成 `CONSOLE_PORT_IN_USE`，底层证据是沙箱禁止绑定，而非端口已被占用。Host 指定的 `install.test_upgrade_service` 启动私有测试服务子进程后也因同一绑定失败提前退出，未碰日常服务。`blackboard.tasks.test_objectives` 有两条写入互斥错误，单独重跑 23 条仍有同样两条错误：检出内临时 cwd 被 `MockWorkspace._repository_root` 向上找到本检出的 `.git`，本应独立的模拟目录因而被归为同一 checkout；没有放宽互斥规则或改动测试替身来消除它。

当前没有达到 Host 要求的 14 个模块全部通过条件，需要 Host 在允许私有回环端口、且私有临时根不处于任何 Git checkout 下的环境，对本回合封存源码重跑上述 5 个未通过模块。没有跳过测试、替换真实 HTTP/RPC 断言或尝试绕过沙箱。记录更新后单独重跑 repository hygiene，5 条通过；`git diff --check` 通过，改动均在写入范围内。仍未跑完整检查、安装、调用真实 harness 或模型、读取日常看板；Host 最终验收与整合核对待完成。

Host 此次提供了额外只读事实：35 个与 8 个宏任务所在组的旧摘要能由仓库的 Git 公共目录重建。这支持代码可恢复原 42 个中的 35 个及另 8 个，但本回合没有自行核对日常数据，也不能据此保证原 42 个中的剩余 7 个；它们对应的另一个旧摘要仍须获得机械证明。

Suggested commit message: `Fix repository identity recovery during offline upgrades`.
