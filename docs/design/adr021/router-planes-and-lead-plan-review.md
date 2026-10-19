# 审查：Router 的几个面与 Lead 的执行计划

对象是 `docs/design/adr021/router-planes-and-lead-plan.md`，计划写在 291dddbd 上。291dddbd 到 d8beb762 之间没有改过源码（`git diff --stat` 里只有文档和一个测试），所以计划里的行号仍然可以对照。本文按 d8beb762 写。每条结论都标了出处：“读源码”是我在这个提交上亲自读到的；“读文档”来自 ADR、`AGENTS.md` 或待办；“推断”是我自己的判断。审查期间没有运行任何测试或服务。

## 1. 先做 Lead 会不会让后面的事返工

结论：会让后面返工的有四处，其中一处涉及存储；另外，短路径本身还有两个缺口。

**最要紧的是名下微任务的归属形式。14 定下的形式，15、16、10 和 33 以后都要用。** 计划写的是“14 复用 workflow_children”，可这张表现在是按协助任务设计的：`decision_command_id` 不能为空，存的是批准命令；`state` 是协助任务自己的一套取值；表里还有 `auto_continue`（`src/hey_my_buddy/blackboard/store/db.py:590-605`，读源码）。名下的微任务没有批准命令，它的状态以 `workflow_runs` 为准。如果把它直接塞进这张表，同一列的意思就要看这一行是哪种记录，这违反了 `AGENTS.md` 的规定：两种形式并存时，行里要写明自己是哪一种（推断）。15 和 16 都从这张表读数据，以后再改就得多做一次升级。所以 14 现在要守住四点。第一，每行写明种类，批准命令那一列只有协助任务才填；也可以干脆给名下微任务另建一张表。第二，归属记在 Lead 所执行的那个微任务（run）上，不记在 attempt 或 buddy 上，这样换手时归属不变（计划已经写了这一点）。第三，名下微任务要带上 Lead 的 `macro_task_id`。第四，控制台的“等 Host”计数是按根来数的（`src/hey_my_buddy/blackboard/tasks/macro_tasks.py:88-106`），在等 Lead 的微任务不能算进去（读源码，加推断）。至于 Lead 那一行的三种状态，都能从已有的事实推出来：已交付，就是在等 Host 验收；正在运行，并且活动里有工具调用没跑完（`src/hey_my_buddy/protocol/activity.py:20`），就是在等名下的微任务；其余情况都算在工作。所以 16 不需要新的存储（推断）。

**Router 的预算（影响从 07 传到 06）。** Router 调用的硬预算和 Worker 的硬时限现在用的是同一个字段。`src/hey_my_buddy/blackboard/routing/decision.py:686` 把预算加上宽限写进 Router 任务的 `timeoutSeconds`；时间一到，`src/hey_my_buddy/buddy/runtime/worker.py:781-795` 把它停掉。Router 的切换又是认终止原因 `deadline` 来判断的（`src/hey_my_buddy/blackboard/routing/decision.py:1637`、`src/hey_my_buddy/buddy/roles/router.py:141`，读源码）。计划让 07 依赖 06，原因大概就在这里。短路径把 06 挪到了后面，07 就得自己守住三点：预期时长只给微任务和命令用；Router 的预算换成只有 Router 调用才有的字段，到点照旧硬停；`deadline` 只表示 Router 的预算用完了。守不住的话，要么 06 返工，要么切换失灵（推断）。

**急停和级联取消（影响从 14 传到 10）。** 14 取消 Lead 时要连带取消它名下的微任务，这只能走现有的 `_cancel_children`（`src/hey_my_buddy/blackboard/tasks/workflow.py:3377`），不要另写一条专给 Lead 的级联。这样 10 收窄急停的范围时只需要改这一处（推断）。11 也不用等 10：控制台的停止今天就有（`src/hey_my_buddy/blackboard/tasks/workflow.py:3247`），11 现在就要把它和 Host 的 `cancel` 分开。控制台停止立即生效，Host 停止先收尾。终止原因要一次定全：Host 停止的两种结果、控制台停止、发起者未知。发起者单独记一列，这样 14 以后只需多填一个来源（推断）。

**提交字段（影响从 13 传到 15）。** Lead 提交微任务时，用和 Host 同一套字段校验（`src/hey_my_buddy/protocol/schemas.py:104`），能动哪些微任务由归属来限，不另开一份 Lead 专用的字段清单。这样 15 加上“修正的是哪一个”时，只需要加在一个地方（推断）。

**不会返工的几项。** 02–05：四个 harness 都已经有 `observer-interrupt` 这种结束方式，角色层也已经有原生续接（`src/hey_my_buddy/buddy/roles/run_execution.py:325`、`:578`）。harness 只需声明能不能续接，比如 Claude Code 就没有 `native-session`（`src/hey_my_buddy/buddy/harnesses/claude/adapter.py:34`）。只要 11 不把收尾写进各家的 native_run，它和 02–05 只是改到同一批文件，不是返工（读源码，加推断）。08：提醒挂在被等待的那个微任务上，Lead 用的是同一个 `await`。前提是 07 把预期时长记在黑板的回合记录里，而不是只交给 Worker（推断）。09：需要的事实已经有了，Lead 停在 `await` 时有一个工具调用没跑完，不会被误报为卡住（读源码）。

**这六项本身的先后。** 第一，漏了一个前置（读文档）。按 `AGENTS.md`，归档升级已经消费掉，下一次改动要先挪升级基点、重新生成样板看板、删掉归档升级的代码，在那之前不能改任何存储形式。这段代码还在（`src/hey_my_buddy/blackboard/store/migrations.py:1`、`src/hey_my_buddy/install/upgrade.py:306-433`）。01 会改存储，所以要在 01 前面加一个微任务。第二，01 不是 Lead 的前置，四个 harness 都已经声明了“放开”这一档（`src/hey_my_buddy/buddy/harnesses/registry.py:139-145`，读源码）。开发者想先做 01 可以照做，但它不应该挡住 12。第三，12 不依赖 11。真正的依赖是 12→13→14，以及 14→11。计划里的 07→06、11→08–10、12→11 都只是因为改到同一批文件，不是真正的依赖（推断）。第四，Lead 名单的设置入口和授予前的说明都放在了 16 里，而设置只能经控制台写（`src/hey_my_buddy/blackboard/evaluation/evaluation.py:27`）。所以按短路径装上之后，用户没法启用 Lead（读源码）。

## 2. 层层设防与多余的东西

多余或防得过度的有四处。

1. 12 的“两层模型不同”如果做成黑板的检查，没有可靠的输入：黑板只知道 Host 的 `host_id` 字符串（`src/hey_my_buddy/blackboard/store/db.py:502`），不知道 Host 用的是什么模型。ADR-026 第 3 条用的词也只是“应当”。更小的做法是让用户排名单时自己顾及，在说明里写一句即可（读源码）。
2. 13 的“禁共享设置、安装、再授予”如果单独写成一组拒绝，就成了第二套机制。凭据本来就是白名单（`src/hey_my_buddy/blackboard/tasks/workflow.py:45`）。更小的做法是只把对名下微任务的 Host 操作加进白名单（读源码）。
3. 15 打算新建一个模块和一张表。可要存的只是“修正的是哪一个”这一个引用，提交时写定，之后不再改。在 `workflow_runs` 上加一列可空的外键就够了（推断）。
4. 原生检查打算“在空主目录或授权配置副本上跑”。按 ADR-030 和 `AGENTS.md`，对真实 harness 的检查就是 `tests/probes/` 下的探针，用用户自己的配置、有意去跑；拷一份配置是第二种做法，而且空的主目录也隔不开登录（读文档）。

说得有理、应该留着的：01 用一张表判定工具证据；07 拒绝旧字段，不留别名；11 存下宽限的截止时间，并且停止状态未知时不续接；14 让归属跟着微任务走；19 不存计数；21 和 23 最多打回两轮，逐项写入；22 做不到强制就不声明；26 只自动改排一次；31 和 32 先证明原生路径走得通。18 的对应表、带依据的建议和可撤回，是待办“路由知识按归属分层”里开发者定的方向，不是计划自己加的。25 里“登录/提供方改名先保留”这句说不清谁会用它，需要讲明它和 ADR-030 第 4 条的边界（推断）。

## 3. 现状表抽查

抽查了 15 处，结论没有错的。另有 1 处依据引错、3 处行号偏了一到四行、1 处归类重复。

- 结论正确的：
  - L1：`src/hey_my_buddy/protocol/schemas.py:108`。
  - L6 部分：Codex 用的是 `hey-my-buddy-router`（`src/hey_my_buddy/buddy/harnesses/codex/native_run.py:712`）；DSH 和 ZCode 的 `read_only_structured` 为 False，在 `src/hey_my_buddy/buddy/harnesses/dsh/adapter.py:27` 和 `src/hey_my_buddy/buddy/harnesses/zcode/adapter.py:20`，计划写成了 26、19。
  - L10：`src/hey_my_buddy/blackboard/store/db.py:958` 只预留了 plane。
  - L12：`src/hey_my_buddy/buddy/harnesses/run_contract.py:67` 只有四档，计划写成了 71。
  - L13：`src/hey_my_buddy/blackboard/routing/router.py:66`。
  - L15：`src/hey_my_buddy/install/upgrade.py:377`。
  - L17：`src/hey_my_buddy/blackboard/tasks/workflow.py:3279-3285` 只跳过已验收和已结束的微任务，已交付的照样会被取消。
  - ADR-022 第 1 条：默认值在 `src/hey_my_buddy/protocol/schemas.py:160`，0 表示不限（`:29`），到点硬停在 `src/hey_my_buddy/buddy/runtime/worker.py:781`。
  - ADR-022 第 3 条：事实已经齐了（`src/hey_my_buddy/protocol/activity.py:20`），判定还没有做。
  - ADR-022 第 7 条：`src/hey_my_buddy/blackboard/store/db.py:54`。
  - ADR-026 第 1 条：`src/hey_my_buddy/buddy/roles/prompts.py:13-20` 只有 Worker 和 Router 两段。
  - ADR-026 第 9 条：`src/hey_my_buddy/blackboard/store/store.py:568`。
  - 只读一档：`src/hey_my_buddy/buddy/harnesses/registry.py:57`、`src/hey_my_buddy/buddy/harnesses/base.py:154`。
  - 写检出一档：`src/hey_my_buddy/buddy/harnesses/registry.py:139-145`。
- 依据引错的：ADR-026 第 3 条引了 `src/hey_my_buddy/blackboard/store/db.py:309`，那是 `evaluation_state`，该对照的是 `router_buddies`（`:924`）。顺带发现 `evaluation_state.decision_buddy_id` 在源码里没有任何地方写入，`src/hey_my_buddy/blackboard/service/service.py:879` 读它，读到的永远是空，可以随 06 删掉（读源码）。
- 归类重复的：ADR-022 第 5 条标为“部分”，但 Router 的预算这部分已经齐了；它缺的那一项（删掉 Worker 的硬时限）属于第 1 条。

## 4. 漏掉的

1. 挪升级基点并删掉归档升级（见第 1 节，读文档）。
2. ADR-022 第 2、3 条在控制台的显示，以及“可能卡住”阈值的设置入口。没有哪个微任务排了它们，计划也没写明留给第 4 步（读文档）。
3. 数据流向说明的更新：ADR-021 第 4、16 条，ADR-023“写检出不保护读取”。42 只做了入口（读文档）。
4. 维护面的数据包超大时的处理。ADR-023 规定这时改用只读一档，把数据包放成目录。一批 50 个任务，每个任务的原文最多 1 MiB（`src/hey_my_buddy/protocol/schemas.py:21`），会超过 `src/hey_my_buddy/buddy/harnesses/run_contract.py:93` 的 2 MiB 上限。21 没有排这件事（读源码，加推断）。
5. 17 要读 15 写的修正关联，应该写明 15 排在 17 之前（推断）。

## 5. 要开发者定的事

计划列出的四件：

1. 当前的 Router 没有强制的只联网一档时怎么办：确实该问。不过 ADR-021 第 2 条已经规定，调用前不满足资格，就由下一个 Router 处理同一个请求。照这一条，画像可以交给下一个有资格的 Router 去做，工作面不用换手，画像也不用停下。建议请开发者确认这样做不违反“三个面一起换”（推断）。
2. 运行时的版本从哪里取：该问，待办里写明了要在这一批开头定。按 ADR-023“复用已经在跑的路径”，建议四个 harness 都取检测时已经记下的版本。
3. 备注冲突：这个问题因为有了对应表才出现。“交用户处理”会给用户添操作，“取最新”会丢掉一条。还有一种做法：两条都留着，各自注明来源，两条都交给 Router（推断）。
4. 退役的 buddy：待办写的是到第 4 步再定，现在不用问。

该问而计划没有问的：

5. 要不要把 Lead 名单的设置入口从 16 里拆出来，放进短路径？不拆，就得等 16 做完再装。
6. ADR-026 第 5 条要让用户知情，靠的是第 10 条的泳道。还没有泳道就先装上 Lead，开发者能不能接受？
7. Host 停止一个 Lead 时，它名下的微任务是走收尾还是立即停？建议走同一条收尾路径，不另立规则。
8. “两层模型不同”只是给用户的提示，还是要黑板检查？
