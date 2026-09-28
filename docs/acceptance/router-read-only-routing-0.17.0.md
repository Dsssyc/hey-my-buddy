# Router read-only routing 0.17.0

## 交付边界

0.17.0 源码候选已完成集成检查，schema 保持 12。未安装到日常环境，未执行真实 Router 能力探针。所有 read_only_structured_verified 仍为 false；没有已验证路由模型时，新路由请求进入 Host 边界，不调用模型。显式完整配置的普通委派沿用现有流程。

## 里程碑

| 阶段 | 提交 | 内容 |
| --- | --- | --- |
| M0 | dea4654 | 清理继承环境后保留随机控制台端口 |
| M1 | 8ae4aa1 | Python prompt v9、冻结候选、答案边界、偏好事实、预算与记录 |
| M2 | cef3790 | 通用只读结构化调用、冻结输入、续接先准备、预算和停止证据 |
| M2 补正 | 28ee98d、81756c1、24bb3ee | 原始 Git blob、链接环、准备错误、停止证明、待准备路由视图 |
| M3 | 7b49ba3 | 删除 DSH 专属路径并迁移回归测试 |
| 夹具补正 | e1daa50 | 评价与历史夹具显式 mock 能力；补齐 staged-worker 端口隔离 |
| M4 | d64cbec | 点阵片段、依据定位、预算设置、用量及构建资源 |
| M5 | 本记录所在提交 | 版本、参考文档、探针脚本与验收记录 |

Router 是 decision attempt，不写 workflow_turns，不持有 agent 凭据。请求时冻结合法候选，采用时重查原始硬约束、pin/exclude 和能力。理由、引用和程序计算的偏好结果会记录；缺少卡片引用不再否决合法选择。预算使用现有 meta/JSON，不新增表。不可观测的工具或读取量为 null。

输入从不可变 Git blob 物化，避免 export-ignore、export-subst 和 checkout filter 改写内容。原 manifest 与私有副本在停止后核验；变化以 router-input-changed 结束。受管冻结内容排除默认 ignored/cache 文件。模型提供方可以看到 Router 读取的文件。

## 验证

源码服务测试使用私有 state/runtime 与 BUDDY_CONSOLE_PORT=0。focused.py 复用 buddy.checks 的隔离和清理。阶段计数不能相加作为独立测试总数。

| 命令或范围 | 结果 |
| --- | --- |
| uv run --frozen python .dsh-skill-build/focused.py test_packaging test_runtime test_daemon test_zcode test_codex test_claude test_zcode_request_input_probe | M0：153 通过；日常服务仍监听 49637 |
| focused.py test_router test_user_policy test_catalog test_catalog_observations | M1：32 通过 |
| focused.py test_router test_codex test_claude test_workflow_preparation test_workflow | M2：149 通过，最后增量 16 通过 |
| 路由、历史、偏好、容量与并发 | 150 项首轮两处旧夹具断言失败；修复后对应两项通过，后续全量覆盖 |
| npm test（apps/console） | 39 个文件、486 项通过 |
| 最后三个路由/详情前端文件聚焦检查 | 44 项通过 |
| npm run build（apps/console） | tsc 和 Vite 通过，console_assets 已重建 |
| focused.py test_objectives test_router_probe | 35 项通过；全部离线/mock |
| uv run --frozen python -m buddy.checks | 首轮 Python 2 failures、17 errors；第二轮 1,227 项 Python、139 项 Node 全通过，退出 0 |
| focused.py test_router test_workflow_routing test_routing_history | 全量后最后的只读路由视图补正：51 项通过 |
| Git replace 原始对象隔离补正 | 4 项真实 Git 输入测试通过；不读取替换对象 |
| uv lock --check、git diff --check | 通过 |

日志保存在 .dsh-skill-build/：m0-tests.log、m1-focused.log、m2-final-focused.log、m3-routing-final.log、m4-vitest.log、m4-final-build.log、final-checks.log、final-checks-rerun.log、last-routing-view.log。失败轮次未被改写成通过。

首轮全量失败来自旧夹具配置未验证 Router、未包含预算字段的快照断言，以及 staged-worker 环境漏设随机端口。修复后先跑受影响套件，再完整复跑。原文包括：

~~~text
buddy.errors.BoardError: This configuration has no verified decision capability
AssertionError: False is not true
{"error":{"code":"CONSOLE_PORT_IN_USE","message":"Cannot bind 127.0.0.1:49637; free the port or configure BUDDY_CONSOLE_PORT"}}
Ran 1227 tests in 1046.462s
FAILED (failures=2, errors=17)
~~~

整合审查还修复了卡片输入字段误改、未确认停止却返回 cancelled、链接环未被分类、准备错误丢失机器码、预算默认值变化破坏 requestId 重放，以及准备阶段错误显示 Host 指定的问题。

## 浏览器

使用 tests/probes/objective_console_preview.py 的合成数据，没有连接日常控制台。1440×900 浅色、1440×900 深色、390×844 窄屏各覆盖完成、失败、取消、放弃，共 12 张截图：

- .dsh-skill-build/screenshots/light-{completed,failed,cancelled,abstention}.png
- .dsh-skill-build/screenshots/dark-{completed,failed,cancelled,abstention}.png
- .dsh-skill-build/screenshots/narrow-{completed,failed,cancelled,abstention}.png

DOM 实测四个桌面片段均高 22 px、没有片段文字、保留点阵。失败宽 14 px 且有细线 ×；取消是虚线且没有 ×；放弃没有 ×。已查看浅色、深色及窄屏截图。窄屏没有文档横向溢出；控制台错误和警告均为 0。

双击定位所属委派的 preview-decision-a2，没有跳到当前另一条路由；Enter 定位 preview-decision-a1。证据为 browser-detail-evidence.log、browser-keyboard-final.log、browser-console.log。

## 分发包

执行 uv run --frozen python packaging/stage-plugin.py --destination .dsh-skill-build/stage-final/hey-my-buddy，再通过 stage_smoke.py 在独立临时根目录冷启动包内 CLI。移走分发源目录后仍可读取 health/runtime，stable=true、leaks=[]、resourcesMissing=[]。未验证 Router 的 selection-request 返回 needs-host、runId=null，没有启动模型。私有服务通过自身 CLI stop 收尾。

运行时内容 ID：15366b8e3dfaa43add6f6073aeef9fd7。详细路径、身份和停止回执见 .dsh-skill-build/stage-smoke.json。文档收尾后重新生成分发目录，并核验运行资产与已验证运行时的字节一致；文档不是运行时依赖。

## 待批准的原生探针

tests/probes/router_readonly.py 默认只准备。只有 --execute 才调用模型；该开关不能代替用户逐次授权。准备、拒绝与执行分支已有 17 项 mock 测试。脚本不改黑板、配置或 capability；模型自述不能使报告自动通过。

以下命令尚未执行，每次必须用新的 output-root：

~~~sh
uv run --frozen python tests/probes/router_readonly.py --adapter claude --provider anthropic --model claude-sonnet-5 --effort medium --preset quick --output-root /tmp/adr014-claude-approved-1 --execute
uv run --frozen python tests/probes/router_readonly.py --adapter codex --provider openai --model gpt-6-sol --effort high --preset quick --output-root /tmp/adr014-codex-approved-1 --execute
~~~

元组来自本次已启用配置的只读核对。日常服务仍报告 Codex CLI 缺失；不能改日常设置或猜替代配置来绕过。Codex 还须核实其 shell 暴露、实际读根和网络策略是否满足操作边界，不能只凭请求参数认定通过。

quick 的临时上限为 60 秒、8 次工具调用、可观测时 128 KiB 读取量；Codex 最多两个同 attempt 原生回合完成一次格式纠正，Claude 一个。未知读取量为 null，停止有有界清理余量。没有美元或 token 硬上限，不把这些控制描述成费用保证。三个档位均待实测。

通过标准：内部随机 marker 可读；原生策略和工具证据证明越界读、写及工具联网被拒绝；输入和外部 sentinel 未变；请求/解析配置与原生身份可核对；预算、格式纠正和停止证据一致。缺少原生证据时保持 unverified。

DSH 当前原生策略不限制读取范围与网络，不安排收费 Router 探针，不开放该能力；--prepare-only 可记录计划，--execute 会拒绝。ZCode 按议程仅做 mock，未验证且无额度，--execute 同样拒绝。两者都没有执行 Router 原生探针。

## 黑板与配置流程偏差

Host 误把“不改动日常黑板”理解为不能登记正常委派，创建了不必要的私有黑板。最初另有两项原生子代理只读预检。用户纠正后，普通复核改用日常 Buddy；保留全部原 run 身份，没有重新派发来隐藏历史。

Host 又误把广告 effort 当作用户可用配置：文档 7e363262-0130-4b90-bfb7-d7aebbb438d1、日常复核 5193000a-df62-46b2-9521-77e2c92d4e69 使用了未启用的 Flash/high；控制台接线 a8f8e764-ace1-4e5e-a92d-7a27dc804371、探针准备 2fcaf66a-e6e1-4058-a8ec-073f14e4711f 使用了未启用的 Codex Sol/medium。发现时四项均已结束并确认停止。这是 Host 的选配错误，不构成配置支持或授权证明。

已核对日常启用配置：Flash max/off、Codex Sol high/max、Claude Sonnet 5 medium、Opus 5.5 high。实际 capability 另报告 Codex CLI 缺失。后续必须同时核对配置和实际能力。没有修改用户启用状态、偏好、容量或发布模型评价。

产物由 Host 独立检查、测试和整合；验收备注保留这些错误。收尾使用原 runId/controlFile 和官方 CLI：get → integration-record → acknowledge → workspace-cleanup-plan/apply，保留固定补丁、manifest 和 refs。逐项回执在 .dsh-skill-build/ledger/，测试迁移的早期回执在 test-integration.json、test-ack.json、test-cleanup-plan.json。被后续工作取代的初始前端 attention run 按取消收束，不伪造完成回合；私有服务在任务停止后关闭。

## 开放边界

原生 Router 能力仍全未验证；Codex 的允许操作边界、Claude 的策略/拒绝证据和读取量观测需逐次批准的探针。DSH 暂不可用，ZCode 仅 mock。预算参数待实测。日常 0.17.0 安装没有授权，也没有执行。源码检查、mock/浏览器/打包验证与日常安装是独立结论。
