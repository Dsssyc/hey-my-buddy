# Codex 与 Claude Code 能不能在回合进行中收下 Host 的问题、并把答复对上这个回合

## 问题

DSH 与 ZCode 的问询走合作式的检查点：Host 的问题排在队里，等 Worker 自己调用 `hey_my_buddy_checkpoint` 才送到，答复经 `hey_my_buddy_answer_inquiry` 回来。Codex 与 Claude Code 还没有问询（[执行计划](../design/adr031-execution-plan.md)“落在这块底座上的待办”里的 31–32，要求先证原生的路径）。要弄清两件事：原生一侧有没有把一条输入送进正在跑的那个回合的办法；答复能不能可靠地对上这个回合与这个问题。

## 看了什么

- Codex CLI 0.162.1 的 app-server：用它自带的 `codex app-server generate-json-schema` 生成协议的 JSON Schema，分别不带与带 `--experimental`；读了 `turn/steer`、`thread/start`、客户端自带工具（dynamic tools）与服务端发给客户端的 `item/tool/call` 几处的定义。没有读源码。
- Claude Code CLI 2.1.299：读了 `claude --help` 里 `--input-format stream-json`、`--mcp-config`、`--allowedTools`、`--no-session-persistence` 几项。没有读源码。
- 自己的实验，各一个回合，开发者 2026-10-16 逐次批准：都用开发者自己的 harness 配置与登录，不改任何设置；脚本在仓库被忽略的 `tmp/inquiry/` 里。

## 看到的事实

- **Codex CLI 0.162.1，协议定义。** `turn/steer` 不是实验接口，参数是线程号、`expectedTurnId` 与输入；`expectedTurnId` 是前提，与当前在跑的回合不符就失败，回复里给出回合号。`thread/start` 的 `dynamicTools` 是实验字段，要在 `initialize` 里声明 `experimentalApi`；模型调用这样的工具时，app-server 向客户端发 `item/tool/call` 请求，带线程号、回合号、调用号与参数，客户端回 `{contentItems, success}`。`thread/start` 另有自由形式的 `config`，可以按次覆盖配置（本次没有试用它挂 MCP 服务）。
- **Codex CLI 0.162.1，实测**（`gpt-6-luna`，推理强度 low，临时线程）：让回合去跑 `sleep 12` 再答 DONE；回合开始 3 秒后用 `turn/steer` 送进问题，被接受，回复里的回合号就是在跑的那个。模型随即调用 `hey_my_buddy_answer_inquiry`，参数 `{id: "q1", answer: "42"}`，请求里的回合号与在跑的回合一致；之后接着跑完 `sleep 12`，答 DONE，回合正常结束，共 23 秒。这次问题送到时命令还没开始；命令进行中送到的问题，按协议要等这一步结束、模型下一次决策时才看得到，没有实测。
- **Claude Code CLI 2.1.299，实测**（`haiku`，不留会话，本次另挂一个只记录调用的 MCP 服务）：用流式输入送第一条消息，让它跑 `sleep 12` 再答 DONE；它刚发出 Bash 调用时送进第二条消息（问题）。命令结束之后，同一次运行里模型先用 `ToolSearch` 找到被延后加载的 MCP 工具，再调用 `answer_inquiry`，参数 `{id: "q1", answer: "42"}`，然后答 DONE；整个过程只有一个结果事件（4 步），共 26 秒。也就是说，回合进行中送进的消息在下一步决策时被同一个回合处理，没有另起一个回合。
- **我们的推断。** 两边都能把问题送进正在跑的回合：Codex 由 `turn/steer` 按回合号送到，Claude Code 由流式输入在下一步送到；答复都能经我们挂上的工具结构化地回来。Codex 的工具调用自带回合号，对不对得上回合由协议保证；Claude Code 靠的是 MCP 工具挂在这一次运行上，与 ZCode、Claude Code 现在的完成工具同一种做法。

## 取了什么，没取什么

- 取：两边都按“送进正在跑的回合 + 经我们的工具答复”来做问询，不再只等合作式的检查点；这比 DSH 与 ZCode 现在的送达更快，能力上要如实声明为不同的送达方式（ADR-023：差异记成声明的能力）。
- 待定：Codex 的答复工具用实验性的 dynamic tools（回合号由协议给出，不起额外进程，但 Codex 升级可能改它），还是用 `thread/start` 的 `config` 挂现成的会话 MCP 服务（不依赖实验接口，与另外几个 harness 一致，本次没有实测）。由实现时的方案定，交 Host 验收。
- 没取：替 Claude Code 预先加载工具或改它的工具搜索设置：那是用户的配置，模型自己会去找（ADR-030）。

## 时效

Codex CLI 升级后 `turn/steer` 或 dynamic tools 的形状变了，或 dynamic tools 不再是实验接口；Claude Code CLI 升级后流式输入的消息改为排到回合结束之后处理，或 MCP 工具的加载方式变了。实验各只跑了一次，模型换成别的之后是否同样调用答复工具没有验证。
