# 控制台标识与 Lead 预览说明

## 执行计划（2026-10-15）

基点为 `31a2184f`，由整合 Host 授权本 Lead 在独立受管检出中完成四项控制台改动；最终验收由整合 Host 执行。先读过 backlog 四条原文、ADR-029、共享 skill 与 workflow 的 Lead、工作区和固定产物验收规则。

分成两个经默认路由的名下微任务，并行使用独立 worktree：微任务 A 负责标识显示与复制、命令执行隐藏不存在的 buddy、Lead 委派方，写入范围为 `apps/console/src/`；微任务 B 负责 `waiting-owned` Lead、时间轴字段投影与浏览器场景，写入范围为 `tests/probes/macro_task_console_preview.py`、`tests/python/cli/test_host_preview.py`、`apps/console/browser/lead.check.ts`，必要时修改现有 `apps/console/browser/actors.check.ts`。两者不修改同一文件；A 的新组件与测试在 src 内，B 的浏览器检查只依赖已有的 Lead 呈现和补充后的预览数据。

微任务 A 只跑所改组件及数据工具的定向 Vitest 和 i18n 检查，不跑前端全量、Python 或浏览器检查。微任务 B 只跑 `tests/python/cli/test_host_preview.py` 与自己的 Lead、actor 浏览器检查，先安装本检出的 console 开发依赖并将浏览器所需构建写入 ignored `tmp/lead-preview-assets`，在 ignored `tmp/` 中生成继承原配置的临时 Playwright 配置，将 webServer 的 assets 指向该目录、testDir 指回原 browser 目录，避免触碰打包资源。Python 必须经 Host 指定解释器运行检查器，采用默认进程数并清除继承的运行时、Worker 与代理凭据变量。

Lead 对每份交付读取固定 Git 产物的差异与范围，在该微任务检出中重跑受影响测试；范围内缺陷用 `continue` 退回。验证通过后将固定产物的差异合入本 Lead 的检出，以实际整合提交作为 `accept.target` 验证的目标；若产品拒绝该目标或整合流程无法完成，记录命令与输出并向 Host 请求处理，不绕过验收。

全部整合后依次运行 `npm --prefix apps/console ci`、前端全量测试、浏览器检查、重建 `src/hey_my_buddy/console/assets`、指定解释器的 `tests/python/cli/test_host_preview.py`；不跑 Python 完整检查，不安装产品，不改变日常服务。

## Lead 使用记录

角色说明给出了执行凭据文件；每次启动器调用显式设置 `HMB_AGENT_CREDENTIAL_FILE`，由启动器读取，不读取文件内容。`help submit` 成功返回参数规则；Lead 提交省略 `hostId`、owner 与宏任务字段。

当前 shell 的 Node.js 为 25.8.1，不在 console 声明的支持版本范围内；已找到现有 Node.js 24.21.0，前端命令统一将其 bin 放在 PATH 前端，尚未运行验证。

第一次提交命令形式为 `HMB_AGENT_CREDENTIAL_FILE=<本次角色给出的凭据文件> ~/.agents/skills/hey-my-buddy/scripts/hey-my-buddy submit --params-file tmp/ui-submit.json`，第二次改用 `tmp/preview-submit.json`，两次退出码均为 0，没有补 `hostId`。回执的 `leadOwnership.leadRunId` 均为本 Lead 的 `9f8571e9-5983-4afa-9232-864a8d56282c`，状态为 `executing` / `queued`，原因 `awaiting-model-selection`；首次提交的凭据问题本轮未复现。A 的 runId 为 `79771ce9-890b-4cab-867b-4e05c68791da`，B 为 `97e3de5a-d4f5-4d15-ba79-a8362b8b8701`，buddy 尚待路由选出。两份提示均写明写入范围、限定检查、三句交付，以及 Host 的全部环境规则；没有授予继续委派权。

本轮提交两项后返回 `waiting`，等待产品的 `waiting-owned` 叫醒路径；后续回合验明投递与恢复上下文。

第二回合由 A 的 `workflow.turn_returned` 自动叫醒，`resumeMode` 为 `native-session`，输入恢复两项的当前状态与变化标记，新的执行凭据路径也随角色说明到达。A 已交回，B 仍执行中；读取两项的 `get` 都成功，不需要 Host 控制文件，也不读凭据内容。A 路由为 Codex CLI 的 `openai / gpt-6.1-sol / high`，B 路由为 Codex CLI 的 `openai / gpt-6.1-sol / max`。

## 交付与验收

A 的固定产物为 `e52a083f-e826-4d95-98e5-1b77ca4bb8d5`，输出提交 `d9392b57`，12 个改动文件均在 `apps/console/src/` 内；交付了共享复制标识组件及三处入口、命令执行的 buddy 隐藏、Lead 原始来源显示、中英文文案及行为回归测试。Lead 读取固定 Git 差异并核对：按 `spec.adapter === command` 判断命令执行；原始来源保留 `sourceHostId`，无来源 Host 时采用直接 Lead / 根 Lead；当前管理方仍独立显示；剪贴板成功与失败经边界反馈，保留已选记录离开读取范围后的标识。

Lead 在 A 的检出使用 Node.js 24.21.0 重跑 `npm --prefix apps/console test -- src/components/CopyableIdentifier.test.tsx src/task-presentation.test.tsx src/console-data.test.ts src/task-title.test.tsx src/macro-task-timeline.test.tsx src/i18n/language.test.ts src/i18n/sources.test.ts src/i18n/unused.test.ts`，8 个文件 71 项全部通过，退出码 0；固定差异的 `git diff --check` 也通过。A 返修 0 次，真实布局与原生剪贴板留待整合后的浏览器检查。

A 固定差异通过 `git diff --binary <原始输入提交> <固定输出提交> -- apps/console/src` 和 `git apply --index` 整合，在 Lead 的独立检出生成提交 `5a873a48`，未碰分支、标签或 stash。验收调用为 `HMB_AGENT_CREDENTIAL_FILE=<第二回合执行凭据> <启动器> accept '{"runId":"79771ce9-890b-4cab-867b-4e05c68791da","artifactId":"e52a083f-e826-4d95-98e5-1b77ca4bb8d5","note":"<差异审查与 71 项检查记录>","target":{"path":"<Lead 自己的受管检出>","ref":"5a873a48"}}'`，退出码 0，返回 `state: accepted`、`integration.state: verified`、`strategy: patch`、`reclaim.removed: true`，整合标识为 `int-b5214ce3-185e-49ab-8463-6bc54ba03543`；实际提交目标可用，没有要求调整验收条件。

B 首回合未形成可验收的交付。第三回合由 `workflow.turn_failed` 自动叫醒；`get` 显示 B 为 `failed`、`terminationReason: harness-error`、`shutdownConfirmed: true`，`result` 的已选尝试退出码为 1，错误为 `multiple native final messages completed`，并含 `turnError: the runner did not exit zero`。执行从 05:26:53 UTC 到 06:06:25 UTC，原生回合没有完成可用结果；目前这是 Lead 试跑最不顺的一处，失败回执不能直接当作模型交付。

黑板保留了 B 的固定部分产物 `ce2fd2ae-fef8-488f-8ffe-5dfd829158d7`，输出提交 `29ffedbd`，仅修改 `apps/console/browser/lead.check.ts`、预览脚本和 `test_host_preview.py`；状态明确为 `partial: true`、`verified: false`、`final: false`，未整合、未验收。Lead 读取固定差异和该微任务检出内保存的 Playwright 失败上下文，发现四种语言 / 宽度组合均在检查器标题断言失败：`Expected: 等待 Lead`、`Received: 等待 Host`；检查器类型栏与时间轴已显示 Lead，但 `inspector-card.ts` 的 `head` 仍直接调用 `spanHead(host)`。

该新场景暴露的根因位于本 Lead 的总写入范围 `apps/console` 内，A 已验收，不存在并行写入。Lead 用 `scope-amend`（`commandId: preview-inspector-scope-1`、`expectedRevision: 4`、`expectedScopeVersion: 1`）在 B 原范围上增加 `apps/console/src/inspector-card.ts` 与已有 `apps/console/src/macro-task-timeline.test.tsx`，理由为修复等待 Lead 检查器标题，返回退出码 0、revision 5、scopeVersion 2；没有扩展本 Lead 的 Host 授权范围。随后经 `continue --params-file tmp/preview-continue.json`（`commandId: preview-inspector-rework-1`、`expectedRevision: 5`）要求 B 从固定部分产物继续、修复头部根因、不放松浏览器断言，只新增运行检查器定向 Vitest，其他环境规则与限定检查维持，返回退出码 0。B 累计返修 1 次，尚待下一次交付；本 Lead 未代改子任务代码，也未通过其他工具重试失败操作。

第四回合由 B 的 `workflow.turn_returned` 叫醒，B 的继续执行采用 `reconstructed-new-session`，保留了首回合固定改动，最终在三句摘要中准确列出检查与边界。固定最终产物为 `90697df6-7baa-4fae-b507-fff0ef70ee90`，输出提交 `86d4bb99`；累计差异的五个文件覆盖首回合的 `lead.check.ts`，没有因最后一回合未再次修改该文件而漏掉它。B 补充了一个两回合 Lead、一个请求 Lead 决定的名下微任务、一个仍排队的名下微任务，明确记录最初创建它们的 Lead 回合；等待状态不冒充进程仍在运行。检查器头部与类型栏统一复用时间轴已解析的标题，现有非 Lead 等待与执行片段的轮次也有行为断言。

Lead 读取累计固定差异、`git diff --check` 通过后，在 B 的实际检出重跑指定 Python 检查器的 `tests/python/cli/test_host_preview.py`（3 项、默认 4 进程、私有根已清理）、`npm --prefix apps/console test -- src/macro-task-timeline.test.tsx`（43 项），并重新构建 ignored `tmp/lead-preview-assets` 后运行 `npm --prefix apps/console exec -- playwright test --config tmp/lead-preview.config.mts lead.check.ts actors.check.ts`（10 项，覆盖中英文及 1440 / 1024 宽度）；全部退出码为 0。浏览器检查真实的只读预览停止拒绝、键盘操作及文本可读性，没有验证实际后端成功停止。

恢复时一个容易误用的点是：B 的最新 workspace ID 为 `ws-c831af755ec7c780c570d746a609316a`，但类型是 `existing`，实际检出路径仍是最初的 `ws-c612e1793e4fef71a82db606c1fff421/checkout`，必须用 `get.workspace.path`；Lead 按 ID 猜目录导致两次命令无法启动，改用回执真实路径后通过。另一次手工复核命令把 `--config` 误写成 `../../tmp/lead-preview.config.mts`，npm exec 实际保留检出根为 cwd，未找到配置；改成根相对的 `tmp/lead-preview.config.mts` 后 10 项通过。这两处是 Lead 自己的命令错误，未发生权限拒绝。

B 的五份累计固定改动原样合入 Lead 提交 `64a4ab7a`。使用第四回合执行凭据调用 `accept`，绑定最终 artifactId 和 `target: {path: <Lead 自己的受管检出>, ref: 64a4ab7a}`，退出码 0，返回 `state: accepted`、`integration.state: verified`、`strategy: patch`、`reclaim.removed: true`，整合标识为 `int-b1b3c205-a521-46e2-b97b-93ed88ac0bc1`。两项均已验收，A 返修 0 次、B 返修 1 次；没有遗留名下微任务，开始整合后的最终验证。

## 整合后检查与停止点

在 Lead 的整合提交 `64a4ab7a` 上，按要求使用 Node.js 24.21.0 依次运行：`npm --prefix apps/console ci` 退出码 0；`npm --prefix apps/console test` 退出码 0，81 个文件、1,119 项全部通过，耗时 30.26 秒；`npm --prefix apps/console run test:browser` 退出码 1，停在命令自身先执行的 `tsc --noEmit` 阶段，浏览器用例未开始。原始输出保留在 ignored `tmp/console-full-test.log` 与 `tmp/console-browser-test.log`。

类型检查错误原文为 `src/console-data.test.ts(25,25): error TS2352: Conversion of type '{ spec: { adapter: string; }; lineage: { buddy: {}; }; }' to type 'Task' may be a mistake because neither type sufficiently overlaps with the other`，第 26 行的 `{ spec: { adapter: string; }; } as Task` 同样触发 TS2352，缺少 `runId`、`task`、`status`、`owner` 等字段。两行均来自 A 的新增测试；Vitest 执行通过没有覆盖 TypeScript 编译。Lead 在验收 A 之前只重跑其定向 Vitest，没有类型检查，这是本轮验收检查安排的缺口，不是产品把不合格的编译结果判为通过。

Host 要求范围内缺陷用 `continue` 打回、不由 Lead 代改，且验收与整合流程走不通时停下报告；共享 workflow 同时明确已验收的微任务不能继续。A 与 B 已经完成固定产物验收并回收检出，当前没有可用于打回的非终态微任务，因此 Lead 停在此处，请 Host 决定返修途径，不自行修改测试、不以 `notRequired` 或调整验收结果绕过。需要的具体修复是给这两项测试使用符合类型的 fixture（或者明确的测试边界断言），然后重跑类型检查及受影响测试；之后从浏览器检查继续，成功后重建打包资源，再按指定解释器运行 Python 预览检查。若 Host 允许在本 Lead 检出直接修复这两行，即可继续同一份整合产物；若仍要求由微任务实现，则需要 Host 授权新的返修微任务。

截至停止点，最终打包资源未重建，最终整合检出的 Python 检查未运行；此前 B 自身交付上的 Python 3 项和浏览器 10 项通过仅证明 B 的独立产物，不能当作整合后检查通过。额外原生剪贴板与窄屏复制入口检查尚未执行。

## Host 继续指令与返修计划

第五回合 Host 明确：已验收微任务晚发现的缺陷，另提交名下返修微任务是产品已有路径，不需要额外授权；不授权 Lead 自己修改代码。此前 Lead 把“不能继续已验收微任务”理解成需要再请示新的返修途径，造成一次可避免的停顿，属于 Lead 对流程的理解偏差。

新增顺序执行的微任务 C，以已整合封存提交 `12b8a4b0` 为基点，写入范围仅 `apps/console/src/console-data.test.ts`，按文件内或相邻测试的合法夹具写法修复第 25、26 行，不使用 `as unknown as` 绕过类型检查。C 只运行 `npm --prefix apps/console run typecheck` 与 `npm --prefix apps/console test`，交付三句话，环境规矩四项原文写入提示。C 交回后 Lead 审查差异并自行重跑这两项，再整合并验收，接续浏览器检查、资源重建、指定 Python 预览检查。

C 已通过本回合执行凭据 `submit --params-file tmp/fixture-repair-submit.json` 提交，退出码 0，runId 为 `67b47a8e-28ec-4778-a6b3-a16327409c3a`，回执为 `executing` / `queued`、`awaiting-model-selection`；未指定 buddy，继续默认路由。提示中带了完整环境规矩和验收前 typecheck 要求，目前 C 返修 0 次，等待交回。

本次检查遗漏形成一条后续验收要求：凡名下微任务改动控制台，验收之前先运行 `npm --prefix apps/console run typecheck`，因为 `npm test` 不执行类型检查；这条也写入后续微任务提示。它只补齐本次工作检查，不修改产品行为或其他文档。

供 Host 另查的 harness 失败证据：微任务 B 为 `97e3de5a-d4f5-4d15-ba79-a8362b8b8701`，首回合 `af8881ef-3ac7-43da-a968-392b67692206`，尝试 `59fa3a0a-d0d3-49f4-bd35-d735de450984`，Codex CLI，`openai / gpt-6.1-sol / max`；`get.currentTurn.createdAt` 为 `2026-10-15T05:26:53.511Z`，`updatedAt` 为 `2026-10-15T06:06:25.624Z`。当时 `get` 输出 `state: failed`、`disposition: null`，`result.selectedAttempt.exitCode: 1`、`error: multiple native final messages completed`，同份结果中 `turnError: the runner did not exit zero`、`terminationReason: harness-error`、`shutdownConfirmed: true`；没有有效三句模型交付，只有标记为 partial / unverified 的固定部分产物 `ce2fd2ae-fef8-488f-8ffe-5dfd829158d7`（提交 `29ffedbd`）。通过本轮执行凭据读取自身名下的 `get` / `result` 的原始 JSON 保留在 ignored `tmp/preview-failed-get.json`、`tmp/preview-failed-result.json`，未读取日常服务日志或私有 harness 记录。

## 最后交付检查

C 由默认路由选出的 DSH CLI `deepseek-official / deepseek-flash / max` 完成，返修 0 次；固定最终产物为 `25c87167-6a4f-412f-9402-7dd50bac91dd`，输出提交 `b987ef84`，只改声明的一个文件。差异增加完整的 `Task` 夹具，按参数覆盖需要测试的属性，两处错误断言改用该夹具，命令执行用例仍验证即使记录了 buddy 也应隐藏，未使用 `as unknown as`、`any`、忽略编译错误或改配置。

Lead 对 C 先运行 `npm --prefix apps/console run typecheck`，退出码 0，再运行 `npm --prefix apps/console test`，81 文件 1,119 项全部通过，退出码 0，用时 25.08 秒；单文件固定差异的 `git diff --check` 通过。原样差异整合为 `bd309c86`，`git diff --exit-code b987ef84 HEAD -- apps/console` 退出码 0，证明最终整合的控制台树与刚才验证的产物相同。用本回合执行凭据对 C 的最终 artifactId 和本 Lead 检出的实际提交 `bd309c86` 调用 `accept`，返回退出码 0、`accepted`、`integration.state: verified`、`reclaim.removed: true`，整合标识 `int-aeba5b24-de70-41be-9e5f-faa99271d259`；三项名下微任务均已验收，没有未收齐的工作。

依 Host 的继续指令，在整合状态上接续运行 `npm --prefix apps/console run test:browser`，106 项全部通过，退出码 0，耗时 16.6 秒（原始输出 `tmp/console-browser-final.log`）；然后 `npm --prefix apps/console run build` 退出码 0，重建打包的控制台资源（原始输出 `tmp/console-build-final.log`）；最后清除继承运行时、Worker 和凭据环境变量，以 Host 指定解释器运行 `PYTHONPATH=src <指定解释器> -m hey_my_buddy.cli.checks tests/python/cli/test_host_preview.py`，采用默认 4 进程，3 项通过，私有根删除、退出码 0（原始输出 `tmp/python-preview-final.log`）。没有运行 Python 全量检查，没有安装产品，没有启动、停止或重启日常服务或 Worker。

资源替换仅涉及旧脚本 `index-DnzGstcH.js` 换成新脚本 `index-iWDO4HEY.js` 及 `index.html` 引用更新，旧脚本是本次被替换的产物，其他打包资源保留。最终验收仍由整合 Host 完成，以上是本 Lead 对源码及合成预览的检查证据，不代表安装或发布。

额外以 ignored `tmp/copy-browser.config.mts` 与 `tmp/copy-browser.check.ts` 在真实 Chromium 中复核原生剪贴板：中英文、1440 与 390 宽度，宏任务标题、微任务检查器、详情的按钮都复制完整标识，悬停可读完整值，独立微任务在宏任务标题处复制 `run:preview-run-c1`；宏任务头部与检查器控件无重叠、页面无横向溢出，详情仍显示委派它的 Lead。8 个场景最终全部通过；首次脚本在 390 宽度仍选择隐藏的桌面 `.tl-label` 而有两项超时（6 项通过），改为窄屏实际使用的 `.tl-entry` 后只重跑这两项，2 项通过、退出码 0，属于临时检查脚本的入口错误，没有修改产品源码。原始结果分别在 `tmp/copy-browser.log` 与 `tmp/copy-browser-narrow.log`。

最终交回：四项改动、夹具返修与重建资源都在本 Lead 检出；前端全量 1,119 项、浏览器全量 106 项、指定 Python 3 项通过，另有原生复制与窄屏 8 场景通过。名下共 3 项，A 为 Codex CLI `gpt-6.1-sol / high`（返修 0）、B 为 Codex CLI `gpt-6.1-sol / max`（返修 1）、C 为 DSH CLI `deepseek-flash / max`（返修 0），均经默认路由、固定产物审查与实际提交验收，已回收子检出。本次 Lead 的凭据、正常交付叫醒、失败叫醒、恢复、范围修订、累计产物整合和三次验收可用；最不顺的是 B 首回合约 39 分钟后失败，以及 Lead 验收前未先做 typecheck、误以为晚发现问题需要 Host 另批返修路径造成的额外回合，证据和纠正均已记录。

## Host 再审返修（第七回合）

Host 已核对第 1、3、4 项、整合与先前记录的全量检查，但复现第 2 项遗漏：工作流的命令执行 buddy 是 `{"adapter":"command"}`，`WorkflowPanel` 的记录细节仅以 `value.buddy` 是否非空决定显示，因此仍会写出 `undefined · undefined / undefined｜buddy 来源未记录`。此前 A 只处理执行列表与详情头部，Lead 的回归夹具没有经 WorkflowPanel 覆盖这个真实形态，先前“四项完成”的判断被 Host 纠正。Host 另指出标识 span 的原生 `title` 与 Tooltip 叠出两个提示。

新增顺序微任务 D，以交回时封存的完整整合提交 `1adae637` 为基点，集中修改 `apps/console/src/console-data.ts` 中“这次执行有没有模型 buddy”的共享规则，让执行列表、详情头部、记录细节共同使用，补 WorkflowPanel 实际渲染命令记录细节的回归；同时删除 `CopyableIdentifier` 标识 span 的原生 `title` 并更新相应行为断言。写入范围限制为上述数据工具、三个消费组件、复制组件及其相关现有测试，不动 Python、浏览器测试、文档或打包资源；文件间无并行写入。D 只跑 typecheck 和明确列出的相关组件 / 数据工具测试，Lead 验收前先重跑 typecheck 与相关测试，整合后再跑前端全量、浏览器全量并重建打包资源。

D 用第七回合执行凭据 `submit --params-file tmp/command-record-repair-submit.json` 提交，退出码 0，runId 为 `8b26486c-f55e-40b7-979b-44f57a80d720`，回执为 `executing` / `queued`、`awaiting-model-selection`，未指定 buddy；提示带完整环境规矩、三句交付和验收前 typecheck 要求。D 返修 0 次，等待交回；前三项验收状态保持。

第八回合因 D 交付自动叫醒；D 路由为 ZCode CLI `bigmodel-api-2 / GLM-5.3 / max`，首份固定产物 `862e3a62-06d0-4402-97e4-b6ef9e73c3e7`、提交 `813d0121`，6 个文件均在授权范围。Lead 重跑 typecheck 退出 0，5 个指定测试文件 37 项通过，退出码 0；但逐项审查发现 `hasModelBuddy(task)` 仍只读详情任务 `spec.adapter`，WorkflowPanel 仍可能把接口返回的 `{adapter: command}` 当模型 buddy：新增回归额外修改了详情任务 spec，未覆盖 Host 只替换工作流 buddy 的原始复现。此处未因测试通过而验收，也未整合首份产物。

Lead 用 `continue`（`commandId: command-record-rework-1`、`expectedRevision: 4`）打回，明确要求共享规则识别实际工作流 buddy 的适配器标记、WorkflowPanel 本身不另写判断，并从原夹具只替换接口返回 buddy 做回归；复制组件去原生 title 的修复保留。调用退出码 0，D 返修累计 1 次，等待下一份交付；检查仍限先 typecheck，再原五个定向测试文件。

第九回合由 D 第二份交付叫醒，最终固定产物 `633d5297-d582-4b99-a7b5-4247d3f8c2f0`、累计输出提交 `b53f519d`，仍只有原先六份授权文件。`hasModelBuddy(task, buddy)` 统一判别基础设施适配器与实际记录的 buddy 形态：执行列表、详情头部经 `taskExecutor` 传 lineage 的 buddy，WorkflowPanel 记录细节传工作流自己的 buddy；裸 adapter 标记不会进入模型名字格式化，尚待分配的模型执行保留既有待确定语义。WorkflowPanel 回归保留原详情 spec，只把接口返回 buddy 改为 `{adapter: command}`，同时断言整栏消失、没有 undefined、真实模型 buddy 仍显示；复制组件只保留 Tooltip，测试明确原生 title 不存在。

Lead 重跑 typecheck 退出 0，随后 5 个定向文件（`console-data.test.ts`、`WorkflowPanel.test.tsx`、`components/CopyableIdentifier.test.tsx`、`task-presentation.test.tsx`、`task-title.test.tsx`）37 项通过，退出码 0，累计固定差异的 `git diff --check` 通过；D 原样整合为 `895f7db4`，`apps/console` 树与已测产物一致。用本回合执行凭据绑定最终 artifactId 和实际目标提交 `895f7db4` 调用 `accept`，退出码 0，返回 `accepted`、`integration.state: verified`、`reclaim.removed: true`，整合标识为 `int-474335b2-ef34-4d6c-bf67-3d8868f4aec4`。D 由 ZCode CLI `GLM-5.3 / max` 完成，返修 1 次；四项名下微任务现已全部验收，继续整合后全量检查与资源重建。

本次整合后的最终验证按顺序完成：`npm --prefix apps/console test`，81 文件 1,120 项通过，退出码 0，耗时 24.41 秒；`npm --prefix apps/console run test:browser`，106 项通过，退出码 0，耗时 16.3 秒（含命令自身的 typecheck 与构建）；`npm --prefix apps/console run build` 退出码 0，重新生成打包资源；最后仍按 Host 指定解释器、默认 4 进程运行 `tests/python/cli/test_host_preview.py` 的检查器，3 项通过，私有根清理、退出码 0。原始输出分别保留在 ignored `tmp/console-command-repair-full.log`、`tmp/console-command-repair-browser.log`、`tmp/console-command-repair-build.log`、`tmp/console-command-repair-python.log`，未跑 Python 完整检查。

最终 `index.html` 引用脚本 `assets/index-1SHUT5Hj.js`，资源目录只有这一份脚本，引用的图标、样式与脚本均存在；先前中间构建的 `index-iWDO4HEY.js` 已随本次资源替换移除，不交付两份并存的脚本。命令执行的执行列表、详情头部和工作流记录细节现由同一规则决定是否显示模型 buddy，等待原因英文仍保持原状；复制标识只保留 Tooltip。四项源码改动及此次两处返修均已在本 Lead 检出整合，Host 的最终验收仍待进行，没有安装或发布。

名下最终共 4 项：A（Codex CLI，`gpt-6.1-sol / high`）返修 0 次，B（Codex CLI，`gpt-6.1-sol / max`）返修 1 次，C（DSH CLI，`deepseek-flash / max`）返修 0 次，D（ZCode CLI，`GLM-5.3 / max`）返修 1 次，均已验收并回收检出。此次 Host 打回表明，检查次数不能替代覆盖真实输入形态；Lead 后续审查以原始复现和三处消费路径为准，D 首份交付即使 37 项测试通过也被打回，最终回归不再添加会掩盖问题的 spec 前提。
