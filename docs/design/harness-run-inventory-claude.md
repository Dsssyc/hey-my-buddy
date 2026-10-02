# Claude Code 运行清单

2026-10-03。本文是抽取"让一个 harness 运行一次并拿到事件、结果与停止证据"通用运行模块前的代码调研,只陈述 `src/buddy/adapters/` 下 Claude Code 相关代码的事实,未运行任何测试、原生 CLI 或模型调用。范围:`src/buddy/adapters/claude.py`、`src/buddy/adapters/claude_runner.py`、`src/buddy/adapters/claude_protocol.py`、`src/buddy/adapters/claude_config.py`,以及共用的 `src/buddy/adapters/base.py`、`src/buddy/adapters/read_only.py`、`src/buddy/adapters/turn_io.py`、`src/buddy/adapters/decision.py`;少量支撑事实引自 `src/buddy/harness_runtime.py`、`src/buddy/harness_discovery.py`、`src/buddy/adapters/native_observations.py`,均已注明。

三个启动入口的现状:Worker 回合的 `start` 与审阅路由的 `start_read_only_structured` 由 Claude Code 实现,快速路由的 `start_no_tool_structured` 没有——`ClaudeAdapter` 未声明 `no_tool_structured`(`src/buddy/adapters/claude.py:38`-43;`src/buddy/adapters/base.py:150`),基类入口直接抛 `UNSUPPORTED_ADAPTER`(`src/buddy/adapters/base.py:152`-154),决策适配器的快速分支也会先拒绝(`src/buddy/adapters/decision.py:36`-38)。此外同一 runner 还接受第四种分支:只做 initialize 的发现模式(`src/buddy/adapters/claude.py:272`-302;`src/buddy/adapters/claude_runner.py:321`-322),它不属于三个角色入口,但与它们共用全部进程与协议代码。

## 1. 启动

Worker 回合 `start` 先调 `prepare`(`src/buddy/adapters/claude.py:119`-120)。`prepare` 校验:spec 的 provider、model、effort 三键必须是 nonblank 字符串且 provider 为 `anthropic`(`src/buddy/adapters/claude.py:77`-81);必须存在受治理回合(`src/buddy/adapters/claude.py:82`-83);`resumeMode` 不得是 `native-session`,只能是 `initial` 或 `reconstructed-new-session`(`src/buddy/adapters/claude.py:84`-88);`BUDDY_CLAUDE_SETTINGS_POLICY` 只接受 `isolated`(`src/buddy/adapters/claude.py:89`-92);不得配置第三方 provider 覆盖变量(`src/buddy/adapters/claude.py:93`-95);必须有私有 `BUDDY_STATE_DIR`(`src/buddy/adapters/claude.py:96`-97);回合结果文件不得已存在(`src/buddy/adapters/claude.py:98`-99);CLI 可解析(`src/buddy/adapters/claude.py:100`-103)。然后写回合文件并核对工作区(`turn_io.prepare_turn`,`src/buddy/adapters/turn_io.py:134`-138),创建 `claude-private` 私有目录(`src/buddy/adapters/claude.py:105`),把控制文件 `claude-control.json` 写入尝试目录,键含 directory、nativeRoot、cwd、timeoutSeconds、inputFile、outputFile、taskFile、activityFile、taskId、attemptId、generation、新分配的 uuid4 sessionId、access(工作区 manifest 为 `read` 时为 `read`,否则 `write`)和 spec 三键(`src/buddy/adapters/claude.py:106`-117)。

Worker 回合的进程启动:打开 `runner.stdout.log` 与 `runner.stderr.log`(`src/buddy/adapters/base.py:85`-89;`src/buddy/adapters/claude.py:122`-123),以 `owned_popen` 启动 `python -m buddy.adapters.claude_runner --control <claude-control.json>`,cwd 为工作区路径,环境为 `controller_environment(context.directory, context.environment)`(不带 `read_only`),stdin 为 DEVNULL,新会话、close_fds(`src/buddy/adapters/claude.py:125`-129)。句柄 deadline 为 `now + timeoutSeconds`,`timeoutSeconds == 0` 表示无限(`src/buddy/adapters/claude.py:134`-136)。不带 `read_only` 意味着凭据变量会进入控制器进程环境(`src/buddy/harness_runtime.py:72`-75)。

审阅路由 `start_read_only_structured` 委托共用的 `read_only.start`(`src/buddy/adapters/claude.py:45`-47;`src/buddy/adapters/read_only.py:122`-154)。它先用 `_account_environment(name, context, purpose='review')` 消费服务冻结的账户选择,要求账户 adapter 与 `claude` 匹配(`src/buddy/adapters/read_only.py:105`-119, 123);nativeRoot 为 `review-native` 而非 `claude-private`(`src/buddy/adapters/read_only.py:126`;对照 `src/buddy/adapters/claude.py:105`);控制文件为 `readonly-control.json`,键含 directory、nativeRoot、account、cwd(来自请求)、timeoutSeconds(来自请求预算)、taskId、attemptId、generation、新 uuid4 sessionId、固定 `access: "read"`、activityFile、spec 三键,以及 `readOnlyRequest`(prompt、outputSchema、budget、captureEvidence,可选 nativeProbe)(`src/buddy/adapters/read_only.py:125`-139)。控制器环境是账户环境再过 `controller_environment(..., read_only=True)`,凭据变量被剥离(`src/buddy/adapters/read_only.py:140`-141;`src/buddy/harness_runtime.py:72`-75)。启动命令形态与 Worker 相同,cwd 为请求的 cwd(`src/buddy/adapters/read_only.py:146`-148);句柄 deadline 为 `now + 预算 timeoutSeconds + 10`(`src/buddy/adapters/read_only.py:153`)。注意:决策适配器的审阅分支要求 `read_only_structured and read_only_structured_verified`(`src/buddy/adapters/decision.py:53`-54),而 `ClaudeAdapter` 未声明 verified(`src/buddy/adapters/claude.py:38`-43;`src/buddy/adapters/base.py:148`),所以这个入口虽然实现存在,当前不会被决策适配器选中。

快速路由 `start_no_tool_structured`:没有。Claude Code 未实现该入口(见文首),`src/buddy/adapters/claude_runner.py` 也没有任何 `noToolRequest` 分支;共用侧的 `read_only.start_no_tool` 会写 `no-tool-control.json`(`src/buddy/adapters/read_only.py:178`-189),但对 Claude 不可达。

发现模式(补充事实):`_probe_native_metadata` 在系统临时目录写 `control.json`(键 `discover: True`、directory、cwd、timeoutSeconds 25),启动同一 runner 模块,30 秒内未停则 terminate,成功且确认停止后删除临时目录,否则保留现场(`src/buddy/adapters/claude.py:272`-302)。

相同点:三个实际分支(Worker、审阅、发现)都是"写控制文件 → 启动 `python -m buddy.adapters.claude_runner --control`",进程参数形态一致(DEVNULL stdin、start_new_session、close_fds、日志重定向到尝试目录)(`src/buddy/adapters/claude.py:122`-133;`src/buddy/adapters/read_only.py:144`-152;`src/buddy/adapters/claude.py:282`-289);runner 内部对原生 CLI 的启动、协议与停止处理是单份共用代码(`src/buddy/adapters/claude_runner.py:211`-494)。不同点:控制文件名与键集(`claude-control.json` 对 `readonly-control.json`)、nativeRoot(`claude-private` 对 `review-native`)、控制器环境(凭据进或不进)、cwd(工作区对请求目录)和 deadline 语义(0 为无限对固定加 10)各不相同。

## 2. 会话与输入

会话建立:每个尝试新分配一个 uuid4 的 `--session-id`,Worker 与审阅都如此(`src/buddy/adapters/claude.py:114`;`src/buddy/adapters/read_only.py:129`);runner 校验它存在且为 UUID v4(`src/buddy/adapters/claude_runner.py:243`-249)。命令行从不使用 `--resume`(`src/buddy/adapters/claude_config.py:282`)。原生 `system/init` 帧必须报告与预分配相同的 session_id 和与分配一致的 cwd,否则回合失败(`src/buddy/adapters/claude_protocol.py:467`-487)。发现模式用 `--no-session-persistence`(`src/buddy/adapters/claude_config.py:277`);执行参数不带该旗标(`src/buddy/adapters/claude_config.py:281`-310),即执行回合的会话留在用户自己的 Claude Code 存储中,可见性未验证(`src/buddy/adapters/claude.py:355`-360)。

模型、提供方与推理强度的选择和核对:请求侧是 spec 三键,由两个入口各自写进控制文件(`src/buddy/adapters/claude.py:116`;`src/buddy/adapters/read_only.py:132`);Worker 的 `prepare` 先做静态校验(见第 1 节)。命令行为 `--model` 加条件 `--effort`,effort 为 `default` 时省略 `--effort`,以免漂移的原生默认把两种配置混进同一 buddy 的证据(`src/buddy/adapters/claude_config.py:291`, 303-304;`DEFAULT_EFFORT` 注释 `src/buddy/adapters/claude_config.py:16`-19)。运行时核对发生在 runner:initialize 的响应经 `_catalog` 整理为模型目录(跳过 `default` 别名,取 `resolvedModel`,归一 `supportedEffortLevels`)(`src/buddy/adapters/claude_runner.py:45`-94),Worker 分支与审阅分支各自要求 spec 的 model 在目录中且 effort 在该模型的档位列表里(`src/buddy/adapters/claude_runner.py:330`-335;`src/buddy/adapters/claude_runner.py:171`-174)。账户核对:initialize 的 account 回读必须是第一方 Anthropic(`src/buddy/adapters/claude_config.py:162`-177);唯一允许回退的形状(第一方且 tokenSource 缺失)用同一个解析出的可执行文件、同一允许列表环境和 cwd 做一次 `auth status --json` 读回核实(上限 10 秒、64KB)(`src/buddy/adapters/claude_config.py:180`-238;调用点 `src/buddy/adapters/claude_runner.py:316`-320)。观察侧不充当证明:init 帧的 model 只是会话模型的观察,argv 从不当作证明(`src/buddy/adapters/claude_protocol.py:481`-485);`resolved` 只是重述经过验证的请求配置,不声明已生效的 effort 回读(`src/buddy/adapters/claude_runner.py:336`-339);`observedModels` 是有界的观察集合而非单一身份(`src/buddy/adapters/claude_protocol.py:372`-378)。

输入送入:两种实际分支都通过 stream-json 的 `user` 帧发送一条文本消息(`src/buddy/adapters/claude_runner.py:356`-358;`src/buddy/adapters/claude_runner.py:177`-179)。发送前先 `pump_available` 划定用户消息边界,早于用户消息到达的模型输出(assistant、result、stream_event)在重放缓冲帧时被拒绝(`src/buddy/adapters/claude_runner.py:352`, 367-371;`src/buddy/adapters/claude_protocol.py:152`-165;缓冲上限 128 帧 `src/buddy/adapters/claude_runner.py:285`-287)。

任务书与提示词的组装:Worker 回合的任务书来自 `spec["task"]`,由 `turn_io.prepare_turn` 写成私有 `task.txt`(`src/buddy/adapters/turn_io.py:134`-137);回合输入由服务拥有的 turn_input 写成 `turn-input.json`,上限 262144 字节(`src/buddy/adapters/turn_io.py:141`-146, 18)。runner 在 Worker 分支拼接提示词:`HARNESS_PREAMBLE` + 三段 `ASSISTANCE_HINTS` + task.txt 全文 + turn_input 的 canonical JSON(`src/buddy/adapters/claude_runner.py:35`-42, 353-354;`src/buddy/adapters/turn_io.py:28`-41)。审阅路由的提示词就是请求的 `prompt` 字段(`src/buddy/adapters/read_only.py:133`;`src/buddy/adapters/claude_runner.py:177`-178),对 Claude 而言由决策适配器用 `router.render_prompt(document)` 组装(`src/buddy/adapters/decision.py:59`;`router` 模块不在本文范围)。

## 3. 工具与权限

两种实际分支共用同一 `execution_args`(`src/buddy/adapters/claude_config.py:281`-310):基础是 `-p --input-format stream-json --output-format stream-json --verbose`(`src/buddy/adapters/claude_config.py:271`-272),再加 `--safe-mode`、空 `--mcp-config`、`--setting-sources ""`、`--restricted`、`--permission-prompt-tool stdio`、`--settings` 私有 settings.json、`--session-id`、`--model` 和 `--json-schema`(`src/buddy/adapters/claude_config.py:283`-291, 309)。

工具集按 `access` 选择:read 时用 `READONLY_TOOLS`(Glob、Grep、LS、Read)加 `--permission-mode default` 和 `--disallowedTools`(Bash、Edit、MultiEdit、Notebook、Write),会话级拒绝表对原生子代理同样生效(`src/buddy/adapters/claude_config.py:107`, 112, 292-295);write 时用 `WRITABLE_TOOLS`(Agent、Bash、Edit、Glob、Grep、LS、MultiEdit、NotebookEdit、Read、Task、TodoWrite、Write)加 `--permission-mode acceptEdits`,让工作区内的普通编辑不变成人工审批,同时永不使用 bypassPermissions(`src/buddy/adapters/claude_config.py:102`-103, 296-302)。Worker 回合的 access 来自工作区 manifest(`src/buddy/adapters/claude.py:106`, 115),审阅调用固定为 read(`src/buddy/adapters/read_only.py:130`)。带 outputSchema 时(即审阅调用)再把 `mcp__*,WebFetch,WebSearch,Agent,Task` 追加进拒绝表,因为 `--tools` 不限制 MCP(`src/buddy/adapters/claude_config.py:305`-308)。

沙盒与联网:runner 把 `sandbox_settings()` 写成原生 settings.json(`src/buddy/adapters/claude_runner.py:250`-253),内容为 Bash 沙盒启用、`failIfUnavailable`、不允许未沙盒命令回退、网络严格白名单只放行包管理器注册表域名(`src/buddy/adapters/claude_config.py:247`-268, 87-95)。审阅调用(`readOnlyRequest` 存在)把网络白名单清空,即完全禁网(`src/buddy/adapters/claude_runner.py:251`-252)。Worker 回合保持注册表白名单。

权限请求的处理:原生 `can_use_tool` 控制请求一律回 `deny`,附带 `TOOL_DENIAL_MESSAGE`(提示模型把边界作为 attention 写进结构化结果),并记入 denied_requests,上限 32 条(`src/buddy/adapters/claude_runner.py:290`-307;`src/buddy/adapters/claude_config.py:116`-117);其他原生控制请求(如 hook 回调)回 error 响应并计数,不失败(`src/buddy/adapters/claude_runner.py:308`-312, 443-444)。快速路由:没有。

## 4. 事件

读取与校验:专用线程逐行读原生 stdout,帧超过 8MB 拒绝;`decode_json` 拒绝重复 JSON 成员与非有限数,非对象帧拒绝(`src/buddy/adapters/claude_protocol.py:104`-116, 73-88, 29)。分发时(`_deliver`):`control_response` 必须带合法 request_id、匹配一个未决请求且不可应答两次;`control_request` 转给回调;其余字符串 type 交给消息回调;无 type 的帧失败(`src/buddy/adapters/claude_protocol.py:189`-218)。控制器主动请求(`call`)有 16 个未决上限和 100000 的 id 预算(`src/buddy/adapters/claude_protocol.py:220`-236)。取消或超时在每次收发检查点抛出(`src/buddy/adapters/claude_protocol.py:118`-124)。

`TurnEvidence.observe` 把每帧归类(`src/buddy/adapters/claude_protocol.py:435`-539):`rate_limit_event` 记录额度窗口与观察,rejected 状态直接抛 `QuotaRejected`(`src/buddy/adapters/claude_protocol.py:439`-461);带 `parent_tool_use_id` 的帧是子代理帧,不算根回合证据(`src/buddy/adapters/claude_protocol.py:463`-466);`system/init` 记录初始化并核对 session 与 cwd(`src/buddy/adapters/claude_protocol.py:467`-488);`system/task_started` 与 `task_updated` 跟踪背景任务,终态判断用精确相等的状态闭集(`src/buddy/adapters/claude_protocol.py:489`-515, 48-49);`assistant` 计一次模型消息并统计 tool_use 块(`src/buddy/adapters/claude_protocol.py:517`-528);`stream_event` 记流式活动(`src/buddy/adapters/claude_protocol.py:529`-530);`result` 保存终态结果帧,重复即失败(`src/buddy/adapters/claude_protocol.py:531`-538)。

未知事件的处置:未知的顶层 type 与未知的 system 子类型都返回 None 被忽略(`src/buddy/adapters/claude_protocol.py:516`, 539);未知的原生控制请求子类型被回以 error 响应但不失败(`src/buddy/adapters/claude_runner.py:308`-312)。失败的是结构违规:重复 init、init 无身份、session 或 cwd 不匹配、重复 result、背景任务超过 32 个(`src/buddy/adapters/claude_protocol.py:471`, 473-480, 532-533, 498, 514)。

活动、用量与额度:活动由 `_activity` 原子写入控制文件指定的 activity.json,含 phase、observedAt、eventSeq、nativeSessionId 和 counts(modelTurns、toolCalls)(`src/buddy/adapters/claude_runner.py:97`-119);phase 取值为 starting、streaming-model、tool-running、finishing(`src/buddy/adapters/claude_protocol.py:488`, 527-530, 538)与 waiting-model(`src/buddy/adapters/claude_runner.py:373`)。用量优先取 result 帧的 usage(标 complete),缺失或全零时用按消息 id 去重的 assistant usage 求和(标 partial),缓存读/写计数单独可空,不把未知当零(`src/buddy/adapters/claude_protocol.py:541`-583, 585-603, 627-665)。额度把 unifiedWindows 分数换算成百分比窗口,越界值不发表(`src/buddy/adapters/claude_protocol.py:330`-356, 667-713)。适配器收集时把这些归一化为 `tokenUsage`、`quota`、`lastAssistantMessage`(`src/buddy/adapters/claude.py:154`-157)。审阅分支的 usage 只含 toolCalls 与 bytesRead=None(`src/buddy/adapters/claude_runner.py:183`, 204-207),另加 elapsedMs(`src/buddy/adapters/claude_runner.py:492`-493)。

## 5. 结束约定

Worker 回合用结构化输出:命令行带 `--json-schema OUTCOME_SCHEMA`(`src/buddy/adapters/claude_config.py:309`),schema 是嵌套 anyOf 的两分支——completed 要求 `request: null`,assistance/attention 要求完整 request 对象(`src/buddy/adapters/claude_protocol.py:239`-276)。终态 result 帧的 `subtype` 必须精确为 `success` 且 `is_error` 恰为 False,缺字段、错类型或未知 subtype 都是失败;结构化配额拒绝先于一般失败分类(`src/buddy/adapters/claude_runner.py:397`-405;`src/buddy/adapters/claude_protocol.py:363`-369)。`structured_output` 经 `parse_structured_output` 要求恰好一个 `outcome` 键,再用 `turn_io.validate_outcome` 校验字段与边界(`src/buddy/adapters/claude_protocol.py:279`-286;`src/buddy/adapters/turn_io.py:210`-251)。若发生过被拒的原生权限请求而结构化结果是 completed 或无效,控制器把结果改写为 attention 结果(`src/buddy/adapters/claude_runner.py:406`-416, 122-132)。通过后写回合记录到 outputFile(独占创建),含身份、inputSha256、promptSha256、sessionId、previousSessionId、resumeMode、outcome 与 provenance(`src/buddy/adapters/claude_runner.py:417`-435, 490-491)。

Worker 回合的收集侧再校验一遍:`_read_native_turn` 要求退出码 0 且停止已确认,读回合记录(上限 98304 字节)与控制文件,比对身份五元组、resumeMode、previousSessionId 与 inputSha256,要求记录的 sessionId 等于 Buddy 预分配的 UUID,再走 `validate_outcome` 与 `validate_turn_provenance`(`src/buddy/adapters/claude.py:364`-388;`src/buddy/adapters/turn_io.py:18`, 387)。`validate_turn_provenance` 逐项核对 provenance 的类型化期望(adapter、turnEnd、resultIsError、resultSubtype、structuredOutputSource、initObserved、backgroundSettled、eventSeq 至少 2、modelUsage 至多 16、permissionDenials 有界,controllerAttention 必须绑定被拒请求 id 且携带 attention 结果)(`src/buddy/adapters/claude.py:207`-250)。

审阅路由也用结构化输出,但 schema 换成请求的 outputSchema(`src/buddy/adapters/claude_config.py:255`-258, 309);`_read_only_call` 把 `structured_output` 作为 `rawAnswer`,用 `valid_answer(raw, schema)` 按 JSON Schema 子集(type、enum、required、properties、additionalProperties=false、items、minLength、maxLength、maxItems,其余关键字拒绝)得出 `answerValid`(`src/buddy/adapters/claude_runner.py:199`-207;`src/buddy/adapters/read_only.py:35`-85)。终态校验与 Worker 相同的显式成功判据(init 已观察、session 相同、subtype success、is_error False、背景任务沉降)(`src/buddy/adapters/claude_runner.py:200`-203)。最终判定在调用方:决策适配器用 `router.validate_answer` 核对答案形状与候选边界(`src/buddy/adapters/decision.py:124`-125)。快速路由:没有。

## 6. 停止与证据

取消:Worker 的 `cancel` 调 `handle.terminate(grace_seconds=8)`(`src/buddy/adapters/claude.py:204`-205);决策适配器对路由句柄用 12 秒宽限(`src/buddy/adapters/decision.py:136`-138)。`terminate` 先对所属进程组 SIGTERM 并轮询,超时后 SIGKILL 再轮询(`src/buddy/adapters/base.py:262`-282);只有创建子进程的对象可发信号,存下的 PID 只是诊断值(`src/buddy/adapters/base.py:187`-192)。runner 把 SIGTERM、SIGINT(Windows 再加 SIGBREAK)翻译成 cancelled 事件(`src/buddy/adapters/claude_runner.py:501`-504)。

原生中断:取消、超时与审阅预算耗尽会向原生 CLI 发送 `interrupt` 控制请求,用新的 2 秒控制预算,失败不影响控制器的停止回执(`src/buddy/adapters/claude_runner.py:135`-147, 450, 454-456)。超时:`execution_deadline` 把 `timeoutSeconds == 0` 变为无限,其余请求与关闭等待保持有限(`src/buddy/adapters/claude_runner.py:157`-164);版本探测 5 秒(`src/buddy/adapters/claude_runner.py:261`-262);auth 读回 10 秒(`src/buddy/adapters/claude_config.py:131`)。

进程退出与停止证据:runner 的 finally 关闭 stdin,等最多 3 秒,`shutdown_confirmed(0.2)` 不成则 `terminate(1)`,再以 0.5 秒确认,把 `processState`(shutdownConfirmed、nativeExitCode)写进结果(`src/buddy/adapters/claude_runner.py:470`-482);status 为 ok 但停止未确认或退出码非 0 时改为 `native-shutdown-failed` 并丢弃回合记录(`src/buddy/adapters/claude_runner.py:485`-489)。进程组消失的观察不以 PID 推断:组长在世时捕获 pgid,组内任一后代存活都算活着,观察不可用时按活着处理(`src/buddy/adapters/base.py:200`-210, 236-260)。收集侧的双重确认:Worker 的 shutdown 要求控制器结果的 `shutdownConfirmed` 为 True 且外层句柄的组确认消失,取消与配额拒绝同样适用(`src/buddy/adapters/claude.py:141`-145);审阅调用同样双确认(`src/buddy/adapters/read_only.py:262`-263)。两边的 ok 都额外要求退出码 0(`src/buddy/adapters/claude.py:161`;`src/buddy/adapters/read_only.py:264`);信号名从负退出码翻译(`src/buddy/adapters/claude.py:403`-410)。runner 的退出码是 0 当且仅当 status 为 ok(`src/buddy/adapters/claude_runner.py:494`)。停止证据进入 `AdapterOutcome.shutdown_confirmed` 与 stopEvidence 结构(`src/buddy/adapters/claude.py:200`-202;`src/buddy/adapters/decision.py:103`-106)。快速路由:没有。

## 7. 续接

Claude Code 的会话续接状态:`ClaudeAdapter.native_resume = False`(`src/buddy/adapters/claude.py:41`);共用侧 `read_only_structured_resume` 默认也是 False(`src/buddy/adapters/base.py:149`)。`prepare` 拒绝 `resumeMode == "native-session"`(`src/buddy/adapters/claude.py:84`-86);runner 只接受 `initial` 与 `reconstructed-new-session`,initial 不得带 previousSessionId,reconstructed 必须分配新的 session id(`src/buddy/adapters/claude_runner.py:340`-348)。

续接依赖的状态与存放:续接语义(`resumeMode`、`previousSessionId`)由服务拥有,写在 turn-input.json 里(`src/buddy/adapters/turn_io.py:141`-146),收集时与回合记录比对(`src/buddy/adapters/claude.py:378`-384);回合记录再记录同样的字段与本次 sessionId(`src/buddy/adapters/claude_runner.py:432`-435);`validate_turn_provenance` 校验 initial 无前驱、reconstructed 的 sessionId 等于 previousSessionId(`src/buddy/adapters/claude.py:245`-250)。原生会话本身存在用户自己的 Claude Code 存储中(`storageScope: "harness-user-store"`),可见性未验证,`_native_session` 如实报告 `resumable: False`(`src/buddy/adapters/claude.py:346`-361)。审阅调用每次也是新 uuid4 会话,没有续接概念(`src/buddy/adapters/read_only.py:129`)。

## 8. 只属于 Worker 回合的东西

问询:Claude 适配器没有接问询桥;`turn_io.inquiry_paths` 存在,但调用它的只有 DSH 与 ZCode 适配器(`src/buddy/adapters/turn_io.py:169`-196;调用点 `src/buddy/adapters/dsh.py:93`-94, 152 与 `src/buddy/adapters/zcode.py:86`)。运行本身不接触问询。

协助与注意请求:三段 `ASSISTANCE_HINTS` 只进 Worker 提示词(`src/buddy/adapters/turn_io.py:28`-41;拼接点 `src/buddy/adapters/claude_runner.py:353`-354);`OUTCOME_SCHEMA` 的 assistance/attention 分支承载请求对象(`src/buddy/adapters/claude_protocol.py:239`-276);被拒的原生权限请求在收集侧把 attentionRequired 传给 Host,completed 结果不能替代它要求的 Host 注意(`src/buddy/adapters/claude.py:169`-182)。审阅调用的提示词不含这些提示。

凭据:`agent_credential` 由 `turn_io.write_turn_files` 写进 harness 私有目录的 agent-credential.json(绝不进模型可见的 turn 输入),并导出 `BUDDY_AGENT_CREDENTIAL_FILE`、`BUDDY_TASK_ID`、`BUDDY_ATTEMPT_ID`(`src/buddy/adapters/turn_io.py:147`-155;设计注释 `src/buddy/adapters/base.py:32`-44)。Worker 的控制器环境不带 `read_only`,这些变量到达控制器进程(`src/buddy/adapters/claude.py:127`;`src/buddy/harness_runtime.py:72`-75);原生子进程的环境由 `claude_config.native_environment` 构造,BUDDY_* 一律不进模型驱动的进程(`src/buddy/adapters/claude_config.py:65`-83, 313-328)。审阅调用的控制器环境带 `read_only=True`,凭据变量被剥离(`src/buddy/adapters/read_only.py:141`;`src/buddy/harness_runtime.py:72`-75)。

检出:executionWorkspace manifest 决定 Worker 的 cwd 与 access(`src/buddy/adapters/base.py:44`-52;`src/buddy/adapters/claude.py:106`-115);`verify_workspace` 在写回合文件时核对 manifest 未变更(read 或带路由决定时要求未变),结果存进 `context.effective_workspace`(`src/buddy/adapters/turn_io.py:199`-207);收集侧在 ok 且有回合记录时 `seal_workspace` 生成 workspaceSeal,失败则整个回合失败(`src/buddy/adapters/claude.py:192`-199;`src/buddy/adapters/turn_io.py:293`-307);manifest 也会进结果(`src/buddy/adapters/claude.py:186`-187)。原生 init 帧的 cwd 必须与分配的工作区一致(`src/buddy/adapters/claude_protocol.py:478`-480)。审阅调用没有 manifest,只有请求的 cwd(`src/buddy/adapters/read_only.py:128`)。

其余接触点:任务书 task.txt 只属于 Worker(`src/buddy/adapters/turn_io.py:134`-137;`src/buddy/adapters/claude.py:111`);回合记录与 provenance 校验只属于 Worker(第 5 节);工件列表(task-specification、turn-result、runner 日志,带大小与 sha256)只由 Worker 收集产出,且仅在确认停止时(`src/buddy/adapters/claude.py:202`, 413-421);配额失败块、nativeAttention、nativeSession 等结果字段是 Worker 发布的(`src/buddy/adapters/claude.py:154`-185);activity.json 两个实际入口都写(Worker 经 `src/buddy/adapters/claude.py:112`,审阅经 `src/buddy/adapters/read_only.py:131`,落盘逻辑同一份 `src/buddy/adapters/claude_runner.py:97`-119)。

## 9. 重复

控制器进程的启动在两个实际入口各写一遍:打开日志、`owned_popen` 同样的参数形态、构造 `ProcessHandle`,仅控制文件路径、cwd 与环境不同(`src/buddy/adapters/claude.py:122`-133;`src/buddy/adapters/read_only.py:144`-152)。控制文件的组装同构:键集高度重叠(directory、nativeRoot、cwd、timeoutSeconds、taskId、attemptId、generation、sessionId、activityFile、spec),差异是 Worker 的 inputFile/outputFile/taskFile/access 对审阅的 readOnlyRequest(`src/buddy/adapters/claude.py:108`-117;`src/buddy/adapters/read_only.py:125`-139);发现模式是第三份(`src/buddy/adapters/claude.py:279`-280),共用的 no-tool 版本还有第四份但对 Claude 不可达(`src/buddy/adapters/read_only.py:178`-189)。

句柄 deadline 在两处各写:Worker 的"0 为无限"对审阅的"加 10 秒余量"(`src/buddy/adapters/claude.py:134`-136;`src/buddy/adapters/read_only.py:153`)。收集侧的"读控制器 stdout 的 JSON 结果 + 双重停止确认 + 状态三分"两处各写:Worker 的 `_read_result`(512KB 上限)对审阅的 `_evidence_json`(256KB 上限)(`src/buddy/adapters/claude.py:139`-167, 391-400;`src/buddy/adapters/read_only.py:248`-266, 212-221)。

runner 内部的"划定用户消息边界 → 发送 user 帧 → 重放缓冲帧并拒绝早期模型输出 → 等待 result → 关闭 stdin → 排空到流结束"在 Worker 分支与 `_read_only_call` 各写一遍(`src/buddy/adapters/claude_runner.py:352`-383;`src/buddy/adapters/claude_runner.py:175`-198)。spec 的 provider/model/effort 核对写了三遍:适配器 `prepare`、runner Worker 分支、`_read_only_call`(`src/buddy/adapters/claude.py:77`-81;`src/buddy/adapters/claude_runner.py:330`-335;`src/buddy/adapters/claude_runner.py:171`-174)。第三方覆盖与 settings policy 的拒绝在适配器与 runner 各查一遍(`src/buddy/adapters/claude.py:89`-95;`src/buddy/adapters/claude_runner.py:229`-236)。回合记录的导入有两份近似实现:通用 `turn_io.read_turn` 与 Claude 自带的 `_read_native_turn`(后者另加严格 JSON 解码与控制文件 sessionId 对齐)(`src/buddy/adapters/turn_io.py:254`-290;`src/buddy/adapters/claude.py:364`-388)。新会话的 uuid4 分配两处各写(`src/buddy/adapters/claude.py:114`;`src/buddy/adapters/read_only.py:129`)。cancel 的 terminate 包装两处(`src/buddy/adapters/claude.py:204`-205;`src/buddy/adapters/decision.py:136`-138,基类默认 `src/buddy/adapters/base.py:183`-184)。

## 10. 对照表

| 步骤 | Worker 回合 `start` | 审阅路由 `start_read_only_structured` | 快速路由 `start_no_tool_structured` |
| --- | --- | --- | --- |
| 1 命令组装(控制器进程) | `python -m buddy.adapters.claude_runner --control`(claude.py:125-129) | 相同(read_only.py:146-148) | 没有 |
| 1 控制器环境 | 不同:不带 read_only,凭据变量进控制器(claude.py:127) | 不同:账户环境再 read_only=True,凭据剥离(read_only.py:123,141) | 没有 |
| 1 私有目录 | claude-private(claude.py:105) | 不同:review-native(read_only.py:126) | 没有 |
| 1 控制文件 | 不同:claude-control.json 含 inputFile/taskFile/outputFile/access(claude.py:108-117) | 不同:readonly-control.json 含 readOnlyRequest(read_only.py:125-139) | 没有 |
| 1 原生 CLI 参数 | 相同:同一份 execution_args(claude_runner.py:255-258) | 相同,但 outputSchema 来自请求、网络白名单清空(claude_config.py:255-258;claude_runner.py:251-252) | 没有 |
| 1 句柄 deadline | 不同:timeoutSeconds,0 为无限(claude.py:134-136) | 不同:预算 + 10(read_only.py:153) | 没有 |
| 2 会话建立 | 相同:新 uuid4 --session-id,校验 v4 与 init 回读(claude.py:114;claude_runner.py:243-249;claude_protocol.py:467-487) | 相同(read_only.py:129) | 没有 |
| 2 模型与提供方核对 | 相同:initialize 目录核对 provider/model/effort,账户须第一方(claude_runner.py:330-335, 316-320) | 相同(claude_runner.py:171-174) | 没有 |
| 2 输入送入 | 不同:HARNESS_PREAMBLE+ASSISTANCE_HINTS+task.txt+turn_input JSON(claude_runner.py:353-358) | 不同:仅请求 prompt(claude_runner.py:177-179) | 没有 |
| 3 工具集与权限模式 | 不同:按 manifest access 选 WRITABLE/READONLY 与 acceptEdits/default(claude.py:115;claude_config.py:287,292-302) | 相同(固定 read;另禁 MCP/Web/Agent,claude_config.py:305-308) | 没有 |
| 3 沙盒与联网 | 相同:sandbox_settings,网络白名单为包注册表(claude_runner.py:250-253) | 不同:白名单清空(claude_runner.py:251-252) | 没有 |
| 4 事件读取与校验 | 相同:Connection 与 TurnEvidence 单份共用(claude_protocol.py:91-237, 435-539) | 相同 | 没有 |
| 4 活动/用量/额度 | 相同:_activity、token_usage、quota_candidate(claude_runner.py:97-119, 460-469) | 相同,但 usage 只发表 toolCalls(claude_runner.py:183, 204-207) | 没有 |
| 5 最终结果约定 | 不同:OUTCOME_SCHEMA+回合记录+provenance 双侧校验(claude_config.py:309;claude.py:207-250, 364-388) | 不同:请求 schema+answerValid,调用方再判定(claude_runner.py:204-207;decision.py:124-125) | 没有 |
| 6 取消与超时 | 相同:interrupt、execution_deadline、cancelled/deadline 分类(claude_runner.py:135-164, 445-456) | 相同 | 没有 |
| 6 停止证据 | 相同:processState+外层组消失双确认(claude.py:145;claude_runner.py:470-489) | 相同(read_only.py:262-263) | 没有 |
| 7 续接 | 不同:resumeMode/previousSessionId 校验,永不续接原生会话(claude.py:84-86;claude_runner.py:340-348) | 没有:每次新会话(read_only.py:129) | 没有 |

快速路由一列全为"没有",原因是文首所述:Claude Code 未声明 `no_tool_structured`,基类入口抛 `UNSUPPORTED_ADAPTER`(`src/buddy/adapters/base.py:152`-154),决策适配器的快速分支也会先拒绝(`src/buddy/adapters/decision.py:36`-38)。

## 11. 抽取时的观察

控制器子进程的编排(日志、`owned_popen`、`ProcessHandle`、deadline)在两个实际入口已经同构(第 9 节第一条,`src/buddy/adapters/claude.py:122`-133;`src/buddy/adapters/read_only.py:144`-152),可以直接成为每个 harness 一个通用运行模块的骨架,差异只剩控制文件路径、cwd 与环境开关。

runner 内部对原生 CLI 的启动与停止(版本探测、settings 写入、owned_popen、Connection、权限拒绝回调、finally 收尾)是三个分支共用的单份代码(`src/buddy/adapters/claude_runner.py:259`-313, 460-494),说明"harness 运行一次"的底层已经统一,分叉只发生在输入与结果处理,这层不必再抽。

用户消息边界、结果等待与流排空在 Worker 与审阅两分支重复(第 9 节,`src/buddy/adapters/claude_runner.py:352`-383 与 175-198),可以合并为一个"送输入、收结果"原语;两处差异只有回调查活动的方式和审阅的预算检查(`src/buddy/adapters/claude_runner.py:181`-189)。

模型、提供方与推理强度的核对写了三遍(适配器 prepare、runner 两个分支,第 9 节);通用运行模块应只保留 runner 的目录核对一份,prepare 里的角色前置校验(必须受治理回合、必须第一方 provider)留在角色侧(`src/buddy/adapters/claude.py:77`-88)。

结束约定是角色差异而非运行差异:Worker 用固定 OUTCOME_SCHEMA 加回合记录与 provenance,审阅用调用方 schema 加 answerValid(第 5 节);通用运行模块应接受 schema 参数与结果校验回调,而不是内置其中一种(`src/buddy/adapters/claude_config.py:305`-309;`src/buddy/adapters/claude_runner.py:406`-435 对 204-207)。

停止证据的判定已经是统一原语:双确认、interrupt、terminate 顺序与退出码判据(第 6 节,`src/buddy/adapters/base.py:187`-310;`src/buddy/adapters/claude_runner.py:460`-494),抽取时直接复用,不需要按角色分叉。

凭据、检出核对与密封只属于 Worker 授权(第 8 节,`src/buddy/adapters/turn_io.py:147`-155, 199-207, 293-307),必须留在角色一侧;通用运行模块不应看到 `agent_credential`,审阅路径已经示范了用 `read_only=True` 的控制器环境把它剥掉(`src/buddy/adapters/read_only.py:141`)。

障碍:收集侧的结果形状分叉大——Worker 发布回合记录、用量、配额、注意与工件(`src/buddy/adapters/claude.py:139`-202),审阅发布 rawAnswer/answerValid(`src/buddy/adapters/read_only.py:248`-287),共用 collect 里还有一段 Claude 用不到的 no-tool 证据保留(`src/buddy/adapters/read_only.py:224`-245);先定义一份公共结果文件,再让角色扩展,否则通用模块会背上三种形状。

障碍:Claude 自带的 `_read_native_turn` 与通用 `turn_io.read_turn` 是两份近似实现(第 9 节),抽取时要决定以哪份为准,否则通用模块里会出现第三种回合导入(`src/buddy/adapters/claude.py:364`-388;`src/buddy/adapters/turn_io.py:254`-290)。

障碍:发现模式是同一 runner 的第四条路径(第 1 节,`src/buddy/adapters/claude.py:272`-302),三个角色入口的清单没有覆盖它;通用运行模块若只按三个入口设计,发现模式要么被排除,要么变成第五个特例。

审阅入口当前不会被决策适配器选中,因为 `read_only_structured_verified` 未声明(第 1 节,`src/buddy/adapters/decision.py:53`-54);按 ADR-023 第 2 条,抽取通用运行模块时应保持这种"能力声明决定资格"的边界,运行模块不因此增删行为。
