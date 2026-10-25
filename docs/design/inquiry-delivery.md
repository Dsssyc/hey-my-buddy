# 问询投递诊断

2026-10-22；诊断源码为 `1fc196674ba63fcaf661da5083836004fe6e98a7`。本记录只提出修法，没有修改产品、测试或日常数据。时间均为 UTC。观察来自 Host 已导出的消息与 get、原处执行日志、按原生线程号定位的 Codex 会话记录，以及本检出中的模型无关复现。源码解释的是此提交的行为；历史事故另按当时的安装与验收记录判断。

## 结论与统计口径

导出实际有 40 条：Codex **24** 条（12 answered、1 delivered、11 unavailable），ZCode 16 条（2 answered、14 unavailable）。委派说明里的“Codex 23 条”与其分项之和不符；以 `msg-tasks.json` 对消息的 taskId 关联后的计数为准。导出跨越 2026-10-16 至 22 日，并非 22 日一天的提问。

Codex 的 11 条 unavailable 中，**7 条已在同一原生回合内送达并答复，2 条是在执行交回后才提问，2 条没有执行桥接收证据**。最后 2 条与当时已经证实的实时通道登记表缺陷吻合，但缺少这两次 inquire 的原始返回与服务通道时间线，登记表是推断归因，不能记为逐条证实。额外那条 delivered 也已经答复：`2ec929b6` 的“已送达，等待答复”是过期消息投影，不能用它证明模型漏答。

| 类别 | 11 条 unavailable 中的条数 | 额外 delivered | 证据等级 |
| --- | ---: | ---: | --- |
| 原生日志已有答案，服务消息仍旧，结束时归为无法送达 | 7 | 1 | 问询日志、原生输入与答复调用直接相互印证；当前机制已复现 |
| 原生回合及执行均已交回，随后才提问 | 2 | 0 | 原生结束、turn 交回与提问时间直接证实 |
| 原生回合仍运行，问题没有进入执行桥的留存证据 | 2 | 0 | 未进入日志与原生输入为观察；0.42.1 登记表缺陷为推断 |
| 已证实因静默帧、Lead 等待、桥未激活或 steer 拒绝而漏投 | 0 | 0 | 本组未取得相应逐条证据；不能从 unavailable 原因码反推这些机制 |

## 证据位置与读法

`<exports>` 指 Host 检出的 `tmp/inquiry-diagnosis/`；本次逐条读取 `msg-unavailable.json`、`msg-delivered.json`、`msg-answered.json`、`msg-tasks.json` 以及下表 taskId 对应的 `get-<taskId>.json`。get 中取 `audit.turns[]` 的 attemptId、turnId、generation、createdAt、updatedAt、provenance、turnResultPath，不能把 `task.activity` 的最后一次活动直接当作某个旧 attempt 的时间线。导出未提供一份完整的历次活动事件流，也没有历次 inquire 的桥返回。

`<attempt>` 是 `<state>/attempts/<taskId>/<attemptId>/`，由 get 的 `audit.turns[].turnResultPath` 定位。以下 `J` 表示这个目录原处的 `inquiry.results.jsonl`，`O` 表示 `turn-output.json`；引用 J 的行号是原文件行号。本次未复制这些文件。逐条核对了 `execution.stderr.log` 与 `execution.stdout.log`：下表涉及的 12 个 attempt 均为 stderr 0 字节、stdout 133 字节，仅有 `durable completion receipt written for attempt <attemptId>`，没有可定位到提问时刻的执行事件。因此没有拿执行 stdout 的文件时间充当 native turn 结束时间。

所有这 12 个 attempt 的原生会话首行 `session_meta.payload.cli_version` 都是 **Codex CLI 0.162.1**，originator 为 `hey_my_buddy`。原生记录以 get.provenance.nativeThreadId 和 nativeTurnId 双重定位；下文 N 编号的行号对应原处 JSONL，读取类型与必要字段，没有复制会话文件。N 的日期目录和文件名是本地时间命名，行内 timestamp 才用于比较 UTC。

### 消息与执行绑定

`attempts` 是服务 `_message_update` 被调用的累计次数，每次同值投影也递增（`store.py::_message_update`），**不是 turn/steer 次数或模型重试次数**。消息 updatedAt 是服务消息的更新时间，不能替代 journal.deliveredAt 或 answer.at。

| messageId | inquiryId | taskId | attemptId | attempts | createdAt | updatedAt | 导出状态 |
| --- | --- | --- | --- | ---: | --- | --- | --- |
| 5d4f7824-19e1-4ac6-9a02-c60c3300a821 | reuse-design-progress-1 | 0feecd78-bc15-4f12-aa53-ee9c000dbe2c | 175d8cf4-5765-4994-916f-bccffa899013 | 3 | 2026-10-21T16:56:02.451Z | 2026-10-21T17:02:39.851Z | unavailable |
| 9e29454d-5733-4b84-97a4-dc45ef4605a5 | m4-lifecycle-assertion-1 | e9525939-2150-40e3-8782-02429b297cfb | b85110f8-2943-4b38-8664-1fdd11358ad9 | 3 | 2026-10-20T13:21:29.755Z | 2026-10-20T13:25:23.547Z | unavailable |
| ac14a0b8-d8bd-4ad2-8a17-01943cd95c17 | c-cursor-writer-1 | 2858e526-aa4f-40df-8b50-744730d017f1 | 1f1cd3bd-ac64-4604-b25d-d9b8c8e7f232 | 0 | 2026-10-19T04:37:30.356Z | 2026-10-19T04:37:30.356Z | unavailable |
| 7cd124f4-7717-4488-9707-31eb4876eb5c | session-a-q1 | da71f9cf-4b54-41de-b3e6-74af69ccf784 | 7ea0e3f5-91e9-4bad-b22e-fc6efe4087f2 | 3 | 2026-10-19T02:26:06.875Z | 2026-10-19T02:35:33.143Z | unavailable |
| 67845cec-ce7e-4879-9222-658611d9b55b | session-b-q1 | bd808336-d084-4bb3-8786-c69aebcec347 | 3f3ccd98-c27d-48dc-976a-dee325384c71 | 0 | 2026-10-19T02:24:54.549Z | 2026-10-19T02:24:54.549Z | unavailable |
| 1bd9c643-18c4-42a2-971e-cc72126439cf | b3-q5 | c23b3dae-4f00-4877-a1ae-61c89d74291e | e5450482-cb51-4151-9289-c78a73e24e73 | 2 | 2026-10-18T09:04:38.922Z | 2026-10-18T09:29:42.528Z | unavailable |
| 67c37d96-e703-4dfe-be84-ce1a96f27d6f | batch3-turn3-harness-logo-owner | cda72b0d-e2fe-4416-9664-0a3acc5bc650 | 81678661-72d2-4a46-a06e-0531ae736a95 | 2 | 2026-10-18T09:03:40.930Z | 2026-10-18T09:04:40.069Z | unavailable |
| 9d9d9dc8-fa2e-4889-8487-79814ba8cb53 | b3-q2 | c23b3dae-4f00-4877-a1ae-61c89d74291e | 217d15ba-80f1-4963-a69d-3639e55fefb9 | 2 | 2026-10-18T08:36:37.534Z | 2026-10-18T08:53:08.752Z | unavailable |
| 620eb14a-6d87-4482-a2d9-5bd04458c6a5 | mt4-q1 | 68cd1a06-9026-40f5-8d40-3bc65125cc5d | e25af906-ca84-4072-8138-4033c0eaad5c | 2 | 2026-10-18T05:39:10.558Z | 2026-10-18T05:48:02.224Z | unavailable |
| db4f8b52-3e91-4bae-b633-b8511c9240ca | repro-q2 | e97cc1b0-b8dd-468b-9d87-a1272e6d9b06 | 78d2b7de-355e-49ef-96b4-47da67bcb12c | 0 | 2026-10-16T18:02:33.855Z | 2026-10-16T18:02:33.855Z | unavailable |
| 5e04475a-0ed4-417e-b086-8243b99152b2 | q-console-list-motion-1 | 29b947aa-0bea-4643-ac37-a85952b46629 | e0eca088-acda-47a7-ba6d-e65953d59b0e | 0 | 2026-10-16T17:31:25.870Z | 2026-10-16T17:31:25.870Z | unavailable |
| bc620007-c873-4056-9a6c-29dd46c22ffb | owner-proposal-review-close-1 | 2ec929b6-7ee1-4f24-a6a3-072dc6240bae | f160eb6b-7cc2-4f88-a22a-5c852212b79a | 2 | 2026-10-21T03:05:26.077Z | 2026-10-21T03:05:26.127Z | delivered |

### 原生回合与服务回合的起止

服务回合起止取对应 get 的 `audit.turns[]` createdAt/updatedAt；它覆盖准备和交回等阶段，不等于内核进程的创建/退出时刻。`spawn.marker` 只提供进程身份，没有独立的墙钟起止。N 的 task_started/task_complete 给出原生回合起止；它们证明相应回合何时结束，不单独充当进程组停止证明。

| messageId 前缀 | 服务回合开始 → 交回 | 原生回合开始 → 完成 | 原生文件编号与行号 |
| --- | --- | --- | --- |
| 5d4f7824 | 2026-10-21T16:47:32.908Z → 2026-10-21T17:02:39.850Z | 2026-10-21T16:47:36.773Z → 2026-10-21T17:02:38.073Z | N1，2 / 185 |
| 9e29454d | 2026-10-20T13:09:08.696Z → 2026-10-20T13:25:23.546Z | 2026-10-20T13:09:11.844Z → 2026-10-20T13:25:22.313Z | N2，2 / 579 |
| ac14a0b8 | 2026-10-19T03:53:30.485Z → 2026-10-19T04:31:20.627Z | 2026-10-19T03:53:32.747Z → 2026-10-19T04:31:18.900Z | N3，2 / 1104 |
| 7cd124f4 | 2026-10-19T02:25:34.718Z → 2026-10-19T02:35:33.143Z | 2026-10-19T02:25:38.442Z → 2026-10-19T02:35:31.154Z | N4，522 / 713 |
| 67845cec | 2026-10-19T02:15:34.385Z → 2026-10-19T02:23:09.861Z | 2026-10-19T02:15:38.490Z → 2026-10-19T02:23:08.681Z | N5，2 / 240 |
| 1bd9c643 | 2026-10-18T08:56:18.705Z → 2026-10-18T09:29:42.527Z | 2026-10-18T08:56:21.738Z → 2026-10-18T09:29:35.646Z | N6，578 / 1092 |
| 67c37d96 | 2026-10-18T08:33:21.765Z → 2026-10-18T09:04:40.068Z | 2026-10-18T08:33:24.888Z → 2026-10-18T09:04:37.882Z | N7，2 / 750 |
| 9d9d9dc8 | 2026-10-18T08:29:53.541Z → 2026-10-18T08:53:08.751Z | 2026-10-18T08:29:55.729Z → 2026-10-18T08:53:04.827Z | N6，223 / 575 |
| 620eb14a | 2026-10-18T05:28:50.130Z → 2026-10-18T05:48:02.223Z | 2026-10-18T05:28:51.514Z → 2026-10-18T05:47:59.866Z | N8，2 / 624 |
| db4f8b52 | 2026-10-16T18:01:10.945Z → 2026-10-16T18:03:46.189Z | 2026-10-16T18:01:12.760Z → 2026-10-16T18:03:43.794Z | N9，2 / 85 |
| 5e04475a | 2026-10-16T17:06:46.709Z → 2026-10-16T17:46:08.174Z | 2026-10-16T17:06:48.140Z → 2026-10-16T17:46:01.804Z | N10，2 / 1245 |
| bc620007 | 2026-10-21T02:58:56.651Z → 2026-10-21T03:06:59.134Z | 2026-10-21T02:59:00.141Z → 2026-10-21T03:06:57.483Z | N11，2 / 135 |

原生文件索引：

- N1：`~/.codex/sessions/2026/10/22/rollout-2026-10-22T00-47-36-01a15f5c-719c-7da2-818d-a16d19234c69.jsonl`。
- N2：`~/.codex/sessions/2026/10/20/rollout-2026-10-20T21-09-11-01a1596e-1ecf-7e73-8951-c6e8eb69d8bd.jsonl`。
- N3：`~/.codex/sessions/2026/10/19/rollout-2026-10-19T11-53-32-01a1524b-0c04-76b2-8ccc-6f25916f50e3.jsonl`。
- N4：`~/.codex/sessions/2026/10/19/rollout-2026-10-19T09-44-23-01a151d4-cccd-7e93-8aa0-86de9cce60b7.jsonl`。
- N5：`~/.codex/sessions/2026/10/19/rollout-2026-10-19T10-15-38-01a151f1-6927-76d1-b197-4caaa65c0a9e.jsonl`。
- N6：`~/.codex/sessions/2026/10/18/rollout-2026-10-18T15-59-30-01a14e05-df9c-7f72-b540-e45004b9fec3.jsonl`。
- N7：`~/.codex/sessions/2026/10/18/rollout-2026-10-18T16-33-24-01a14e24-ea68-76d2-be4a-610770e0e4ad.jsonl`。
- N8：`~/.codex/sessions/2026/10/18/rollout-2026-10-18T13-28-51-01a14d7b-f312-7272-adaa-9546d0938a6a.jsonl`。
- N9：`~/.codex/sessions/2026/10/17/rollout-2026-10-17T02-01-12-01a145e0-072b-7462-a1dc-76bebf4c8384.jsonl`。
- N10：`~/.codex/sessions/2026/10/17/rollout-2026-10-17T01-06-47-01a145ae-37a8-7022-8e7b-c9ff57104393.jsonl`。
- N11：`~/.codex/archived_sessions/rollout-2026-10-21T10-58-59-01a15c65-d3db-7872-afd2-43f6a08d8bfc.jsonl`。

## 一、7 条已答却显示无法送达，另有 1 条已答却显示等待

以下 J 都有完整的 `queued → delivered → answered`。逐条比对 taskId、attemptId、generation、服务 turnId、questionSha256 与导出 questionSha256，并比对 delivered.nativeCorrelation 的 native thread/turn 与 get.provenance；没有把其他回合或同名问题的答案混入。下面 native 输入是根回合的 user message，答复是含相同 inquiryId 的根调用，J 中 `answer.via` 均为 `tool:hey_my_buddy_answer_inquiry`，有独立 toolCallId。

| 消息 | J 行号 | 排入桥 → 原生确认送达 → 答复落盘（UTC） | 原生输入 / 答复调用证据 |
| --- | --- | --- | --- |
| 5d4f7824 / reuse-design-progress-1 | 1 / 2 / 3 | 2026-10-21T16:56:02.457661Z → 2026-10-21T16:56:02.591632Z → 2026-10-21T16:56:16.072469Z | N1：140 行 2026-10-21T16:56:04.290Z；142 行 2026-10-21T16:56:16.042Z |
| 9e29454d / m4-lifecycle-assertion-1 | 1 / 2 / 3 | 2026-10-20T13:21:29.757803Z → 2026-10-20T13:21:29.869518Z → 2026-10-20T13:21:42.558058Z | N2：503 行 2026-10-20T13:21:30.324Z；507 行 2026-10-20T13:21:42.550Z |
| 7cd124f4 / session-a-q1 | 1 / 2 / 3 | 2026-10-19T02:26:06.881889Z → 2026-10-19T02:26:06.944550Z → 2026-10-19T02:26:12.687175Z | N4：545 行 2026-10-19T02:26:07.141Z；547 行 2026-10-19T02:26:12.661Z |
| 1bd9c643 / b3-q5 | 7 / 8 / 9 | 2026-10-18T09:04:38.924954Z → 2026-10-18T09:04:39.084986Z → 2026-10-18T09:04:45.350078Z | N6：738 行 2026-10-18T09:04:39.378Z；740 行 2026-10-18T09:04:45.332Z |
| 67c37d96 / batch3-turn3-harness-logo-owner | 10 / 11 / 12 | 2026-10-18T09:03:40.933510Z → 2026-10-18T09:03:40.973816Z → 2026-10-18T09:03:46.629544Z | N7：735 行 2026-10-18T09:03:42.240Z；737 行 2026-10-18T09:03:46.614Z |
| 9d9d9dc8 / b3-q2 | 4 / 5 / 6 | 2026-10-18T08:36:37.537182Z → 2026-10-18T08:36:37.714832Z → 2026-10-18T08:36:48.217574Z | N6：331 行 2026-10-18T08:36:38.378Z；333 行 2026-10-18T08:36:48.203Z |
| 620eb14a / mt4-q1 | 1 / 2 / 3 | 2026-10-18T05:39:10.561451Z → 2026-10-18T05:39:10.646523Z → 2026-10-18T05:39:17.849090Z | N8：416 行 2026-10-18T05:39:13.208Z；419 行 2026-10-18T05:39:17.835Z |
| bc620007 / owner-proposal-review-close-1 | 1 / 2 / 3 | 2026-10-21T03:05:26.098730Z → 2026-10-21T03:05:26.117299Z → 2026-10-21T03:05:47.029651Z | N11：106 行 2026-10-21T03:05:28.308Z；118 行 2026-10-21T03:05:46.993Z |

`5d4f7824`、`9e29454d`、`7cd124f4`、`1bd9c643`、`67c37d96`、`9d9d9dc8`、`620eb14a` 的答案都早于各自原生回合结束，导出消息却仍 answer:null、state:unavailable。送达耗时是从创建起约 44–181 ms，均有 turn/steer 的成功关联，不支持“回合已完成才尝试送”“桥未激活”“steer 被拒”这几种解释。原生 user message 的写入可能晚于 RPC 接受和 journal.deliveredAt，不能把那几秒差当作运输失败。

`bc620007` / `owner-proposal-review-close-1` 的 J 第 3 行在 2026-10-21T03:05:47.029651Z 已记 answer，原生 N11 第 118 行在 03:05:46.993Z 发起对应答复调用。`O.provenance.unansweredDeliveredInquiries:0` 与此一致。还留存的 `<state>/harnesses/codex/micro-tasks/8990e6ce22dfb31c38f5f22de71ff502fd8b7ae368bead63b4b053c494a8d5bf/attempts/f160eb6b-7cc2-4f88-a22a-5c852212b79a/run-c44de143fe814f0a9992f80426182dbb/inquiry-report.json` 为 answered:1、queued:0、delivered:0、unansweredDelivered:0。该目录 `native-turn-facts.json` 确认同一个原生 turn completed。`native.stderr.log` 有 skill 扫描和一次内部 collab spawn 错误，没有该问询投递失败；J 与 N 已给出成功的直接证据。故这条并非“已送达但模型未答”。

### 源码中的断点

调用路径是 `inquiry.observe → store._message_post → live channel.request / channel.observe → _apply_bridge_answer / _apply_journal → store._message_update`。桥独立提交 J，Codex `_NativeInquiry.poll` 在确认 `turn/steer` 的返回 turnId 后调用 `deliver_native`；根答复工具经 `_NativeInquiry.answer → InquiryBridge.answer_native` 写 J。服务侧的消息更新发生在 inquire/observe 的这次取样中。`waitMs` 等待遇到 delivered 就会退出，不保证等到 answered。

`messages` / `message_get` 只读服务消息；正常结果提交 `store.execution_result → _commit_execution_result → _transition_attempt(..., finished)` 没有先导入问询 journal 的步骤。`_transition_attempt` 只把服务上仍 queued 的消息改为 unavailable / `inquiry-queued-at-attempt-end`，且不处理 delivered。因此桥后续已答、服务尚未再次取样时，结果提交就会制造这里两种过期终态。`tests/python/blackboard/store/test_current_core.py::test_result_settles_only_its_queued_questions_and_replay_adds_no_events` 也明确钉住 delivered 留存的现状；这里引用的是测试源码，未声称本次重跑该测试。

这是源码与复现证实的机制；历史 8 条精确地“哪次请求后不再被服务导入”没有逐次 API 返回可追溯，不能声称重建了每一次观察调用。但源日志已有答案、服务消息缺答案的事实逐条成立。`attempts:2/3` 只能说明有这些投影更新，不能证明桥投递重试了这些次。

## 二、2 条是在交回后提问

`ac14a0b8` / `c-cursor-writer-1`：N3 第 1104 行在 2026-10-19T04:31:18.900Z 完成原生回合；get 的 turn 在 04:31:20.627Z 交回；问询 04:37:30.356Z 才创建，晚于交回 **369.729 秒**。`67845cec` / `session-b-q1`：N5 第 240 行在 02:23:08.681Z 完成原生回合；turn 在 02:23:09.861Z 交回；问询 02:24:54.549Z 才创建，晚于交回 **104.688 秒**。两条均 attempts:0、createdAt=updatedAt、`inquiry-no-live-agent`，没有 J，也没有对应原生输入。这与 `_message_post` 对非 running/cancelling 任务立即记 unavailable 相符。

这里是明确的回合已结束，不属于运行中静默或回合结束前后的竞争。底层进程是否已经退出不靠 PID 或缺文件判定；所需结论只是已经没有可绑定的活动回合。产品应在提问前显示回合已经结束，并保留此次拒绝的事实；若 Host 要让 Worker 处理新问题，应显式接续且成为新 turn，不能偷偷把本次 inquiry 变成新调用。

## 三、2 条仍在运行，但桥没有收到的留存证据

`db4f8b52` / `repro-q2`：2026-10-16T18:02:33.855Z 创建时，N9 的原生回合区间为 18:01:12.760Z–18:03:43.794Z，get 的 turn 到 18:03:46.189Z 才交回。J 只有同 attempt 的 `repro-q1` 三行，没有 q2；N9 也没有 q2 输入或调用。q1 已在 N9 第 25 行（18:01:30.442Z）进入回合，证明这个执行原先具有问询能力；q2 不能归为“该执行始终没有桥”。q2 创建正落在 N9 第 57–59 行的一次原生工具等待（18:02:12.110Z–18:02:42.124Z）里，之后还有工具与结束事件；没有证据证明 poll 没被调用。

`5e04475a` / `q-console-list-motion-1`：2026-10-16T17:31:25.870Z 创建时，N10 回合仍在 17:06:48.140Z–17:46:01.804Z 区间，get 的 turn 到 17:46:08.174Z 才交回。执行目录没有 J；N10 整个对应回合没有此 inquiryId 的输入或工具调用。其前有第 904 行 17:31:17.589Z 工具输出，其后有第 906 行 17:31:31.672Z 活动，排除“早已结束”。

两条导出均 attempts:0，createdAt=updatedAt。该 updatedAt 没有提供 attempt 结算时刻，不能拿当前源码的结束清扫规则倒推其历史更新过程。没有问询 bridge admission/error 或 steer 错误的原始返回，故准确原因只到“没有桥提交/原生输入证据”，不把无日志直接写成已证明的桥未激活或原生拒绝。

**推断：** 两条最符合当时 0.42.1 的通道登记表丢失。[0.42.1 安装记录](../acceptance/installs/0.42.1.md)说明当日 10:21Z 安装；[0.43.0 验收](../acceptance/live-channel-0.43.0.md)记录“第一次日常问题一直排队、两个诊断回合中一个中途断开”，并在私有无模型实验里证明旧 `BoardService` 构造会在每次 task_wait 时重置共享登记表，执行只登记一次。修复实际随 [0.44.0](../acceptance/installs/0.44.0.md) 安装，发生在这些提问之后。q1 可达而 q2 不入桥与此吻合；验收记录没有把那两次诊断逐条绑定到这里的完整 messageId，所以这 2 条的根因仍列为推断。本次没有重连、重启或访问日常服务来补证。

### Lead、静默帧与原生拒绝的排查边界

两条 Lead 自己的异常（`b3-q2`、`b3-q5`）分别在 c23b3dae 第 2、3 回合运行期间提出，而且都已答。`get-c23b3dae-4f00-4877-a1ae-61c89d74291e.json` 的 `audit.turns` 显示第 4、6、7、8 回合以 waiting 交回；从各次交回到下次回合创建的等待区间是 2026-10-18 的 09:34:51.296Z–10:32:01.049Z、10:54:57.611Z–10:55:01.486Z、11:00:22.573Z–11:00:24.705Z、11:00:57.838Z–11:05:53.769Z，均不与这两条提问相交。这里按服务回合记录判断接续间隔，不用它证明底层进程组停止。cda72b0d 是名下 Worker，并非当时在等待的 Lead。此 11 条没有一条可确认为“Lead 等待、没有原生回合”导致漏投。

Codex `protocol.Connection.pump` 在 finally 调用 after_pump；`_pump_one` 的队列等待上限 0.2 秒，超时返回仍走 finally；`native_run._wire.dispatch_feedback` 再调用 poll。因此“没有原生帧，所以 poll 不调用”与当前源码不符。本次假 peer 在首帧前完全静默，仍收到 steer。长工具也不等于 Python 控制器停止 pump；日志里无模型输出不能用来证明控制器阻塞。若未来要归因于 poll 停顿，需控制器 tick/队列时间与调用区间的证据。

`_NativeInquiry.poll` 会在 evidence.completed 非空时退出；桥未 active 时 pending_inquiries 为空；原生拒绝若与 completion 同时到达会停止投递，其余 RPC 拒绝会报协议错误。这些是真实代码分支，本组成功日志没有走失败分支，剩余 2 条缺少相应 RPC 证据。它们都是未来可验证的边界，不是本批已观察到的原因类别。

## 四、模型无关复现

脚本在本检出的忽略目录 `tmp/inquiry-diagnosis/reproduce.py`，结果 `tmp/inquiry-diagnosis/reproduction-result.jsonl`；原生时间线字段提取脚本为 `tmp/inquiry-diagnosis/inspect_timeline.py`，只输出必要字段到 `timelines.jsonl`。`verify_report.py` 在原处重读导出、原生记录与问询日志，校验消息表、绑定、时刻、原生行号和分类，输出必要字段到 `verification-result.txt`，退出 0。这些是本次交付的临时材料，不进入产品测试。复现使用锁定依赖（Python 3.12.10），只建全新的 tmp 内私有 BoardStore；以内部 command fixture 记录建立 attempt，不启动命令执行，不创建微任务，不启动服务，不运行真实 harness，也没有模型或日常服务调用。

从检出根运行：

```sh
env -u HMB_STATE_DIR -u HMB_RUNTIME_ROOT -u HMB_RUNTIME -u HMB_RUNTIME_IDENTITY \
  -u HMB_AGENT_CREDENTIAL_FILE -u HMB_AGENT_CREDENTIAL_FD \
  -u VIRTUAL_ENV -u UV_PROJECT_ENVIRONMENT \
  UV_PROJECT_ENVIRONMENT="$PWD/tmp/inquiry-diagnosis/venv" \
  UV_CACHE_DIR="$PWD/tmp/inquiry-diagnosis/uv-cache" \
  uv run --frozen python tmp/inquiry-diagnosis/reproduce.py \
  > tmp/inquiry-diagnosis/reproduction-result.jsonl
```

假 peer 是同一脚本 `--fake-peer` 分支的普通 Python 子进程，只通过 JSONL 回应 turn/steer 和发起 root answer 请求。运输用真实 `Connection`、投递用真实 `_NativeInquiry.poll/answer`、持久化用真实 InquiryBridge；peer 在收到 steer 之前不输出任何帧。运行先等真实 answer_native 落盘，再令本地 evidence 完成、桥关闭，调用真实 store.execution_result。结果如下，两种情形全部断言通过，命令退出 0：

为固定观察与回答的先后次序，脚本直接调用 `store._message_post` 和 `bridge._ask` 建立已排队的问题；delivered 分支用 `store._message_update(state="delivered")` 固定服务最后一次取样，queued 分支不做这一步。它们模拟的是两种服务快照，没有经完整 inquire 或 C-Two 构造这次取样；随后投递、答复落盘、结果提交和终态 observe 都调用当前实现。

| 服务最后一次采到的状态 | 问询日志 | result 提交后服务状态 | 随后显式 terminal observe |
| --- | --- | --- | --- |
| queued | queued → delivered → answered | unavailable，inquiry-queued-at-attempt-end，answer:null | answered，reason:null |
| delivered | queued → delivered → answered | delivered，reason:null，answer:null | answered，reason:null |

这复现了本批最大的 7 条类别以及额外 delivered 的机制，并证明现有终态 observe 路径能在显式调用时导入该私有 attempt 的答案。脚本没有完整复演 Worker 结果封存、执行进程、C-Two 或自动恢复；不能据此宣称产品已修好。复现只验证现状，不实施建议中的自动导入。首次 idle pump 也成功投递，因此复现不依赖任何模型产生帧。

## 五、ZCode 的输入能力与本批数据

本次未调用真实 ZCode、未运行发现命令、未联网查新版能力。按仓库的无模型发现/Worker 核对记录，版本为 **ZCode CLI 0.16.9**（[ADR-030 批次 3c 验收](../acceptance/adr030/batch3c-host-review.md)，2026-10-12；[workers](../reference/workers.md)亦记录 0.16.9），现有代码与文档没有可由客户端绑定到当前活动 turn 的输入能力。它不能据此表述为“2026-10-22 所有新版 ZCode 都没有”；若安装版本变化，应重新核对协议，不猜测。

[ZCode checkpoint 验收](../acceptance/zcode-checkpoint-inquiry.md)明确记录：session/send 没有 delivery / expectedTurn 字段，活动 prompt 中拒绝再 send；v4 sendText 只能延后未绑定输入或开始新 turn，内部 expected-turn-fenced steer 不对客户端公开。该文早期测试段另写过 “3.14.3 CLI”；[requestUserInput 探测记录](../acceptance/zcode-request-input-probe.md)将 3.14.3 明确为 App 版本、0.16.9 为 CLI bundle 版本，本记录按两种形态区分。当前 `buddy/harnesses/zcode/native_run.py::_admit_root_input` 只有一次 session/send，参数为 sessionId、inputId、content；之后的工具与事件消费没有一个实时 steer 投递路径。`InquiryBridge` 按 cooperative-checkpoint 排入，根 hey_my_buddy_checkpoint 或 completed finish 的拒绝路径才把问题送到根工具结果里。只看静态代码能确认“本项目未实现这种输入”，协议不支持的结论以这些已记录的版本核对为依据；本次没有读取或重新验证第三方源码。

协作式投递也不能直接解释那 14 条 unavailable。本次附带在相同原处 J 交叉核对：**6 条其实已有 delivered 和 answered，7 条一直 queued 直到强制停止时 unavailable，1 条没有该 inquiry 的日志证据**。这进一步表明消息面板不能直接当作原生未送达率。

| ZCode 消息前缀 / inquiryId | 原处 J 与事实 |
| --- | --- |
| 35666961 / release-boundary | task ac8f33ef-b89b-4125-9642-a82c8a3a5376，attempt 5573994e-138c-4711-b890-440605a33b56；J 1–3 行，07:08:43.856563Z queued、07:13:09.041137Z delivered、07:15:27.398944Z answered（10-22） |
| 0c73215a / owner-design-close-boundary-1 | task 37e04eb9-c490-4442-906c-07fda0a7df22，attempt 45162f54-4058-4074-92d3-0f771d9b84d9；J 1 / 2 / 3 行为 queued、delivered、answered，10-21 02:46:48.321326Z 已答 |
| 7de853ae / keep-baseline-evidence-small；a0afb34a / baseline-before-migration | task 04166768-68d9-492f-b1f5-ff5b703fef85，attempt 9bbbc222-8106-4894-a96c-c5483ac65e11；J 2 / 4 / 6 行和 1 / 3 / 5 行分别为 queued、delivered、answered，在 10-20 16:37:18.962003Z、16:37:07.192097Z 已答 |
| 4f8216c3 / batch3-lead-turn4-layout-compact-confirm | task da2528ac-0a8f-4e11-a4bc-8569bdc17411，attempt d33d512a-b6f9-469f-8d06-b794383332f2；J 1 / 2 / 3 行为 queued、delivered、answered，10-18 10:31:03.872206Z 已答 |
| 9b5f3a38 / batch3-turn2-timeline-scroll-buddy-colors | task 7f3c672c-62f1-4bf9-993f-8ab63e16874c，attempt 153a63ce-53d5-489d-b632-cb605a88fb46；J 1 / 2 / 3 行为 queued、delivered、answered，10-18 08:58:54.276500Z 已答 |
| 421ef1c7、a6c8cdd5、780eca02、0729f3a4、f2df5525、b17c7cd3、970d88af | task 085d3669-0387-46c9-9676-d3a2162090f4，attempt 7deb6fe6-970b-4452-936c-7335974ad767；J 各自 queued 后于 10-20 17:19:57.754662Z–17:19:57.763290Z unavailable，没有 delivered；get.cancellation 明确记 Host 因这 7 条仍排队而接管，terminationReason:forced-stop |
| 76474859 / release-reuse-regressions | task 203b6c4a-9950-43f2-81db-7c689df33274，attempt 5b948785-c264-4a8e-9cd9-3d1d9b87401c；没有此 inquiry 的 J 记录，不归因于未调用 checkpoint |

前述 7 条最早 queued 于 16:43:39.253387Z，到它的 unavailableAt 17:19:57.754662Z 相隔 36 分 18.501 秒，仍未 delivered。直接观察只证明问题未被根检查点消费，不证明模型故意忽略；强制结束也没有经过正常 completed finish 的拒绝机会。此处没有把协作式改称实时，也没有为补投而 stop/restart 或新开回合。

## 六、修法选项与建议（均未实施）

### 已答但服务投影滞后

选项 A：让 Host 持续 inquire/observe，或让 messages 的读取隐含刷新。前者要求调用方轮询才正确，后者让本应只读的消息读取承担写入；两者还会漏掉断线、崩溃与最终一次观察后的答案，不建议作为正确性的依赖。

建议选项 B：把问询的最终事实作为执行结果的一部分，在同一个结果提交边界先导入、再结清，结果重放同样幂等。执行侧在关闭 admission、处理完已经接收的根工具证据并关桥后，冻结问询 journal 或规范化后的有界文件；跨进程只传私有文件位置、摘要和完整执行/turn 绑定。服务使用共享的严格读取与合并规则验证 taskId、attemptId、generation、turnId、版本及 questionSha256，事务里应用答案和送达事实，再把确实未答的问题终态化，与完成事件一起提交。不能把密封过的原记录重写，也不能依赖 selectedAttempt 指向旧 attempt；历史消息必须按自身 attemptId 绑定。服务重启、结果重放、通道分页和恢复路径都使用同一个入口。

运行中仍可按现有 live channel 同步增量，调度器可以主动消费该通道的 inquiry 更新，使 Host 更快看到答案；这只是改善时效，最终正确性由结束导入保证。活动执行不另开文件旁路，不在服务数据库事务内做网络等待；解析与验证在边界一次完成后再提交。现有服务 `blackboard/tasks/inquiry.py::read_journal` 只保留每个 inquiryId 的最后一行，桥读取还做结构合并，绑定规则也不同（[backlog](backlog.md) 已记下）；应收敛到 protocol 的一套读取规则，保留最后 answered 之前的 delivery 与身份字段，不能仅调整一次清扫 SQL。

预期收益是 Host 不再靠第二次提问偶然带回上次答案；回归风险集中在事件/事务原子性、幂等、分页、结果恢复、旧 attempt 绑定及 discarded 竞态。执行结果携带文件证据会改变 execution/service 事实契约，应按项目规则升中间版本与 CONTRACT_VERSION；若选方案需要新增存储形式，必须同批完成显式升级、剪枝和 fixture 库存。这里没有批准改变任何既有 Host API 或 ABI。

### 回合已结束、Lead 等待以及通道不可用

已经结束的 2 条应在 Host/控制台可见“此回合已结束，问询未排入”；发送时仍在服务边界复核，避免 UI 读到旧 running。Lead 已 waiting 而没有运行回合时，显示“Lead 等待名下任务，当前没有可接收问询的回合”；若要支持稍后带入，必须设计一个显式的待接续消息语义与绑定，由 Host 决定接续，不能把 inquiry 偷换成唤醒。

通道未绑定、原生回合尚未激活、原生明确拒绝与回合结束分别保留自己的观测事实，不统一说成“Worker 忙”。登记表生命周期沿用已修复的 daemon 唯一实例，不再建设第二个登记机制；服务重启只能按原有完整身份重新附着。短暂 channel-unbound 若保留 queued，应由服务在同一已授权 attempt 的通道恢复后负责重送相同 inquiryId，并给出可观察的未提交状态；若选择立即拒绝，Host 应收到明确可重试原因。建议没有已验证通道时明确返回未投递与原因，由 Host 在观察到同一回合恢复后显式重试；重试的幂等与状态转换需要随契约明确定义。两种产品语义应择一明确，不能让一次调用悄悄成为永远无人消费的队列。此组历史 2 条根因仍需原始 inquire.bridge 返回与 registry 生命周期日志才能升为证实，不能声称当前源码仍有已修复的 0.42.1 缺陷。

对未来可能出现的 steer/完成竞争，保留 expectedTurnId 和 acknowledgement 的匹配；明确拒绝就记录拒绝码，已完成就关闭本 turn 的投递窗口，不改成 turn/start。若确有控制器 pump 阻塞，再修阻塞的调用处；本次不建议为一个未证实的静默帧原因增添第二条发送线程。

### 已送达但在回合结束前未答

先导入实际答案，再决定是否未答，避免把本批 bc620007 再次误判。桥的 `close()` 已把 queued/delivered 的未答条目标 unavailable；Codex 角色的 `buddy/roles/schema_worker.py::NativeSchemaWorker.delivery` 也已根据 unansweredDeliveredInquiries 把完成结果改为 attention。缺口是把这些事实投影到每条服务消息，并覆盖异常结束、取消与恢复路径，不是另造一套角色结果结算机制。

建议消息结算保留 deliveredAt / nativeCorrelation，结为“已送达，本回合结束前未答”，用独立原因区分“从未送达”；界面不再显示无限期“等待答复”。queued 则按有无桥提交、协作式未被取走、明确拒绝等已有事实结清；未知仍为未知。即使导入失败也要展示证据不可用，不能把未知改成未答或已停止。有效的已落盘答案、Host 的显式终态决定与真正晚到的答复分别按现有边界处理，不靠统一放开终态来掩盖顺序问题。

### 协作式能力如何说明

Host 工具结果和控制台在提问前、排队后都应显示实际 deliveryMode。ZCode CLI 0.16.9 的文案可为“在 Worker 下次检查点或完成工具反馈时交付；当前尚未送达”。显示 queuedAt、最近 checkpoint 与最后原生活动即可；没有检查点时间就写未知。只有验证过的根工具 receipt 才称“已送达”，它可以来自 checkpoint 或 finish refusal；answer receipt 才称“已答”。不要把 waitMs 当成送达承诺或模型期限，不用“正在通知”暗示实时插入。

可选方案是要求 Worker 在自然阶段及回合结束前调用检查点，并让正常 completed finish 继续拒绝未结的问题；这是现有机制，应核对提示词和界面能否如实呈现。若 Host 必须在长任务中及时改变方向，应根据声明能力选 realtime harness，或显式停止/接续；不能通过隐藏的 ZCode 再 send、新回合、设置覆盖或定时模型调用来模拟实时。强制取消、崩溃与权限阻断没有正常 finish 保证，仍须服务最终结清问询。

## 验证边界

本次验证的是原始文件对应关系、Codex CLI 0.162.1 会话中的输入/调用/完成时间、源码路径，以及两种真实 bridge/store 的模型无关复现；逐条字段核对与复现命令均退出 0。没有改动产品代码或测试，没有跑完整检查、真实 harness、模型、日常服务、安装或用户验收。没有变更分支、标签或推送。交付的非忽略文件只有本文件；tmp 脚本及模型无关私有运行材料留在本检出的忽略目录，Host 需要一并取走才能再次执行复现。
