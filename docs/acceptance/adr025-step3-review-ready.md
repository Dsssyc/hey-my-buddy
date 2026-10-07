# ADR-025 第三步提交 Host 验收

Codex 与 Claude Code 已接入同一个 RunRequest → 原生运行模块 → RunResult 外层，Worker 和结构化调用复用公共角色；两个 harness 的旧 runner 与 Codex no_tool 入口已删除。Claude StructuredOutput 按本次根、真实内建来源、调用关联与结算结果识别，普通工具、子会话、同名 MCP 调用与冲突输入仍保留计数。此记录提交验收，不代表 Claude Code Host 已验收。

最终检查基线 `bd4324e`；`uv run --frozen python -m hey_my_buddy.cli.checks` 使用默认并行数，退出 0：Python **2,790 / 176 模块**（skip 1）、Node **110**，459.388 秒。原始日志为 `tmp/adr025-host/step34-20261006-105245/step3-full-check3.log`。本次同时覆盖并行共存的 DSH 原生主体，不代表 DSH 接线完成；第五步尚未开始。

相对第二步 2,628 个测试，2,619 个编号不变、7 个迁移改名、2 个重复 schema/解析器测试删除并有共享覆盖，新增 164 个；两侧扣除列明变化后集合相等。详见[变化表](adr025-step3-test-ids.tsv)。各微任务迁移防护的故障注入、拒绝与同 run 修正、公共整合和清理证据保留在[整合登记](adr025-step34-integration.md)、[Codex 接线](adr025-step3-codex-wiring.md)、[Claude 接线](adr025-step3-claude-wiring.md)及[StructuredOutput](adr025-step3-structured-output.md)。

真实检查：Codex Worker 1 次通过新请求/结果、业务签收、1 次原生工具、两层停止；Claude 1 次无模型发现遇到所选 CLI 登录条件，Worker 0 次，按用户规则记未验证且未重试。详情与用量在[冒烟记录](adr025-step3-native-smokes.md)。未安装或升级日常运行时，未修改用户登录、配置、凭据或日常数据，未读取凭据文件内容。

[格式使用表](adr025-step3-format-usage.tsv) 保留第二步 18 组待追踪项的逐项现状：1 组完整业务消费、3 组部分读取/投影、14 组无业务读取；ModelStartEvidence、DeniedInteraction、UnknownEvents 仍只有构造/序列化用途。按用户决定，这些剩余内容在第四步结束前有真实读取方或删除；不把产物留存算作业务使用。两条线目前停止，等待用户转达外部验收结果；独立 DSH 线继续。
