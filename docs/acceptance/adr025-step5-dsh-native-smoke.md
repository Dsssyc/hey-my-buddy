# ADR-025 第五步：DSH 真实 Worker 回合与实时通道

2026-10-07，使用已安装 DSH 0.1.5-rc.1，deepseek-official / deepseek-flash / off，真实模型运行共1次。准备探针v1至v4没有发送模型输入，失败记录保留；v5经新请求/结果、实际私有Store/BoardService、Worker与controller端点交付。没有安装升级日常运行时，没有改变用户配置、登录或凭据，没有读取凭据文件内容。HOME保持原来源，仅本次DSH_HOME是私有目录；用户自己交互使用DSH的配置不受影响。

| 项目 | 实际证据 |
| --- | --- |
| 宏任务/attempt | run 64ee31d2-c2ab-47b9-9a75-6c50863bf7e5；attempt 4f0cee44-2643-4ab1-ae71-6bca4a177a01；completed、resultDelivered=true |
| 原生结果 | modelStarted=true、end.status=ok、exitCode=0；summary=ADR025_DSH_WORKER_OK |
| 工具与交付 | 真实文件read工具1次；buddy_finish_turn签名收据通过、rootSessionMatched=true；sessionClose=acknowledged |
| 问询 | 同回合服务具名请求，journal依次queued→delivered→answered，buddy_answer_inquiry已核验并导入公共回答 |
| 两层停止 | RunResult原生groupState=gone；Worker持有controller的停止确认true；缺失PID只作后续观察，不作为停止证明 |
| 用量 | dsh/session-record，7条记录，input 95,482（含cached 81,792）、output 657；完整，未填入未知推理量 |

controller的登记socket确切路径、device/inode与hostPid在原始ready/摘要中留存，运行后该文件消失。Worker同一进程的端点路径曾在driver内存观察到，但探针在最终保存前超时，保留证据无法恢复该确切路径，所以不把这一项写成“已经逐文件证明删除”。另行不调用模型的实际WorkerLiveRuntime register/stop检查留存其地址、文件身份及存在→消失；两个真实fixture controller也保存相同生命周期证明，这些不能代替本次DSH Worker文件的缺失观察。

原始request在正常回合收尾后未保留；真实RunResult、turn输出、问询journal、controller ready、原生provenance、会话用量元数据与启动日志已逐文件SHA留存于<checkout>/tmp/adr025-host/<phase>/retained-dsh-native/。私有fixture clone中的Git输入/输出快照对象另以bundle保留；没有把这些快照记录称作源码提交或业务交付文件。原生controller运行源码为ca839f40后的Host公共接线，具体源码现已提交为5776a9b及后续退休整合；两次Claude冒烟基线更早，另文明确未覆盖切换后的实时通道。

本次继续已接受的DSH行为差异：问询可能只在checkpoint送达；快速路由带DSH系统提示；原生续接目前仍不接入；所有hey-my-buddy启动的DSH运行关闭session-title-llm与session-telemetry-otel。没有发现新的行为差异。只读命令强制范围及工具名归类、未知新工具行和可选用量来源沿用第三四步已验收的规则，本次write范围read工具不能当作只读沙盒证据。

补充：ADR-027 合入后在 2278c40 的实际运行代码上，为补齐前次漏存的 Worker 端点地址同目的最小重跑1次（本步 DSH 真正调用模型累计2次，未再调用 Claude Code）。run 98f9a75b-f891-4c43-a5f5-241f27f771e0、attempt 712ef86f-f3dc-428a-9270-8628e5a6d6cc、Worker adr025-dsh-smoke-worker；只读一个最小 fixture 文件、检查点、问询回答与完成签收。modelStarted true、native ok/退出0、native group gone、外层停止 true、签收验证与根会话匹配 true、完成 disposition completed；服务最后确认问询 answered。没有改变日常 DSH、登录或凭据；HOME 沿用，DSH_HOME 由运行模块强制私有，两个已接受的启动项照常关闭。

两个确切端点在启动前/运行中各记下地址：Worker /tmp/c_two_ipc/cc17cc440bac1d596f04bea860311c84d52c46a.sock，controller /tmp/c_two_ipc/cc17da127a00b0f4fa84211b147ec3cb1f41b7a.sock。前者 presentBeforeRun true、后者 presentDuringRun true；Worker 线程结束、实际所选 Worker 与持有者一致、两层停止证据确认后，两个 presentAfterRun 均 false，Host 再查两个确切路径仍不存在。其余 /tmp/c_two_ipc 文件未扫描归属或清理；前一次遗漏地址的证据缺口保留，不改写成前一次已证明。

真实 read 工具1次；私有会话记录用量 nativeRecords7、input83597（含 cached71424）、output596，completeness complete，source dsh/session-record。没有新增 zstd 系统命令依赖。准备探针 v6 把 tracked fixture 文件误放 includeUntracked，被提交阶段拒绝，没有 run、模型或端点；新命名 v7 去掉该选择器后完成。第一次整理证据误用无 pydantic 的系统 Python，解码前失败；换已锁定 uv 环境后严格解码与证据断言通过。原始失败与后续成功分别保留。28份命名材料（含6份帧引用证据与私有fixture Git bundle）、SHA与最终严格解码摘要留在 <checkout>/tmp/adr025-host/<phase>/retained-dsh-endpoint-rerun/ 与 dsh-endpoint-rerun2-evidence.json；私有任务根只由 Host 按创建时记下的确切路径回收。
