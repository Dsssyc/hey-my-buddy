# ZCode 私有会话关闭自动执行后，guide 与回合结束队列的真实验证

## 问题

[上一份续查](2026-10-31-zcode-queue-initialization.md)发现成功 guide 也可能回 queue，且原生宿主默认会自动排空。开发者选定 A：只对黑板创建的私有会话设置原生 setAutoDrain(false)，按事件确认送达，结束后 CAS 删除残留。开发者批准仅一次 GLM-5.3-Flash / low 的真实根回合，失败不重跑；本记录核对这三项前提，不代表产品适配器或安装已验收。

## 看了什么

2026-10-31，ZCode app 随包 CLI 0.17.0。先运行 `tests/probes/zcode_realtime_inquiry_live.py --preflight-only`，确认原生目录、私有 MCP 挂载及 CAS 关闭 autoDrain；另在本地校验 MCP 协议和签名回答回执，均未发送模型输入。随后仅运行一次 `--run-approved-once`，一次性批准记录在发唯一 session/send 前以排他创建写入，之后没有重跑。

原生进程沿产品的 native_environment 白名单读取用户现有提供方配置，以原生 setModel/setThoughtLevel 选择获批模型；没有读取、复制或输出提供方密钥。工作目录、会话数据库、MCP、协议报告均在检出 tmp 的私有根。协议报告只保留指定的非秘密字段，提供方名称在固定夹具中改为占位。没有修改用户自己的会话、设置或默认值，没有连接日常黑板。

原始脱敏报告在 `tmp/zcode-inquiry-approved-A/e3460b87c8c24a01bfd9c12bde71b0fb/report.json`，固定应答为 `tests/python/buddy/harnesses/zcode/fixtures/inquiry-live-app-server-0.17.0.json`。只读检查了这次私有原生数据库的消息数，没有读取日常会话记录。协议准备只补读了安装包导出的 queued/drained 与 v4 wire schema，未读模型、工具、鉴权实现，没有上网。

## 看到的事实

- 〔实测〕沿现有 session/create 创建私有会话后，以 v4 会话快照的 revision/logEpoch 执行 setAutoDrain(false)，命令 accepted；后续快照 queue.autoDrain 为 false。无须改用户默认值，也无须先启动模型来设置该私有会话参数。
- 〔实测〕模型先调用探针的门控 MCP 工具。在工具等待时发 sendText/guide A，命令 accepted，result.delivery 实际为 queue；原生 turn.steerQueued 的 inputId 对应该 commandId，pendingInputId 随后出现在 turn.steerDrained.pendingInputIds，两个事件的 targetTurnId、事件 session/turn 均为原始输入所绑定的根回合。drained 还携带 drainedInputs 与 injectedMessageIds，属于这一批输入的证据。
- 〔实测〕模型随后调用本次挂载的 hey_my_buddy_answer_inquiry，使用准确 inquiryId 和仅在 guide 中给出的随机标记。根工具的成功、未截断结果携带了通过签名验证的回答回执，控制器按现有 InquiryBridge.record_answer 记录；不是从普通助手文字推断回答。
- 〔实测〕门控等待时另发 sendText/queue C，其回执同样为 accepted/queue。原回合以 resultType: success 结束，等待半秒后，C 仍在 held 队列，autoDrain 仍为 false，未出现第二个根 turn.started。以最新 v4 快照的 revision/logEpoch 对 C 的准确 queueItemId 发 deleteQueueItem，得到 accepted；再读快照，C 已不在队列，之后也没有第二个根回合。
- 〔实测〕六个断言均成立：关闭自动执行、guide drain 到根、模型工具回答、根结束后仍 held、CAS 删除后消失、仅一个根。session/close 及拥有的进程组停止均确认，探针退出 0。私有原生库有 4 条 assistant 消息；此数不等同于含辅助调用、重试在内的 HTTP 请求总数。

〔读包〕wireVersion 3 的 fragment 带 fragmentIndex、fragmentCount、logicalBytes、crc32 checksum 和 dataBase64；原生逻辑帧上限为 16 MiB、1024 片。产品的分片解码按这些字段实现，构造夹具验证校验和与大小边界；本次真实回合只观测到 complete 小快照。

## 取了什么，没取什么

取 setAutoDrain(false) 作为开发者已授权的私有回合协议参数；取 drained 的根身份及 pendingInputIds 对账作为送达证据；取原生 held 条目、revision/logEpoch 和删除后重读作为清理依据，写入[执行计划](../design/zcode-realtime-inquiry/plan.md)并继续实现。没有把 queue 回执当作回退，也没有把删除 accepted 当作删除已完成。

没有验证最终答复段的所有竞争、缺事件、错误回执、删除假成功、取消、原生续接、大快照分片或其他模型/提供方；这些边界由受影响的构造夹具检查，不能写成真实模型已覆盖。本次验证发生在私有协议探针，产品驱动的实现、测试与安装是另外的状态；不另做真实模型调用，后续付费实验仍须开发者逐次批准。

## 时效

ZCode CLI 或 v4 wire 版本、私有会话持久化与 autoDrain 语义、guide/drained 格式、CAS 规则或该模型行为变化时需要重查。以后复验写新的续查，不改这次已记录的实际结果。
