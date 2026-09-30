# ADR-019 第二阶段子批 ②：路由与验证（0.25.0）

本批在 `socu/private-dirs` 快进合入 `socu/buddy-core` 的 `58f1cdd` 后实现。源码与契约升级为 0.25.0，schema 保持 15。范围只包含 backlog“路由”的两条；账户、密钥、OAuth、配置页分区导航与存储／会话子批仍由各自会话处理。本次没有安装运行时或修改用户配置。

## 结果与证据边界

| 已移除的 backlog 条目 | 实现与验证 |
| --- | --- |
| Codex 0.159.0 的审阅验证失败，且证据没有保留 | 原生策略在失败分支也先保存到私有结果；外层 attempt 的白名单文件 `review-evidence.json` 保存脱敏策略读回、关联事件摘要、九项检查依据和字节哈希。凭据、路径原文、命令、输出、账户字段与模型原文均不进入该文件。证据发布失败拒绝认证并保留私有诊断；未知停止不清理。Worker 回执引用实际保留的诊断，不再引用已删除的内部日志。 |
| 没有重置时间的额度耗尽可能一直排除 | 无重置时间的 provider/limit 耗尽在观测或上次消费一小时后开放一次机会；选定配置时在同一事务中消费，未选候选、读取和未决 Router 不消费。CLI `quota-redetect` 与 Harness 详情中的最小入口提前开放机会，requestId 回执防止回复丢失后重发再开机会；回执、事件与状态原子提交。已知重置、明确 Host 配置、旧／未知／部分观测及适用作用域均保留原有规则；旧的 pre-meta 原生观测只在写操作中按顺序导入。 |

设计与拥有详细契约的引用见[路由与验证设计](../design/routing-validation.md)、[Harness](../reference/harnesses.md)、[CLI](../reference/cli.md)与[操作文档](../reference/operations.md#routing-diagnostics-and-quota-recovery-0250)。两条 backlog 已删除，其余条目保留。

## 一次经授权的 Codex 原生重跑

用户明确批准一次 Codex 0.159.0、`openai / gpt-6.1-sol / high` 的私有检查，预算 300 秒／24 次工具调用／512 KiB，最多一次格式纠正。Host 使用源代码的真实服务准入、attempt 领取、ReviewCheckAdapter、原生控制器与结果回执，通过进程内私有看板执行；没有使用日常看板，也没有为日常服务发布证书。私有根为 `tmp/routing-validation/native-codex-01590-01/`。

run 为 `c41ba317-de42-480b-8eef-df5ef88b4de9`，attempt 为 `7468d53e-ecee-4c65-9d11-da1f7716bba9`。控制器耗时 80,661 ms，原生耗时 80,358 ms，工具调用 5 次，输入／sentinel 均未改变，原生与控制器停止均确认，检查进程 stderr 为空。原始现场判定仍有 nativePolicy、forbiddenTools、boundaryDenials、internalRead 四项失败，但白名单诊断已保留，SHA-256 为 `dc281cd25ac4dd11332caf11931c18ee1987960cdfaf455aa72a3139c672fce1`。

保留下来的策略与本次私有原生会话定位了检查器问题：原生配置含额外默认字段，准入比较请求控制值，旧评估却比较整个 features、shell 和 network 对象；真实命令省略 `max_output_tokens`，返回形态为固定桥接元数据加 JSON 结果的两个 `input_text` 块。源码统一了严格带类型的请求控制比较，文件系统授权仍精确匹配；识别此确切命令／结果形态，仍拒绝额外 JS、错误变量、未关联回复、任意文字、截断和额外工具。

没有增加第二次原生或模型调用。修正后只离线重放本次五组原生请求／结果：关联完整、内部 marker 读取匹配，外部读取、内外写入与网络请求均有非零退出和原生拒绝；原始现场的输入不变与停止证明保留。原始失败证书没有改写，源码没有新增 0.159.0 的已验证证书；修正后的完整检查器尚未重新进行原生认证。私有原生会话已在确认停止、完成离线诊断后回收，脱敏诊断与离线摘要保留在忽略的 `tmp/`。

## 委派与 Host 审查

经用户单独授权，Buddy 使用默认路由并通过 routingPreferences 表达 ZCode 偏好，选择 ZCode / GLM-5.3 / max 实现额度部分。run `19d7a4c2-b63f-4de0-987f-71f5f6068918` 到达执行时限，以 cancelled 和已确认停止交付部分产物。Host 从共享 Git 对象导出固定 `8a258340dbbb1b4823c2da30d474c8d698515a21` 的补丁，并与 RPC 返回的 SHA-256 `a01b1ec146fe56606f7342203d5cb0b957be768e44d774c1e004bd79c39d9fb8` 核对；没有直接读取日常状态目录内的工作区或日志。

Host 审查并修正了候选扫描提前消费、重新检测缺少真正的请求幂等与事件回执、旧观测没有恢复入口、时间比较及前端测试类型／查询问题。独立并发测试确认两个选择事务仅消费一次；未选候选、Router 不可决与幂等重放保持机会，前端保留未知回复的非秘密请求身份。该部分产物只作为 Host 完成工作的输入，不作为 Worker 已完成或已验收的证明。

## 自动验证

所有测试使用显式私有 BUDDY_STATE_DIR 与 BUDDY_RUNTIME_ROOT，子进程清除继承的运行时、Worker 与凭据环境；没有测试连接日常看板。前端使用现有 Codex 工作区依赖中的 Node 24.19.0，符合仓库 engines；没有更换用户全局 Node。`npm --prefix apps/console ci` 在此环境成功，无 engine 警告。

审阅与目录相关的 85 项聚焦测试、补充配置失败／独立模块／契约检查的 26 项，以及集成后的额度、路由和工作流 95 项测试均通过。前端额度恢复 8 项测试通过，TypeScript 与 Vite 构建通过。最终 `uv run --frozen python -m buddy.checks` 退出 0：1,753 项 Python 测试（1,272.814 秒）、161 项 DSH Node 测试（14 组）全部通过，私有根回收确认成功；完整前端 52 个文件的 609 项测试、TypeScript 检查与 Vite 构建均通过。共享 skill 最初超过 4 KiB 导致单一预算测试失败；入口说明压缩到 4,086 字节、10 项 skill 检查通过后，上述完整检查重新执行并通过。合成预览使用真实构建资产，HTTP 明示 synthetic-fixture-data；Codex 内置浏览器在 1280 宽和 390×844 窄屏中均无横向溢出，额度详情只增加一处重新检测控件。模拟服务拒绝命令时错误可见、没有恢复成功提示，浏览器 warn/error 日志为空。该核对只读取／操作合成页面，没有连接日常控制台或调用原生模型；临时标签与预览端口已关闭，尺寸覆盖已重置。

Windows 真机、Linux 原生权限检查、真实余额耗尽／充值后的恢复、修正后新一次 Codex 认证、日常看板数据变更与安装均未在本批验证。Vite 仍报告主包超过 500 kB 的非阻塞提示，本批没有开展拆包改造。
