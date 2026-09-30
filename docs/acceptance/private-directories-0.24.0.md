# ADR-019 第 11 条：私有目录与备份范围（0.24.0）

本批基于 `socu/buddy-core` 的 `0c5592b`，在独立 `socu/private-dirs` worktree 实现第 11 条，契约 0.24.0，schema 15。账户、密钥输入、OAuth 与 B 批第二阶段不在范围内。Windows 真机及日常看板实际整理未验证；本批未安装、升级或改变用户配置。

[设计文档](../design/private-directories.md) 的逐路径归类表覆盖 DSH、ZCode、Codex、Claude、command、审阅 Router、快速 Router、审阅验证与续做。Worker 独立账户只预留 `harnesses/<adapter>/accounts/worker/`，供后续 B2 使用。旧看板布局由私有夹具复现。

聚焦验证已完成：适配器不变式矩阵 7 项，受影响适配器回归 119 项，DSH Python 35 项、DSH Node 31 项；私有目录/旧布局 8 项，storage 6 项、upgrade 14 项、既有 upgrade migration 5 项及两项受影响 workspace cleanup 检查。主 Host 的预检 6 项和 CLI 表面/帮助 8 项已通过；首轮整合检查与独立 buddy 审查已完成，暴露的阻塞项由两个隔离 Worker 修复；最终通过结果待确认后补入本文。

首轮 command Worker 在原独立 worktree 运行 `npm --prefix apps/console ci`、类型检查、前端测试和构建，四项退出 0（51 个测试文件）；随后完整 `uv run --frozen python -m buddy.checks` 跑 1,682 项 Python 测试，结果为 5 个失败、3 个错误，Node 因 Python 失败未执行。失败涉及已更改的契约/快照字段、旧路径测试夹具、command 的真实停止证明及 shared skill 的 4 KiB 上限；不能作为完整通过证据。

完整只读快照的 buddy 审查在固定输入 `05b45d92b8ddb7b33b3039c869275e17b6094807` 上交付 5 个阻塞项：restore 暂存链接、快速 Router 证据写入父目录/可变控制、storage receipt/pending 绑定、runtime READY 读取顺序，以及 fence 后 detach 前的预检缺口。Host 核对后已接受审查成果并委派修复；这不等于源码验收。第一次审查因未纳入九个 untracked 文件返回 attention，随后用 includeUntracked 补齐固定输入，保留原始记录。

合成控制台预览由 Codex 内置浏览器验证：1440×900、768×800、390×800、320×720；跳过/拒绝数量与路径原因可展开，页面与提醒均无横向溢出，浏览器 warning/error 列表为空。独立 Playwright CLI 的 Chromium 缓存缺失，使用内置浏览器完成同一 DOM 验证；未安装浏览器或日常服务。预览标签与服务已关闭。

用户随后明确授权通过已运行的 0.23.0 buddy 委派。其 RPC 仅用于本批任务的注册、等待、读取成果与验收；实现测试始终使用独立私有 state/runtime。没有扫描或整理日常看板旧数据，没有安装升级服务或改变用户配置。B 批第二阶段继续暂停。

两项修复的正式封存补丁已由 Host 从固定 input/output Git 对象重建并验证哈希：备份/恢复/storage/upgrade 为 `243974989c3527caa9b21794dd02895ee36592f2cddc2761724e068ad4e9f37e`（输入 `b1dc7e1d05acb051799158c5ac86748226c0ac34`，输出 `82bb5ebda6bebf01ab0e4f50094eed221c7a67c7`）；Adapter/Worker 为 `81d4f12582decad70d2c8899d964cc6a787f4d86b52dfe1fbb10b931dddf2c1b`（输入 `57f956974a3598c4955e6bc58cd9da20c9268f3f`，输出 `ee8d65e647b3927f9132a9b056f0d9bc3dea1f5a`）。两者应用前均通过 `git apply --check`，且黑板的 native/controller 与后代停止证据为 confirmed；模型交回 attention 不代替该证据。

原 worktree 的 command Worker 补验备份、Windows publication、预检、私有目录、storage、upgrade、契约与控制台 HTTP 共 100 项，退出 0；Adapter 不变式、crash windows、workflow Worker、startup、turn IO、review probe、routing capability 及三类 no-tool 回归共 86 项，退出 0。针对 Worker 沙盒内无法 loopback bind 或 ps 观察留下的九个明确私有测试根，复用 checks 的 cooperative stop、只读进程观察和生命周期锁检查后均得到 clean，再回收；新补验根也 clean。未用缺失 PID 或观察失败当作停止。

shared skill 补充本地预检与 includeUntracked 输入规则，同时保留权限、路由、等待和验收约束；源码 4,090 bytes，4 KiB 约束的 3 项检查通过。未修改已安装的 skill。

## 归类与六项验收对应

详细逐写入者盘点在设计文档；以下归类与实际白名单及模拟执行矩阵对应。证据均为声明的普通文件，凭据与私有内容位于实际 adapter 的 harnesses 区；账户行只是路径预留。

| 路径/入口 | 证据 | 凭据 | 私有内容与保留规则 |
| --- | --- | --- | --- |
| 通用 Worker | task/turn input/output、spawn intent/marker、harness selection/prestart failure、runner logs、activity | agent-credential.json | 凭据在真实停止后删除；未知停止保留 |
| DSH | dsh-run/stdout.log、stderr.log、session-capture.json；native-usage；inquiry journal/error | inquiry.json 与 agent credential | settings/patch、非分组 session root、no-tool home/profile；按目标/attempt 分区，目标私有内容随其受管工作区回收 |
| ZCode | 普通 turn、activity、native stderr、inquiry journal/error、结构化 result | zcode-control.json（内含 token）、finish-bridge.json、provider snapshots、inquiry/agent credential | goal native DB/storage/binding 支持续做；凭据/快照真实停止后删除 |
| Codex | codex-control、turn、activity、native stderr、结构化 result | agent credential；review/fast auth 链接 | goal binding 保留用于续做；review/fast 私有 CODEX_HOME 在私有区，auth 链接停止后删除 |
| Claude | claude-control、turn、native stderr、结构化 result | agent credential | attempt 私有 settings；延续既有重建模式；原安装 harness 持有的登录/用户 store 不作为本批账户变更 |
| command | task.txt、runner stdout/stderr、spawn records | 内建普通 command 路径无模型凭据 | 任意额外输出需登记证据，否则预检跳过并提醒 |
| review Router | readonly control 与路由/result/log | 私有 auth 链接 | frozen mirror/review-native 位于实际 adapter 私有区；双层停止证据控制清理 |
| fast Router（DSH/ZCode/Codex） | no-tool control、call-1/2 request/result、logs | provider snapshots/auth 链接 | 私有 cwd/home、patch/config dump、native/cache/session 位于私有区 |
| review-check | 外层 Worker 结果与验证摘要 | 私有 auth 链接 | Codex 私有 fixture/runtime/root；只在 native/controller 停止都确认后回收 |
| B2 独立账户预留 | 无 | 本批不实现 | harnesses/<adapter>/accounts/worker/，storage 始终保护 |

| 第 11 条要求 | 实现与测试证据 |
| --- | --- |
| 分区 | shared private paths、全部 adapter/Router/review-check 与续做矩阵；attempt 只含白名单普通文件 |
| 白名单/提醒 | 精确文件与容器规则，未知树计为一项且不遍历；skipped count 与 20 样例；health/console 当前提醒，浏览器四宽度验证 |
| 生命周期 | 真 stop 后删除 credential/link/provider snapshot；native resume binding 保留；workspace cleanup 回收自有 goal roots，unknown storage reason 保留；backlog ZCode snapshot 条目已移除 |
| 链接规则 | POSIX 目录描述符安全打开/JSON 发布；restore、receipt/pending、runtime READY 与重解析点攻击夹具；Windows 真实机器未验证 |
| 旧布局整理 | 私有夹具复现所有指定布局，保留原证据；verified backup 后迁移，journal/crash/rollback、路径篡改与停止未知保留测试 |
| 防回归/预检 | 不变式矩阵、本地无写/无启动预检、20 样例之外完整检查、fence 后 detach 前复检与 installer guard；CLI help 与 owning references 同步 |

## 最终整合结果

2026-09-30（Asia/Shanghai），按要求先 `npm --prefix apps/console ci`，再执行控制台 typecheck、Vitest 和生产构建，全部退出 0。Vitest 为 51 个测试文件、601 项通过。随后 `uv run --frozen python -m buddy.checks` 全部通过：Python 1,721 项，DSH Node 161 项（14 suites），无失败、取消或跳过；总命令退出 0，检查根 teardown 成功。完整回归仍由已运行 buddy 的同一个 command 目标以明确 retry 创建新 attempt，未重放首轮失败执行。

ZCode / GLM-5.3 max 在固定输入 `04e60a38f1d18addc8edcda1d48afc39b878073e` 上完成最终只读复核，未发现新的可具体复现阻塞，空补丁和真实停止由黑板记录。复核逐项对照五项修复及安全反证、command stop 与原生双层 stop、每次 start 清除旧 proof、JSON 发布边界。两条观察级备注已保留：Windows 原生日常旧布局的路径分隔符比较可能 fail-closed；command.prepare 的 task.txt 仍沿用既有写入。当前受管新 attempt 路径未见可复现阻塞，本批不扩大修复范围。

本批源码实现仅针对 ADR-019 第 11 条，契约 0.24.0，schema 15，无 db/migrations 源码变化。ADR 状态与 backlog 已同步；B2 继续等待本批合入。Windows 真机、源候选在真实 harness 上的专门目录/权限检查、日常看板的实际整理和安装切换均未验证或执行。通过日常 buddy 的新增委派、成果读取和验收属于用户后续明确授权；这不构成对日常旧数据的扫描、整理或服务升级。
