# ADR-025 第四步提交 Host 验收

DSH 已由 Python 控制器经用户已安装的 `dsh --profile acp` 接入公共运行请求、结果与角色接缝；Worker、快速调用和发现复用登记的运行模块，问询使用公共实时接口的合作检查点。旧 Node 运行器、插件、安装资源、YAML bridge 和 Python 旧入口均已删除，没有兼容层或 DSH Python SDK。原 4-B1 使用同一个 run 继续完成工具清单与用量读取，内部验收、固定交付和公共整合见[整合登记](adr025-step34-integration.md)。此记录提交验收，不代表 Claude Code Host 已验收。

最终完整检查基线 `6078488eb553ddadcf5ef15f42a9c110696fb126`；`uv run --frozen python -m hey_my_buddy.cli.checks` 使用默认并行数，实际 4 个并行进程，未传 `--jobs`：Python **2,785 / 175 模块**（既有 skip 1），**434.254 秒，退出 0**，检查前后 HEAD 相同。原始日志与元数据保留在 `<checkout>/tmp/adr025-host/<phase>/step4-full-check2.log` 和 `step4-full-check2-meta.json`。首次完整检查的 15 个失败已按边界迁移旧测试接缝并修正打包断言，原失败记录保留；修正批次的这次完整检查也作为整步结束检查。

相对第三步 2,790 个 Python 编号，2,728 个不变、62 个旧编号离开、57 个新编号进入（含改名），去向逐项见[编号变化表](adr025-step4-test-ids.tsv)。两侧扣除列明变化后集合相等，最终收集没有装载错误或重复，表中 Python 目标编号均在实际集合内。旧 Node suite 的 110 个叶子意图按 [101 行展开表](adr025-step4-node-retirement-test-ids.tsv)逐项登记迁移、已有覆盖或随载体删除；Node suite 已退役，不将它描述为空套件通过。防护迁移及实际故障结果见[原生格式](adr025-step4-format-tests.md)、[Worker 流程](adr025-step4-workflow-tests.md)、[实时通道](adr025-step4-live-tests.md)、[黑板夹具](adr025-step4-board-fixtures.md)及整合登记；只在对应目标见证运行了故障时声称已验证。

三种工具范围分别使用公开启动配置：不给工具关闭当前 15 个工具提供行及 plan-mode，只读使用原生只读预设，可写使用既有原生工具；控制器只响应权限升级请求，不重做原生工具。公开配置导出确认不给工具的 18 个目标行均 disabled（含下述两项始终关闭行）。实测只读回合经原生 bash 重定向尝试写文件，工具返回只读拒绝且目标文件不存在，补齐此前只验证写入工具的缺口。工具词汇按原生名字归类，未知名仍为 other，未来清单外的新工具不宣称已受限制；DSH 公共审阅资格仍未开放，原生续接仍未启用。

本步真实 DSH prompt 共 **4 次**：Worker 2 次、只读命令写入 1 次、不给工具 1 次，另有无模型配置导出。首次 Worker 完成后留证脚本保存摘要失败，不能作为完整两层停止证据；必要重跑取得有效完成工具签收、read 工具事实、身份对应、原生组 gone 及外层停止 true。Worker 私有会话记录提供完整 token 用量，3 条原生计数为 input 30,177、cached input 20,608、output 401、reasoning output 112；采用锁定的 Python zstandard，不新增系统 zstd 命令依赖。读取不到仍是未知。配置、运行边界及第一轮留证缺口均见[原生冒烟记录](adr025-step4-native-smokes.md)，不将模型自述或 Worker 完成等同于外部验收。

五项已接受行为差异为：问询可能只在合作检查点送达；快速调用带有 DSH 系统提示词；原生续接可成为新能力但本次仍重建会话；**第四项：所有由 hey-my-buddy 启动的 DSH 关闭 session-title-llm；第五项：同一范围关闭 session-telemetry-otel**。后两项通过每次私有启动 patch 生效，用户自己交互使用的 DSH 不受影响。全部 DSH 启动使用私有 DSH_HOME，真实 Worker 保留 native_environment 的 HOME、代理等环境；没有修改用户配置、登录、凭据或日常数据，没有读取凭据文件内容。

[格式使用表](adr025-step4-format-usage.tsv)逐项收敛第三步留下的 18 组字段：保留有真实生产读取方的部分，删除其余部分与 ModelStartEvidence、DeniedInteraction、UnknownEvents 三类，另删除只剩旧兜底分支读取的 FastPreparation.native。run_contract.py 为 622 行；四个 harness 的生产组装方已适配，保留格式的读取方沿用此前逐字段登记及本表补充，没有为保留字段虚设消费方。公开 CLI schemas 与 C-Two 契约不变，CONTRACT_VERSION 不变；第五步的通道切换尚未开始。

私有分发验证在 `6a800c2` 对冻结锁安装和实际导入核对通过，资源只含 console.assets，旧 runner/YAML bridge 不存在且 sourceLeaks=[]，依赖实际为 pydantic 2.13.5、zstandard 0.25.0。最终完整检查另覆盖实际私有冷启动和打包测试；未安装或升级日常运行时。公共参考文档中旧 Node 行为的语义说明按用户规定没有重写，本步仅作必要路径和命令适配，留外部 Host 按文档所有权处理。

全部微任务已完成内部固定交付审查、整合登记和确切路径回收，原始证据保留在忽略的 tmp，Host 整合根暂留供复核。第四步现在停下等待用户转达 Claude Code Host 验收；第三步的外部验收也仍待转达，所有 harness 线验收前不开始第五步。
