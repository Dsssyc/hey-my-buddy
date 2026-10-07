# ADR-025 第五步整合登记

2026-10-07，实施分支先从 c550d65 快进到 socu/buddy-core 当前的 43d8d63，包含用户指出的 3449897、第三四步验收合并 3fce9ac 及其后的文档提交。第三四步已由 Claude Code Host 验收，依据是 adr025-step3-4-host-review.md；本步尚未完成，日常运行时没有安装或升级。

开始时实际加载 Python 编号 2,785 个、175 个模块，装载错误和重复均为零；原始清单在 <checkout>/tmp/adr025-host/<phase>/step5-before-ids.json。第五步先整合清理批次，再改实时通道；清理无需单独等待外部验收，整步结束才停下。

三项独立清理微任务首次均未指定配置，走现有黑板路由：5-P1 回执核验，run 3b9a2305-b829-4e18-9561-a9f4c803d8f2，路由 dec-dcf974a5-f999-45e4-8388-828c74f62396 选 zcode / zai-api / GLM-5.3-Flash / max；5-P2 运行小段与 ZCode 阶段，run e5e8763a-b5eb-4e83-8952-512603026df1，路由 dec-ef85a626-94ef-4b96-95ec-746e5e8082be 选 zcode / zai-api / GLM-5.3 / max；5-P3 三类防护，run 939b427c-0fc0-4342-bc05-6e424fbf1ae9，路由 dec-6244b519-d4fb-4f4e-9652-b1416ac89a4a 选 zcode / zai-api / GLM-5.3-Flash / max。各自隔离 worktree、固定 43d8d63 基线、唯一可写路径已写入任务描述；公共值、角色与注册表归 Host。三个任务短根由 Host 创建并登记，Worker 不手动删除、不切分支，临时材料不覆盖；验收后按确切根回收。第三个监测子代理因容量被拒，Host 改用原 run 的前台 await，没有重提或更换 buddy。

Host 先确认无生产调用方，再删除 structured_call.start/start_no_tool、旧结果 else/_collect_result 和 read_router_result；保留实际使用的冻结证据留存。控制器移除 before_try，prepare 变为无参数，所有现存启动点保持原位置与 FD 收尾；collect_controller 要求显式 stop，删除可省略 stop 的旧分支及 Node 例外说明。原只为断言拒绝而调用 read_only.start 的测试改用实际角色入口。两项仅覆盖旧结果读取的测试随删除退役；外层停止强制布尔的测试与替换链接拒绝的测试改用真实公共请求、结果和角色收集，保留正向对照。

Host 将角色中的四处厂商判断移到登记参数：冻结账户的读取规则，以及 WorkerReceiptOptions 的未知额度码过滤、只从已验证回合捕获会话、原生活动收据投影；各字段都有生产读取。BUDDY_PYTHON 经源码检索确认只有写入和放行，已删除全部生产引用；启动仍由明确 argv 的解释器、运行时身份与 PYTHONPATH 决定。相关测试不再断言无消费者的环境变量，私有分发回合改报真正的 sys.executable 来核对解释器归属。

Host 清理聚焦 15 个受影响模块，154 项全部通过，27.975 秒，私有检查根正常收尾；没有跑完整检查或调用模型。迁移链接防护的单点变异只去掉新角色读取中的 guard_private_path：原件 1 项通过，变异命中 ok != failed 的断言（0.227/0.346 秒），原始输出留 tmp。第一次探针在原件通过后误读 ChildOutcome.exit_code 属性而退出，不算变异证据；保留该脚本、通过输出与副本，另建新副本和新输出，用真实 returncode 完成核对，没有覆盖旧材料。

用户已明确批准 Claude Code 的两次最小真实冒烟（Worker 回合一次、审阅一次），尚未运行。将沿用本机已有登录，不设置 CLAUDE_CONFIG_DIR 或 CODEX_HOME，不登录、登出、改密钥或读取凭据文件内容；额度或登录阻碍按未验证记录，不反复尝试。
