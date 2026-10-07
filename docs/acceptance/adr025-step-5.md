# ADR-025 第五步：C-Two 实时通道

实施分支 socu/adr025-run-module；输入43d8d63，第三四步已由Claude Code Host验收。本步已合入ADR-027的9b65abbc与仅待办文档变化的54d15908；解决Codex/DSH冲突保留共同运行阶段、accountStatus、未列出所选Codex模型的核对放宽与selectedModelListed事实。日常运行时尚未安装或升级；本记录完成后仍须Claude Code Host外部验收。

公共代码与角色接缝由Host整合，微任务首次均走路由、不指定buddy，隔离worktree与唯一可写范围在任务描述中登记。范围内问题原run continue，已接受C3不可继续及D3准备检出失败的两项Host更正经用户单次授权。清理只由Host按创建时记下的确切根进行；Worker不删除、不用stash、不创建/切换分支、不移动/删除分支标签；受管检出与用户仓库共用Git状态的边界已写进后续描述。实际run、路由、固定产物、拒绝、Host改动、验证与回收明细见[整合登记](adr025-step5-integration.md)。

## 交付

先完成清理：无调用方的structured_call旧启动/结果fallback、read_router_result、before_try与可省略stop的收集入口删除；ZCode/DSH三类回执核验集中到session_receipts；四个运行模块复用native_support，ZCode拆为运行阶段；角色中的厂商判断改为登记参数，BUDDY_PYTHON无生产读取方已删除。回执完成身份、拒绝签名与问询身份三处单点破坏都由实际测试抓住；两个始终关闭的DSH启动行以literal行名验证，两层停止不确认时不能续接，Claude交付识别的无消费者开关删除。

每个controller运行登记一个临时C-Two端点，地址由server_address回读，随机人名仅显示。Worker运行时先设置server/client再注册自己并连接controller；服务只通过Worker转达，内存登记重启后由续租/重协调刷新。原生owner保有问询日记与决定权，RPC线程只有有界队列，提交与回答只有真实owner持久提交后才报告。三个具名实时操作共用完整身份、窄token、实例、有界帧与时间窗；不增加角色专用通道。旧raw inquiry transport、ExistingLiveChannel及ActivitySidecar实时读写入口退休，持久回执和停止判定保持独立。

新增内部worker_live_attach/detach改变BuddyControl契约，按ADR-007将CONTRACT_VERSION由0.28.0改为0.29.0，SQLite schema仍15。实际隔离C-Two peer同代两方向成功，异代两方向ERROR_CONTRACT_MISMATCH；这是完整契约声明与运输ping fixture的核对，不宣称SQLite升级或业务端到端。服务/Worker/控制器须按同一版本升级，CLI公开请求校验与帮助生成不改；安装由外部验收后另行授权。

## 测试编号与消费者

原步输入2785个编号；最新已验上游2874个；最终候选3049个唯一编号、189模块、装载错误零。原输入未变2712个的集合相等，73个旧编号变化/退役与337个新增逐项登记，含上游ADR-027的1个旧改名与89个新增ID差异；相对最新上游为72旧/247新。只列实际编号差异的主表是[整步编号表](adr025-step5-test-id-delta.tsv)，原始清单/集合与计数核对留<checkout>/tmp/adr025-host/<phase>/step5-final-id-accounting1.json。微任务表中的同编号断言机制迁移不算ID变化，防护迁移的真实故障日志与范围边界各自保留。

没有生产使用方的字段、类、参数清单：空。Host与独立只读复核逐组追踪构造、实际读取、校验与公共投影，45个含字段模型/190字段见[消费登记](adr025-step5-format-consumers.tsv)；补充AST实参读取仅用于本项目实现，不对厂商代码做静态证明。formatVersion由实际Pydantic版本校验消费，嵌套事实由真实公共投影消费，descriptor与清理结果由holder和Worker日志消费。

两侧跨边界导入重新计算为56条：黑板→buddy37、buddy→黑板15、protocol→黑板3、protocol→buddy1；第零步54条为33/16/4/1。同一脚本重算已验第零步逐项等于原表，本步增加6、减少4，完整[当前表](adr025-step5-cross-imports.tsv)与[增删表](adr025-step5-cross-import-delta.tsv)登记，不顺手改其他边界。

## 原生与端点证据

| 核对 | 本步原生最小运行次数（不含微任务执行） | 事实与边界 |
| --- | ---: | --- |
| Claude Code | 2 | 用户额外授权的Worker最小回合1、审阅1，使用已有登录，成功；2e6c2db时的运行格式/StructuredOutput证据，早于C-Two切换，不当作最终通道端到端；没有再调用。见[冒烟记录](adr025-step5-claude-native-smokes.md) |
| DSH | 2 | ACP驱动已安装0.1.5-rc.1，两个最小Worker回合真实签收/根匹配、read工具、问询answered、原生group gone与外围确认、token用量；第二回合保留了两个端点确切路径，见[冒烟记录](adr025-step5-dsh-native-smoke.md) |
| ZCode | 1 | 0.16.9、zai-api/GLM-5.3-Flash/max的最小Worker回合，真实Read、问询回答、完成签收、两层停止与两个端点前后事实通过；用量覆盖partial，见[冒烟记录](adr025-step5-zcode-native-smoke.md) |
| Codex | 0 | 本步没有额外原生冒烟，既有第三步证据与本步fake native/C-Two测试分别记录 |

实际DSH补充回合98f9a75b/712ef86f的Worker与controller各记录一个确切socket文件，从运行期间存在到持有Worker结束、两层停止确认后消失；两个精确路径和前后布尔事实留原始证据。首回合漏存Worker地址的缺口保留，不能改写为已证明。实际无模型Worker注册/停止和两个真实ZCode fixture controller也各核对自己的端点文件消失，六类真实peer场景补充正常/异常/失联、身份、队列与仅清自己捕获文件的守卫。没有按日期/名字/无人引用推断归属，没有清理其他/tmp/c_two_ipc残留。

最终合入生产源码离线wheel347项，顶层只有hey_my_buddy与dist-info，无buddy兼容namespace或退休入口；七个关键实时/原生模块逐字等于源码。按uv.lock在独立私有环境安装依赖及wheel，隔离导入四个run与两内部操作通过。没有依赖tests/python的import结果判断wheel顶层；私有构建/安装及版本互验原始输出留tmp。

## 检查状态

第一次最终整合检查默认4并行、189文件全部调度、186文件2981项通过（skip1），3模块失败，480.607秒；失败没有省略或视为通过，后续尾部检查尚未执行。两处范围外旧引用修复的聚焦2项通过；Codex取消时序在原5-D2 run返修，要求真正的身份绑定原生开始事实，不以固定sleep替代。最终整批结果将在返修固定审查与整合后补入；本记录目前不作为第五步完整通过或外部验收。

## 对外变化交给文档维护者

用户维护的ADR、CONTEXT、AGENTS、README、待办与参考文档没有由Host改写。需要其更新：实时活动/问询只用C-Two，文件只留事实证据；服务到运行的请求由Worker端点转达；临时端点从地址路由、同名只影响显示；未注册/失联如实报告不可观察且不构成停止；旧raw传输头与封闭错误码、旧clamp语义不保留，新的严格时间窗/格式与owner错误事实按公共模型报告；0.29契约需要协调升级，schema仍15。DSH所有hey-my-buddy启动的运行关闭session-title-llm/session-telemetry-otel已接受差异延续，用户自己交互使用DSH不受影响。
