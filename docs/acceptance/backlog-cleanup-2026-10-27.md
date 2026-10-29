# 待办清理（2026-10-27）

只读盘点微任务“待办与 ADR 只读盘点”（run `d45f6052`，经路由）逐条核对了 `docs/design/backlog.md` 的 114 条与 33 份 ADR；报告留在 Host 的 `tmp/storage/doc-status-d45f6052/`，没有进仓库。按待办开头的规矩，已全部做完的 39 条从待办删掉，依据记在这里。

- **核对到哪一步：** 引用验收记录的 31 条，记录位置来自盘点，盘点逐条核对过记录里的说法，Host 没有逐条重读；没有专门验收记录的 8 条，Host 逐条在源码里核对过，依据写在表里。
- **留在待办里的：** 部分做完的 27 条、没开始的 40 条、搁置的 8 条。部分做完的条目还没有逐条改写成只剩余下的部分，接手时再改。
- **顺带改正：** “样板看板只覆盖一部分存储形式”一条改为现在的计数（55 张表里 5 张空表）。
- **同日的另一次提交：** 按同一份盘点改正了 21 份 ADR 的状态与 ADR-021 执行计划的现状表。

## 删掉的条目

| 节 | 条目 | 依据 |
| --- | --- | --- |
| 控制台 | 微任务详情的步骤条放大之后，方向键换到视野外的步骤，步骤条不跟着滚 | [console-strip-follow](console-strip-follow.md) |
| 控制台 | 并发上限报错时，“占用”从输入框右侧挪到下方 | [acceptance](schema-29/acceptance.md) |
| 控制台 | 时间轴“适配”的两项浏览器检查随当前时间失败 | [console-strip-follow](console-strip-follow.md) |
| 控制台 | 控制台可读性一批 | [console-frontend](adr029/console-frontend.md) |
| 控制台 | 合成预览里没有 Lead 的场景 | [lead-trial-2](adr026/lead-trial-2.md)、[0.45.2](installs/0.45.2.md) |
| 控制台 | Lead 名下的微任务，委派方显示成“未记录委派方” | [lead-trial-2](adr026/lead-trial-2.md)、[0.45.2](installs/0.45.2.md) |
| 控制台 | 黑板写进记录的英文句子 | [structured-reasons](structured-reasons/structured-reasons.md) |
| 控制台 | 控制台显示并能一键复制宏任务与微任务的标识 | [lead-trial-2](adr026/lead-trial-2.md)、[0.45.2](installs/0.45.2.md) |
| 控制台 | `effortRank` 在控制台的类型里是可选的，合成预览里也没有它 | 随 0.46.0 安装（[安装记录](installs/0.46.0.md)）；`apps/console/src/types.ts` 里 `effortRank` 已是必填 |
| 安装与存储 | 最新的执行活动存了两份 | [acceptance](schema-29/acceptance.md) |
| 安装与存储 | 挪基点到 0.51.0 留下的顺带项 | [acceptance](schema-29/acceptance.md) |
| 安装与存储 | 导出工具自己补写配置表的默认行 | [acceptance](schema-29/acceptance.md) |
| 安装与存储 | 备份只为升级的回滚而存在 | [backup-for-rollback](backup-for-rollback/backup-for-rollback.md) |
| 安装与存储 | 备份那一批验收时留下的几件小事 | [0.48.0](installs/0.48.0.md)、[0.49.0](installs/0.49.0.md) |
| 安装与存储 | 命令执行的 buddy 一栏存的不是 buddy | [step3-group-c](reliability-and-step3/step3-group-c.md) |
| 安装与存储 | 数据库的八成是命令日志与心跳事件（随 0.42.0 安装） | [0.42.0](installs/0.42.0.md) |
| 运行环境 | Windows 上同一用户的另一次登录连不上服务，客户端也不核对服务端是谁 | [c-two-090](release/c-two-090.md) |
| 运行环境 | Windows 上 harness 发现的两项测试不能运行 | 两项测试改用 `os.pathsep`（`tests/python/buddy/harnesses/test_harness_discovery.py`），Windows 上的 CI 通过 |
| 运行环境 | C-Two 关停时遇到被冻结的对端，会等满时限 | C-Two 0.7.5 修好，本项目锁 0.9.0（`pyproject.toml`） |
| 运行环境 | 原因1 留下的四件小事 | [structured-reasons](structured-reasons/structured-reasons.md) |
| 运行环境 | `test_ctwo_service` 的等待通道测试有时序竞争 | [0.48.0](installs/0.48.0.md) |
| 路由与额度 | 任务文本超过 8 KiB 就路由不了 | [acceptance](schema-29/acceptance.md) |
| 路由与额度 | Router 的角色文本把画像一项标成了 “Buddy:” | `src/hey_my_buddy/buddy/roles/texts/router.txt` 里已是 `Profile:` |
| 路由与额度 | 续接被拒之后，Host 要再发一次继续才会重建 | [step3-group-c](reliability-and-step3/step3-group-c.md) |
| 路由与额度 | 日常状态目录里留着旧的 Codex 私有主目录 | [acceptance](schema-29/acceptance.md) |
| 路由与额度 | Codex 作 Router 时，无工具的快速调用在 0.30.0 上不可用 | [batch4-host-review](adr030/batch4-host-review.md) |
| 路由与额度 | 黑板副本上起的服务，停止时会强杀原服务的执行 | 开发者撤销了复制日常黑板起服务的做法；真实数据只经 `tools/export_board_sample.py` 的只读样本（AGENTS.md） |
| 委派与 Worker | 停止来源的覆盖规则只点名了 console 与 service | `blackboard/tasks/workflow.py` 的 `_request_attempt_stop` 改按 `WRAP_UP_INITIATORS` 判定 |
| 委派与 Worker | 同一个现有检出上，两个只读微任务共享不了 | [workspace-read-sharing](workspace-read-sharing.md) |
| 委派与 Worker | `health` 与控制台看不出一个 Worker 一直在崩溃 | [adr031](adr031/adr031.md) |
| 委派与 Worker | 每次执行一个进程，取代常驻的 Worker 池 | [adr031](adr031/adr031.md) |
| 委派与 Worker | 替身 Worker 在前任的执行结算之后显示为 `lost` | [reliability-batch-a](reliability-and-step3/reliability-batch-a.md) |
| 委派与 Worker | 改动超过 512 个文件的产物，封存得了，验收与回收都验不了 | [seal-limit](adr002/seal-limit.md) |
| 委派与 Worker | 时限到了的取消，结果里写的是用户取消 | 预期时长不再到点停止执行（[ADR-022](../decisions/022-expected-duration-and-graceful-stop.md)，[C 组记录](reliability-and-step3/step3-group-c.md)） |
| 委派与 Worker | Codex 与 Claude Code 的 Worker 不能接收问询 | [in-turn-inquiry](adr031/in-turn-inquiry.md) |
| 委派与 Worker | `execution-submit` 的去留：审计结果（2026-10-17） | [execution-submit-0.45.0](adr007/execution-submit-0.45.0.md) |
| 委派与 Worker | 契约版本钉了两处 | 契约版本只钉一处（`tests/python/blackboard/store/test_current_core.py`） |
| 委派与 Worker | 问询在日常黑板上第一次用就没有送到 | [live-channel-0.43.0](live-channel-0.43.0.md) |
| 委派与 Worker | 完整检查不包含前端测试 | [complete-check-and-dispatch-flake](release/complete-check-and-dispatch-flake.md) |
