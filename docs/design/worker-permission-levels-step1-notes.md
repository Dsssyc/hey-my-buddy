# Worker 权限档位第 1 步说明

## 实现前清单

原来的选择存在 `workflow_runs.execution_workspace_json.access`（提交意图）、`workflow_runs.workspace_manifest_json.access`（准备后的当前检出）、`workflow_continuations.workspace_manifest_json.access`（已准备的继续）、`workflow_turns.input_json.executionWorkspace.access`（冻结输入）；`workspace_reservations.access` 记录检出的读写占用。`normalize_execution_workspace` → `WorkflowCoordinator.submit` → `attach_governed_task` → `begin_turn` → `_turn_workspace` → `run_execution` → `NativeSchemaWorker.tool_scope` → `RunRequest.tool_scope` 是回合路径。DSH 与 ZCode 的 Worker 分支现在直接取 write；Codex 的角色格式也直接取 write，只有 Claude 的 Worker 格式已跟随 read。

新增 `workflow_turns.access` 和 `workflow_turns.access_reason`：前者是该回合冻结的权限档位，后者是该次 full 选择的理由，非 full 为 NULL。直接放在回合行，因为两者是一回合的标量属性，不另建一对一表，也不写 meta。微任务当前选择继续放在已有结构化的 `execution_workspace_json`；继续修改这个当前意图，不改已经冻结的回合与封存记录。换档新增 `workflow.access_changed` 事件，含前后档位、理由与操作身份。

扩展唯一待做的 schema 16 → 17 升级，保持 schema 17；由冻结的基准看板重建 `workflow_turns` 到新结构，逐行按所属 run 的原来 `execution_workspace_json.access` 换算，新理由列为 NULL，保留其余列、索引、输入哈希及历史证据。此前不能换档，所以这是准确换算。升级计划对改变的表做精确行比较，对其余表继续用现有精确指纹；测试继续比较升级与新建的完整 schema。

继续时的权限选择进入既有 workspace preparation 路径，由新意图重新生成带正确档位与摘要的 manifest，更新 reservation；旧回合保留旧 manifest。full 具有写占用，沿用现有占用排他规则。资格沿用 `requiredCapabilities`：声明 `permission:read`、`permission:write`，没有 harness 声明 `permission:full`。当前 Worker 实际支持 read 的只有 Claude；Codex 与 DSH 原生运行模块有 read 分支，但 Worker 角色路径目前没有使用它，不能仅凭 Router read 支持宣布 Worker 已支持；ZCode Worker 使用 yolo。

## 范围与待验证

不改变四家权限参数与网络设置，不改控制台、skill、README 或 reference。接口理由长度定为 1000 字符，拒绝空白与多行；full 必须给理由，其他档位不许给理由。原生运行入口未实现 full 时直接拒绝。受影响测试使用 Host 指定的只读解释器与私有 HOME，清除 inherited runtime、Worker 与凭据环境变量；完整检查、启动器、套接字及共享内存、真实回合由 Host 验证。

## 实现记录

回合列采用共享 `WORKFLOW_TURNS_DDL`，升级与新建引用同一份定义；升级以原来的 run 意图回填，并精确比较新行，触发器破坏与重建后失败仍回滚整次升级。schema 保持 17，没有追加第二条升级，也没有改基准看板。未改旧回合的 `input_json`、哈希与封存证据。

`continue` 的幂等请求仅在明确给档位时加入 access/accessReason；沿用时不改变既有无档位请求的哈希。选择与换档事件在同一事务内提交，失败不会部分改写；继续准备使用新的当前意图，按新的 manifest 摘要更新占用。`workspace_reservations.access` 的含义仍是物理检出的读或写占用，full 映射为 write，未扩展其存储取值。完整回合视图从新列读取 access/accessReason；CLI 简要视图增加当前 access/accessReason。

目录发布从适配器已有的 `capabilities` 复制 permission 字符串；路由给既有 requiredCapabilities 增加所选档位的字符串，保留调用方的其他能力边界，仍由既有 SQL 候选筛选执行。指定配置与继续用同一适配器声明检查；拒绝包含 adapter、access、requiredCapability 和 missingCapabilities。command/external 的原有 read/write 调用方执行约定保持；full 不由它们声明。

删除角色格式的 follow_workspace_access 开关和 Codex 固定 write 的断言，所有 Worker 的明确输入档位原样进入 RunRequest；四家的原生权限映射与 Codex 网络设置没有改。未实现 full 的四个原生 run 入口在启动之前明确失败。旧测试直接插回合的 SQL 补上明确的 access；身份与占用测试的 DSH 只读夹具明确临时声明未来的只读能力，其结果不构成真实 DSH 的资格声明；问询测试改用已经声明的 write。

## Host 需要更新的拥有页面

`docs/reference/workflow.md:109` 的 `access (read or write)` 已过时，需补 full、理由与继续换档；同页 prepared manifest 字段列表需补 full 时的 accessReason，回合读取说明需列出冻结列。`docs/reference/cli.md:85` 的 `access: read|write` 与只讲提交时选档的说明已过时；同页 get 简要字段表和 continue 输入字段说明需补当前档位、理由及每个回合的冻结档位。`skills/buddy/SKILL.md:23` 的继续步骤只提 configuration/reroute，需要补权限换档与 full 的理由；两份 Host 指南和各 harness 的权限参考由 Host 按执行计划统一更新。`docs/decisions/023-harness-integration-principles.md:5` 的“这一部分尚未实现”需由 Host 缩成尚未实现的 harness 落法；不能宣称本步完成四家的原生 full。`docs/reference/cli.md:87` 的 `executionWorkspace retains the original intent` 也需改为当前意图，原始选择仍由固定输入与提交记录保存。README 未修改。

执行计划中的时间轴结束判定、准备失败原因展示是顺带待办，本次冻结的七条实现要求没有交付它们，交由 Host 确认其独立安排。控制台、skill、README、reference、用户 harness 配置、账户、已安装运行时和服务均未修改。

权限资格 helper 放在已有 harness registry 内，workflow 沿用其已有 registry 导入边界，没有新增一条黑板到 buddy 的跨边导入。路由边界对 full 也按 write 占用检查；换档后第一回合尚未开始且没有封存输出时，继续准备保留原来明确选择的未跟踪输入，不会因只换档丢失输入。两条路径都有独立真实 Git 测试。显式 access 的 null、空白、未知字符串和非字符串均拒绝，只有省略 access 才使用默认 write。

## 实际验证

使用 Host 给定的只读解释器，不运行 uv 或安装依赖；每批测试先清除继承的 runtime、Worker、agent credential、虚拟环境变量，再提供新建的私有 HOME、CODEX_HOME、CLAUDE_CONFIG_DIR、DSH_HOME、ZCODE_DATA_BASE_DIR 与测试状态目录。PYTHONPATH 用本检出 src 与 tests/python 的绝对路径，使假程序切换 cwd 后仍能导入当前源码。所有 native 结果均为测试夹具，没有模型调用。

- 协议、角色、运行约定、迁移、目录、评估、路由权限、换档、workspace API、CLI 视图组：179 项通过；后续收窄原生改动后角色与运行约定组 82 项通过；显式 null/空值验证与换档/运行约定/帮助组 43 项通过。
- 真实 Git workspace 与 workflow_real：37 项通过；生命周期、工作树身份、工作树身份升级组：100 项通过。
- workflow_routing、workflow_preparation、continue_catalog_trust、workspace_batch、objective_summary_cache、live_registry：129 项通过。
- inquiry、CLI 帮助与 governed 参数帮助：39 项通过。
- 安装升级协调器的 schema/backup/restore/identity 路径 `install.test_upgrade_migration`：16 项通过；固定基准看板 `blackboard.store.test_upgrade_fixture` 的 4 项在同批检查中通过；`blackboard.store.test_migrations` 包含升级与新建 schema 完整比对、历史回填、触发器损坏及回滚，已在前述组中通过。
- 最后取消 continue 事务内重复的能力检查后，换档/拒绝/沿用/首次领取前换档的 4 项再次通过；读取时的 revision fence 仍核对校验时的目标版本。

初轮发现的失败已分别解决：旧 SQL 插入缺少 access、只读占用夹具没有声明只读能力、升级协调器仍把新增列后的完整回合元组与旧元组直接比较。后者改为按升级前的完整列清单比较历史值，再核对新增列；没有删除历史保留断言。两个运行环境问题也已解决：相对 PYTHONPATH 导致换 cwd 的假程序找不到测试模块，以及问询夹具需要一个新建的私有 BUDDY_STATE_DIR；修正私有测试环境后对应整个文件通过。未把这些失败归因于真实 harness，也未修改运行映射来绕过它们。`git diff --check` 通过。

完整检查留给 Host。尚未运行的验收包括日常看板副本上的升级演练、空 HOME/不继承状态目录/经安装启动器的提交—换档—读取，以及真实 harness 回合和故障注入。相关真实 RPC/启动器检查请由 Host 跑：`install.test_upgrade_service.UpgradeServiceTests.test_real_private_service_reads_the_fixed_upgrade_and_uses_public_host_conclusions`、`install.test_local_launcher`、`install.test_runtime`、`install.test_first_install`；本回合没有启动它们，也没有尝试套接字或共享内存探针。前端未改，未运行前端检查；任何本步源码测试结果都不是已安装运行时或 Host 验收的证明。
