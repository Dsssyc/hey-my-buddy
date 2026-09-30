# ADR-019 子批④：账户来源与凭据入口 0.26.0

范围为 ADR-019 第 1–5、8、9 条及第 7 条的账户来源区分，并处理 Claude ANTHROPIC_API_KEY backlog。当前 checkout 先以 b9eb842 合入 24b67af 的①②③；实施分支 socu/worker-accounts-batch4，源码和契约 0.26.0，schema 15。日常安装仍为 0.25.0；本轮未安装、改变用户配置或合并回 buddy-core。

服务在 claim 事务冻结 source 与 credentialRevision；运行中保留原来源，来源或凭据代次变化重建原生续做。额度、提醒、耗尽与重试、按需限频沿用原存储并按来源区分，旧 Worker 回执不污染当前账户。Worker 独立区使用 harnesses/<adapter>/accounts/worker/，Codex 普通执行继续使用 goal 私有 home。备份排除账户内容及系统库条目，storage 保护独立账户与停止未知内容。

CLI 和已认证 Harness 分区实现账户查看、source CAS、登录、状态、取消、登出与移除。密钥仅从 stdin／认证表单进入私有通道，Codex 用原生 stdin 保存，Claude 用专用 macOS 系统凭据库并只在实际原生子进程注入；请求不进入普通命令回放。OAuth 由用户本人完成，链接不进入 SQLite、事件、快照或后续 status；十分钟窗口、关联取消及实际停止均有独立处理。active/uncertain 凭据用户及元数据读取阻止变更，原生保留状态阻止升级。

| 原生检查（逐项获用户同意） | 实际结果 |
| --- | --- |
| N1，Codex 0.159.0 元数据 | 新私有 home 未登录，本机读取 ChatGPT/Pro 与额度事实；只发送 initialize、account/read(refreshToken:false)、account/rateLimits/read，零 thread/turn；所有进程退出 0 且停止确认。保留文件移除了原生账户标识。 |
| N2a，私有浏览器登录启动/取消 | authUrl/loginId 返回，关联 account/login/cancel 返回 canceled，私有账户仍未登录，进程实际停止。 |
| Codex 无效测试密钥 | stdin 写入退出 0，私有目录 0700、原生 auth 普通文件 0600，account/read 返回 apiKey；未打开凭据内容，零模型回合。 |
| 单独授权的私有登出 | 只对含无效测试密钥的私有 home 发送 account/logout；随后未登录，实际停止。 |
| N2b，由用户本人完成浏览器登录 | 首轮返回失败，总时长约 605 秒，推断与十分钟窗口有关；额外取得重试授权后第二轮 97.1 秒成功，私有账户 chatgpt、原生 auth 文件 0600，本机账户前后不变，各进程停止确认。未记录回调地址、授权码、令牌或链接。 |
| Claude Code 2.1.284 只读元数据 | 空私有 CLAUDE_CONFIG_DIR 返回 loggedIn:false/authMethod:none；仅子进程注入无效 API key 后返回 loggedIn:true/authMethod:api_key/apiProvider:firstParty，退出 0，零模型回合。版本由已检查原生二进制静态版本标记确认。 |
| macOS 专用系统凭据库条目 | 新建随机测试身份的无效密钥写入、读回比较与删除均成功；无现有凭据枚举/变更，密钥未进入 argv 或文件。 |

原生操作均使用新建 0700 临时根、私有 BUDDY_STATE_DIR 和空 BUDDY_RUNTIME_ROOT；原生 harness 自己管理凭据，本轮未读取认证文件内容或修改本机共用登录/密钥。Claude OAuth、DSH/ZCode 的登录/密钥入口、Windows/Linux 系统凭据库及原生账户隔离仍未验证，按钮不开放。新独立账户的付费模型执行未额外验证；原生登录成功不能当作模型能力验收。

后端经本次用户批准的运行中 Buddy 自动路由委派，routingPreferences 偏好 Codex，实际选择 codex/openai/gpt-6.1-sol/high。固定成果 b76fec830ecf8bcd5bd3b55dd69318df6a7f2327、artifact 486ff64f-0d14-4c93-ac9c-9f546f918164，在确认停止后由 Host 以 b6fbacc 保存，随后接入已核实原生路径并修改集成边界。服务只通过正常委派接口使用运行中的 Buddy；测试不访问日常看板。Host 已独立运行 114 项受影响测试、34 项账户基础测试、8 项服务边界测试及 44 项前端测试；服务边界覆盖 DEBUG 错误回显、SQLite/回放/事件/备份排除、同步写入期间取消及共享来源拒绝。协议与系统库测试使用私有 fixtures，不调用真实 CLI 或模型。

收尾验证正在执行：使用捆绑 Node 24.19.0，先 npm --prefix apps/console ci，再 uv run --frozen python -m buddy.checks。完整结果、源码与资产标识以及 Host integration/acknowledge 将在实际完成后补入本记录；本段不声称完整验收已通过。

真实合成浏览器检查已看到 Harness 的账户／额度入口、独立账户表单与禁用能力；390 宽度布局可读并可滚动，临时视口已恢复。预览不读取看板、凭据或真实服务，且拒绝写操作。Raw 本地检查脚本与脱敏报告留在忽略的 tmp/worker-accounts/，不进入分发。

源码入口原生联通的第一次检查拒绝了 Codex 原生 tmp/arg0 下的执行工具链接；这些链接不属于凭据。实现现只在实际停止后，以不跟随链接的私有清理删除该原生临时工具树，再核对账户文件；凭据链接和其他特殊条目仍拒绝。模拟 CLI 已加入相同的三个工具链接，14 项原生封装／服务测试通过。该结果不算源码入口原生成功；重试另行向用户请求同意。另一次用户批准的清理已对成功 N2b 私有登录完成原生登出、未登录读回与实际停止，随后删除了六个已停止的本轮临时原生检查根。

额外取得修正后联通检查授权后，0.26.0 真实服务 account-login 入口已通过：新的私有状态／空 runtime，无效密钥只经原生 stdin，返回 Worker 来源、ready、metered、credentialRevision=1；回复、meta/commands/events/attempts/artifacts 和备份清单均未检出密钥，pending owner=0，全部原生操作已确认停止，零模型回合。链接修正同时由包含三个原生工具链接的 mock CLI 覆盖。

2026-10-01 收尾第一次完整检查实际运行 1,816 项 Python 测试（1 skipped），发现两处阻塞：目录刷新测试替身不接收新增的 directory/database 参数，导致把预期 CATALOG_UNAVAILABLE 换成 INTERNAL_ERROR；共享 Skill 超出 4 KB 上限。已修正替身签名而保留失败安全断言，并将 Skill 压到 4,094 字节，4 项受影响检查通过。随后再次先 npm ci（零漏洞），再运行完整 buddy.checks；最终结果以接下来的实际输出为准。

第二轮完整 Python 检查仅剩共享 Skill 原句断言，未出现账户代码失败；已恢复 acceptance and authorization 原句，并压到 4,092 字节，17 项 Skill／安装／目录失败安全检查通过。安装测试 clear=True 的私有环境补齐 runtime/HOME 路径，避免测试计划引用日常默认路径。前端完整测试实际通过 54 个文件、630 项。再次按 npm ci → buddy.checks 执行最终组合树检查，尚未记录通过前不作完整验收结论。

最终组合树检查已通过（2026-10-01）：按 npm --prefix apps/console ci → uv run --frozen python -m buddy.checks 的顺序，Python 1,816 项（1 skipped），DSH Node 165 项，检查命令退出 0；前端 54 文件／630 项全部通过，npm ci 审计零漏洞，tsc/Vite 构建退出 0（保留既有大 chunk 提示）。私有 build-skill 输出 version=0.26.0，包含五个账户模块，排除 tests；没有运行安装。所有测试与服务替身使用私有根，最终安装测试计划也只列私有路径。

最终日志 SHA-256：644db3ccf38dcb53dc3966b3596dab521d0f6d957fe8bebd1746c465c2df16c0；Console 资产 index.html SHA-256：6b452f25fc9bf1c0272e2a9366ba9a7c376b9e3afd8a7bc89348589a7c6767df。本记录不保存秘密、授权 URL、设备码或原始账户响应。源码联通重试已取得单次授权并通过，独立 API-key 模型执行仍只有模拟覆盖。建议 Host 将当前实施分支合入 socu/buddy-core；合并、安装和用户配置变更等待用户下一步指令。
