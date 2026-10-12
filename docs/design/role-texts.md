# 角色说明

2026-10-11，Claude Code Host 应用户的要求起草，同日按用户的三点意见改过一次；用户看过，认为可以用，以后在自举开发里看效果再改。[ADR-030](../decisions/030-harness-layer-scope.md) 第 8 条把它定为产品的一部分，批次 1 起三段文字随运行时发布：正文只有一份，在 `src/hey_my_buddy/buddy/roles/texts/` 下的 `worker.txt`、`router.txt` 与 `lead.txt`，由黑板机械地放在提示词的最前面。这份文档不再抄录正文，只留下为什么要有、写法上的约定与核对到哪里。

## 为什么要有

Worker、Router 与将来的 Lead 所用的 agent，在这里对接的是 Host 或黑板，不是用户。用户为“和自己交互”写的用户级指令、装的交互式工具，到了这里都成了阻碍：同一天的实验里，不加任何说明时，一个 Claude Code 的 Worker 在本项目里读到用户的全局指令，自称“整合 Host”，并说要“等你定”。角色说明由黑板机械地放在提示词的最前面，四个 harness 用同一种做法；它只约束行为，权限仍由黑板机械地判定。依据见[调研记录](../research/2026-10-11-role-text-against-user-instructions.md)。

## 写法上的约定

- 每个角色一段，一次只注入这个角色的；Lead 是 Worker 的一段再加它自己的一段。
- 每段写五件事：你对接的是谁；哪些指令在这里不适用；不许做的交互；**这个角色能对黑板做什么**；卡住了怎么办、做完了怎么交。任务本身、写入范围、结果的格式不在这里，它们由黑板在别处给出。
- 每个角色只看到自己能做的操作。Host 的 skill 不必讲别的角色怎样做，别的角色也看不到 Host 的操作。现有提示词里关于“什么时候结束这一轮、怎样向 Host 求助、不许自己另起任务”的三段说明（`buddy/roles/turn_io.py`）并进 Worker 这一段，不留两套说法。
- 用英文写，因为它对所有用户都一样。
- 要短：它跟着每一次调用走。

## Worker

正文在 `worker.txt`。与起草时相比，批次 1 把原有提示词里“什么时候结束这一轮、怎样向 Host 求助”的说明并了进来，多了两条。

## Router

Router 有三个面（[ADR-021](../decisions/021-router-buddy-planes-and-routing-evidence.md) 第 3 条），每次调用是其中之一；工作面又分快速与审阅两种模式，审阅可以只读查看冻结的副本。

正文在 `router.txt`。它写了三个面，其中维护面与画像面还没有调用方，接线的只有工作面的两种模式。

## Lead

注入时放在 Worker 那一段之后。

正文在 `lead.txt`。随包发布，还没有接线（[ADR-026](../decisions/026-delegation-authority.md) 没有实现）。

## 核对到哪里

- Worker 这一段测了两版，都在本项目的目录里、用户自己的配置原样使用、说明放在提示词最前面。第一版在 Claude Code 2.1.284（Sonnet）与 Codex 0.162.1 上各一次：两家都答自己是 Worker、向 Host 汇报、遇到没有指定的选择不自己定也不问用户。不加说明的对照里，Claude Code 自称整合 Host 并说要等用户定；Codex 说向“用户或上级 Host”汇报。
- 加了黑板操作之后的第二版：Claude Code 答会用 `buddy_finish_turn` 以 `assistance` 或 `attention` 结束这一轮，并指出这个会话里没有这几个工具、所以最终消息就是渠道，与原意相符。Codex 用的是最便宜的模型，它把渠道说成了“向 Host 发起 inquiry”，不准确；为此加了一句“回合中途不能向 Host 提问”，这一句加了之后的确认运行在写这份草稿时还没有结果。
- 第二版又在 ZCode 0.16.9（GLM-5.3-Flash）与 DSH（deepseek-flash）上各测了一次，经各自的运行模块、无工具、私有状态。不加说明时，ZCode 自称“交互式编码代理”、向用户汇报、打算自己替用户选一个；DSH 自称子代理、向“启动它的控制器”汇报。加了说明之后两家都自称 Worker、只向 Host 汇报、以 `assistance` 结束这一轮把选择交回 Host，并说得出用哪个工具。
- 每种情况一个样本，问的是一道不动文件的小题。真实的微任务里它管不管用，要等接进黑板之后在真实任务上看。
- Router 与 Lead 两段没有测：Router 的三种调用各有自己的提示词与格式，维护面与 Lead（ADR-026）还没有实现。
- 接进黑板之后的真实调用记在各批的验收记录里：[批次 1](../acceptance/adr030-batch1-host-review.md)（五次）、[批次 3a](../acceptance/adr030-batch3a-host-review.md)（Claude Code 三次）、[批次 3b](../acceptance/adr030-batch3b-host-review.md)（Codex 四次）。
