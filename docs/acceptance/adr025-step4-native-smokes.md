# ADR-025 第四步：DSH 原生与分发验证

本记录区分原生运行结果、Host 探针留证是否完整和整步验收。核对使用用户已经安装的 DSH；经新 Python 控制器与 `dsh --profile acp`，没有 DSH Python SDK、日常运行时安装或升级，也没有读取凭据文件内容。每次 DSH 启动均有私有 `DSH_HOME`；真实运行保留原有 `HOME`，公开配置导出另设私有 `HOME`。真实认证由 DSH 经既有只读指路配置自行取得，Host 没有复制凭据或改登录。以下模型调用均属于已授权的第四步冒烟，原始脚本、摘要和具名证据清单在 `<checkout>/tmp/adr025-host/<phase>/`。

## 原生运行账目

| 核对 | 真实 prompt 次数 | 实际结果 |
| --- | ---: | --- |
| Worker 初次 | 1 | 新运行模块返回成功，完成工具已签收，有 read 工具事实和完整 token 用量；探针保存摘要时误改输出路径，外层停止布尔值未落盘，不能作为完整两层停止验证 |
| Worker 修正后最小重跑 | 1 | 5.208 秒，运行请求/结果/持有方身份相等，完成工具签收有效，read 工具 1 次，原生进程组 gone，控制器停止和组合停止均 true |
| 只读预设的命令写入 | 1 | 4.035 秒，原生 bash 实际执行 `printf ADR025_COMMAND_WRITE > ./command-write.txt`，工具结果包含 `Operation not permitted` 与 `file access denied under read-only mode`；目标文件不存在，原生组 gone |
| 不给工具 | 1 | 15 个工具提供行、plan-mode 与两项始终关闭行组合生效的真实运行；工具调用计数 0，目标文件不存在，原生组 gone，模型返回未尝试工具。这个结果与公开配置导出共同作为证据，不把模型自述当作能力证明 |
| 公开组合配置导出 | 0 | 私有 HOME/DSH_HOME、`--dump-config`，退出 0、进程组停止；下文列出的 18 个目标行均为 disabled |

共 4 次真实 prompt（其中 1 次为留证脚本修正后的必要重跑），配置导出没有 prompt。配置均为 `deepseek-official / deepseek-flash / low`，ACP initialize 回报版本 `0.0.1`；该值是 ACP agent 的版本，不冒充 DSH 启动器版本。第一次探针没有改写或删除，其错误是把检出路径中的 `.codex` 也替换成 `.dsh`，直到收集完成后保存摘要才失败；原始结果与回合记录仍在本次私有根中。第二次探针修正路径并加输出目录与文件不存在性断言，才重新调用模型。

修正后的 Worker 由公共角色执行器启动 `hey_my_buddy.buddy.roles.run_controller`，经 `RunRequest` 与 `RunResult` 完成；`buddy_finish_turn` 的 receiptVerified=true，原生 session/close 已确认，回合完成签收与两层进程停止分别记录。完成工具作为已核验的交付调用从任务工具计数中排除，read 的 start/end 仍在工具事实中。公开签收由外部 Host 之后完成，本记录不把原生完成等同于宏任务验收。

用量读取没有退步：修正后的 Worker 从本次私有 zstd 会话记录取得完整 3 条原生计数，inputTokens=30,177、cachedInputTokens=20,608、outputTokens=401、reasoningOutputTokens=112，inputBasis=includes-cached；只读回合 2 条计数，不给工具回合 1 条计数，也均可读。使用项目锁定的 Python zstandard，不调用系统 zstd 命令。记录缺失、外来、截断和读不到仍是未知，由既有假 ACP 测试覆盖；本次成功不能外推到未见过的未来 DSH 记录格式。

## 工具范围与行为差异

公开配置导出包含 16 个 `tool-` 行，其中 `tool-result-pruner` 是已登记的上下文剪枝插件，本次没有新工具提供行。其余 15 行为 tool-bash、tool-pwsh、tool-jobs、tool-fs、tool-fs-search、tool-skill、tool-subagent-control、tool-subagent-list-agents、tool-subagent、tool-subagent-fork、tool-workflow、tool-todo、tool-goal、tool-ralph、tool-web；不给工具时这 15 行连同 plan-mode、session-title-llm、session-telemetry-otel 共 18 行均 disabled。仅按公开组合配置与实际事件核对，不静态证明厂商实现；清单外的新工具仍保留为事实，不能被称为已经限制。临时提取器第一次只按 tool- 前缀列出清单外的 pruner，第二次归入此前已登记的非工具提供行，原始导出不改写。

只读实测证明本次 bash 重定向受到原生只读沙盒限制，补上前期只验证写入工具的缺口。没有发生升级权限请求，也没有重试或放宽权限；用户提示词要求一次尝试后返回。工具结果是原生会话记录中与该 callId 对应的 `tool/result`，不只依赖最终文字。共享词表补入实际原生名称 `bash → execute`，未知名仍为 other；这只是分类，DSH 的 systemSandbox 能力与黑板审阅准入没有在此改写。

五项已接受行为差异逐项继续成立：问询经合作检查点送达；快速调用多出 DSH 系统提示词；原生续接可以成为新能力但本次没有启用，仍以新会话重建；第四项是所有由 hey-my-buddy 启动的 DSH 关闭 session-title-llm；第五项是同一范围关闭 session-telemetry-otel。后两项经本次私有启动 patch 应用，用户自己交互使用的 DSH 不受影响。不给工具的私有会话记录中 title 来源为 fallback。没有新增已发现而未批准的行为差异。

## 私有分发验证

固定生产基线 `6a800c2` 构建新的 buddy 分发包，并把包内项目按冻结锁文件物化到独立的 `<package-probe>/runtime/`。该运行时的解释器成功导入新 DSH 注册模块；运行时身份中的包、资源均在私有物化根内，sourceLeaks=[]，资源只含 console.assets，旧 dsh.runner 与 yaml_bridge 均不存在。实际依赖为 pydantic 2.13.5、zstandard 0.25.0；没有触碰日常安装，也没有服务或模型调用。整步完整检查与最终测试编号另行记录，不能用这次包验证替代。

## 未消费格式收敛

Host 按[第三步格式使用表](adr025-step3-format-usage.tsv)逐组处理剩余 18 项，去向见[第四步格式使用表](adr025-step4-format-usage.tsv)。InterruptEvidence.requested/basis 保留真实 Codex 消费；其余未消费组成部分删除。ModelStartEvidence、DeniedInteraction、UnknownEvents 三类删除；model_started、角色 observer 的未知事件与拒绝统计、带摘要的原始签收/拒绝/ACP 证据、工具事实及实际组停止判定均保留。RunResult.native_identity 只保留实际消费的会话与回合；工具证据内的原生身份集合使用原契约，不随这个内部值精简。RunEnd.native_exit_code 继续承载真实退出码，StopLayer 只承载 group_state，不再重复退出码或存下无人读取的观察口径。

请求中的两项 Claude 原生控制值（network_allowed_domains、additional_denied_tools）已有启动配置消费；其余请求字段、结果事实包和实时通道字段沿用第二步逐字段登记的真实读取，四个生产组装方均适配本次删除。公开 CLI schemas 和 C-Two 契约没有变化，CONTRACT_VERSION 不变；第五步的通道契约还未开始。原生测试的语义迁移与整合结果以独立交付及整步记录为准，当前记录不宣称全量检查已经通过。
