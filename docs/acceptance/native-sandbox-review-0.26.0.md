# Codex 原生沙盒证明修正（0.26.0）

基线为 `socu/buddy-core` 的 `24b67af`，修正在独立 worktree `~/.codex/worktrees/sandbox-review-results/hey-my-buddy` 的 `socu/sandbox-review-results` 完成。源码与契约升级为 0.26.0，schema 保持 15。范围只修审阅验证；没有安装、修改用户配置或直接读写日常状态目录。

## 判定边界

检查器从原生请求／回复的线程、回合、调用 ID 和类型建立关联，模型命令的拼写、字段顺序、变量与 shell 写法不再成为通过条件。模型工具流仍需完整，额外／重复／未关联工具、错误类型、截断和审批请求失败；一个原生成功结果需读到随机内部 marker，其余四个结果需有原生拒绝。

边界证明新增五个由控制器固定的原生 `command/exec` 探针，全部指定原有私有 `permissionProfile:"buddy-router"`。它们的目标、操作与 RPC ID 由控制器绑定，依据相关联的原生退出码及拒绝、受管输入／sentinel 的不变结果和本地网络正对照判定。模型只能提供辅助输入，不能决定或声明探针类别。操作参数与响应形态按 OpenAI [0.159.0 协议](https://github.com/openai/codex/blob/rust-v0.159.0/codex-rs/app-server-protocol/src/protocol/v2/command_exec.rs)及[实现](https://github.com/openai/codex/blob/rust-v0.159.0/codex-rs/app-server/src/request_processors/command_exec_processor.rs)核对；本批没有运行真实原生 schema 生成或探针。

原生响应须属于唯一 pending RPC ID；未知／重复／延迟回复、缺失结果、输出达到 8 KiB 捕获上限、未知操作／重复操作／错误 profile 均拒绝。五个固定调用与模型工具调用共用 24 次额度，原 300 秒、同回合最多一次格式纠正与双层停机证明保持。`review-evidence.json` 仍在原证据白名单内，以 v2 保存受限策略读回、模型工具关联摘要和固定原生探针的操作／请求 ID／退出码／拒绝与 marker 比较，不保留命令、路径、stdout/stderr、凭据或原文。

## 两份历史证据的离线重放

Luna 参考文件只从用户指定目录读取并复制到本 worktree，未修改原目录。Sol v1 与同一次原生记录的离线摘要来自此前忽略的私有测试根；补充摘要的 run ID 与原 v1 字节哈希绑定。三份无凭据的历史夹具保存在 `tests/python/fixtures/review-evidence/`。

Sol 和 Luna 都得到相同判定：完整的允许工具请求／回复关联、原生策略有效、内部 marker 读取已观察到、四次原生拒绝。旧 v1 数据没有独立的四类边界请求绑定，因此回放都返回 `HISTORICAL_PROBE_BINDING_MISSING`，不会按调用顺序、退出码、输出长度或模型回答给拒绝补造类别，也不会签发新证书。这表明原拼写检查误拒绝了完整工具流；同时如实保留了旧日志的证明边界。新的真实认证必须产生固定探针证明。

模拟用例覆盖三种不同命令／JavaScript 写法完成同样输入，各项判定一致；缺少固定证明不能由四个模型拒绝代替；原生网络探针成功或仅连接失败都不能被模型“denied”覆盖。额外工具、审批、截断、跨调用／跨运行回复、重复 ID／操作／错误 profile 与变更补充日志均有反例。

## 验证结果

聚焦的沙盒、历史重放、模型写法、Codex 控制器、审阅准入、私有区不变式及当前契约 101 项测试通过。模拟原生进程的两个额外端到端用例确认五个固定挑战使用相同 profile／目标，未关联原生回复在模型启动前失败。收尾先运行 `npm --prefix apps/console ci`，使用 Node 24.19.0 通过且无 engine 警告；再运行完整 `uv run --frozen python -m buddy.checks`，退出码 0，1786 项 Python 测试通过（1276.156 秒），165 项 Node 测试通过（15 suites，28.228 秒）。原始输出保存在本 worktree 忽略的 `tmp/sandbox-review/`，未提交原始日志。

测试全部使用显式私有 BUDDY_STATE_DIR 与 BUDDY_RUNTIME_ROOT，并清除继承的运行时、Worker、凭据与 harness-selection 环境。Windows 真机、真实 Codex 0.159.0 command/exec 路径及完整新认证均未验证；0.159.0 不新增已验证证书，安装和真实重验等待用户授权。

经用户授权的一次 Buddy 只读审查默认路由偏好 ZCode，run `8d224b53-dbe7-4d6b-afbd-d93ca0d50c3e` 到 deadline 后 cancelled、停机确认，未交付报告。本批不把它当审查通过；Host 自行核对一手协议、实现和反例。Host 未直接访问该审查的受管 checkout 或日常状态文件。
