# ADR-025 第五步：Claude Code 最小真实冒烟

2026-10-07，用户另行明确批准 Worker 回合、审阅各一次；以下两次通过新运行请求、原生运行模块、运行结果与公共角色收集，代码基线为已提交并经聚焦验证的 2e6c2db，C-Two 通道尚未切换。本记录不把它当作切换后的实时通道验证或黑板宏任务验收。

使用机器已有的 Claude Code 2.1.284，公开无模型发现选择 anthropic / claude-haiku-4-5-20251001 / default；两次各只读取私有工作目录里的 smoke.txt 一次，再交付给定结构。未设置 CLAUDE_CONFIG_DIR 或 CODEX_HOME，HOME 与本机登录保持原来源；私有 BUDDY_STATE_DIR、BUDDY_RUNTIME_ROOT、cwd 与回合材料由本次探针拥有。不登录、登出、改密钥、读凭据文件内容或安装日常运行时。

| 运行 | 耗时 | 结果与签收 | 工具事实 | 停止 |
| --- | ---: | --- | --- | --- |
| Worker 1 次 | 8.251 秒 | disposition=completed，summary=ADR025_CLAUDE_WORKER_OK；有效回合签收，structuredOutputValidated=true、structuredOutputSource=json-schema、resultIsError=false | Read 1 次，start/end 同 callId，toolCalls=1、streamComplete=true、unsettledToolCalls=0 | 原生 groupState=gone、外层停止 true、组合停止 true |
| 审阅 1 次 | 6.547 秒 | answer={answer: ADR025_CLAUDE_REVIEW_OK}，answerValid=true | Read 1 次，工具预算 1；交付机制不增加普通工具计数，完整流、未结算数 0 | 原生 groupState=gone、外层停止 true、组合停止 true |

两次的请求身份、结果身份与持有句柄身份逐项相等，modelStarted=true。用量均完整：Worker input 32,358、cached input 32,340、output 366、reasoning output 181；审阅 input 13,041、cached input 13,023、output 307、reasoning output 163，inputBasis 均 includes-cached、各 1 条原生结果记录。没有重跑模型，总计 2 次真实运行。

无模型发现共 2 次。第一份 Host 探针成功取得目录后，错误地把 provider 字段当作 id 读取，模型选择处 KeyError，尚未发送任何模型输入；其脚本与错误日志保留。按实际目录格式改为 provider 并另建脚本、目录结果与日志后，再执行上述两次模型运行；没有覆盖第一份材料。原始请求、结果、公开收据、用量与停止摘要保留在 <checkout>/tmp/adr025-host/<phase>/claude2-*，先前错误日志为 claude-native-smokes.log。私有原生材料的确切根在创建台账，验收前保留，不自行推断其他会话对象归属。
