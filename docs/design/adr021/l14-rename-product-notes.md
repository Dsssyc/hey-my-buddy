# ADR-021 L14：产品名字与环境变量的改名记录

本微任务以 `dcdff0274cda225bd41532cc5c656dd3c7df2cce` 为输入，版本保持 0.34.1，schema 保持 19；只改产品名字和环境变量，新增的行为限于新代确实生效后移走可确认归属的旧 skill。开发者的日常服务、Worker、看板、真实 harness 配置和凭据均未作为本次验证对象；没有提交。

清单外按具体拼写去重共改 157 个名字，包括生成的临时目录、线程、请求标识、内部 SQL 函数、预览请求头、前端额度重探存储键和启动器辅助函数。保留例外按文件行计共 76 处，逐行列在后表；指参与者的 buddy、buddyId、buddies、buddy-list、BUDDY_FIELDS、BUDDY_ID 等，以及已经使用产品全名的拼写，不计入这个例外数。

SKILL.md 已移动到 `skills/hey-my-buddy/SKILL.md`，仅 frontmatter 的 `name: buddy` 改成 `name: hey-my-buddy`；还原这一行后，文件与输入版本逐字节相同。正文中的旧安装路径、旧命令和旧术语，以及冻结的 docs、两份根 README、AGENTS.md、CONTEXT.md，都等待 Host 的文档微任务处理。当前打包 skill 的正文仍有这些旧词，这项边界不能描述成最终文档已经可用。`tests/fixtures/` 与 `packaging/generate-upgrade-fixture.py` 保持输入版本。

改名清单中的 `buddy-goal` 已在此前微任务变成 `buddy-micro-task`，本次改为 `hey-my-buddy-micro-task`；不存在的 `BUDDY_VIEWS`、`BUDDY_PREVIEW_PYTHON` 等旧树名称不增加读取者。`hey.my.buddy`、默认数据目录 `.local/share/hey-my-buddy`、`hey-my-buddy.console.theme` 和 `hey-my-buddy.console.view` 均按清单保留。旧用户 Git 引用只属于归档，本次不遍历、搬移或清理它们。

旧代接口仅留在 `install/upgrade.py`：以固定升级基点 `4b7f494670efe7a3fe053062f16cce9312a5e042` 的 READY `sourceCommit` 识别 0.34.1；探测、停止和回退都调用旧运行时自己的 BoardClient，因此旧 `buddy-control`、`buddy-wait`、`buddy-lightweight-1` 资源名由旧代码自己使用，新代码不注册别名。普通新代码只读取 HMB_；旧代子进程在这个边界把允许的 HMB_ 变量改写为 BUDDY_，不同时传两种名字。

旧环境变量的对应范围逐个为 `BUDDY_STATE_DIR`、`BUDDY_RUNTIME_ROOT`、`BUDDY_RUNTIME`、`BUDDY_RUNTIME_IDENTITY`、`BUDDY_MAX_CONCURRENT`、`BUDDY_WAIT_CAPACITY`、`BUDDY_CONSOLE_PORT`、`BUDDY_LEASE_SECONDS`、`BUDDY_MODEL_CATALOG_FILE`、`BUDDY_DEBUG`、`BUDDY_AGENT_SKILLS_DIR`、`BUDDY_CLAUDE_SKILLS_DIR`；显式开发测试允许名单还包含 `BUDDY_DSH_CLI`、`BUDDY_CLAUDE_CLI`、`BUDDY_CODEX_CLI`、`BUDDY_ZCODE_CLI`、`BUDDY_NODE`、`BUDDY_CLAUDE_FIXTURE_CASE`、`BUDDY_CLAUDE_FIXTURE_STATE`、`BUDDY_CODEX_FIXTURE_CASE`、`BUDDY_CODEX_FIXTURE_STATE`、`BUDDY_ZCODE_TEST_CASE`。`BUDDY_DEV_SOURCE`、`BUDDY_WORKER_STATE`、`BUDDY_WORKER_ID` 在转换前移除，不给旧代子进程继承。旧 `launch-settings.json` 的两个容量键在无 control 启动时读取，在回退时按旧名写回；新代持久化使用 HMB_。

新代验证、skill 发布和 active-runtime 切换完成以后，清理 `<agent-skills>/buddy` 与 `<claude-skills>/buddy`。旧目录必须是真实目录（排除符号链接和 reparse point），且非链接的 `skill.json` 是对象、`name` 为 `buddy`；旧 Claude 链接必须指向那个旧目录。无法确认归属的占位保留并报告 path/status；删除失败报告 error，不触发回退。清理时 verified 升级日志仍在，完成后才移除日志；移除日志失败在成功结果中报告 `journalError`，留下 verified 日志供恢复，不回退到已经移走 skill 的旧代。verified 恢复同样执行幂等清理。

固定 Worker 提示词的前后差异只包含三个工具名字：`buddy_finish_turn`、`buddy_checkpoint`、`buddy_answer_inquiry` 改为对应 `hey_my_buddy_` 名字；registry 的 Codex/Claude completion 前缀只替换 `buddy_finish_turn`。结果 schema 哈希不变。Codex 完整 prompt SHA-256 从 `bfe9d49b529fae37a598265f04979c7590161c3bfbb55bd823ffc154822a65b1` 变为 `c622f607ce8db225e6ae7d01ef3f4079406868ff17333b5342ed5c86668d7bf0`；Claude 从 `6216a2d312b1bb9cd887e15c6781e39ee176ad1d52f347411b653721a97ac7a1` 变为 `f508ab6757415e03a44a6f95138018d4eb7d2b2b4e9581d455500e9b383eaef9`。Lead 文字中的产品启动器和 help 命令另按产品名更新；没有改 Worker 的职责、字段或 schema。

新旧文件位置如下；应用 README 只同步命令和环境变量的拼写，其余是文件内容中的名称变更，不移动 buddy 工程模块。构建生成的新 JS 替换旧 JS，CSS 文件的名称和内容保持不变。

| 旧位置 | 新位置 |
| --- | --- |
| skills/buddy/SKILL.md | skills/hey-my-buddy/SKILL.md |
| skills/buddy/scripts/buddy | skills/hey-my-buddy/scripts/hey-my-buddy |
| skills/buddy/scripts/buddy.cmd | skills/hey-my-buddy/scripts/hey-my-buddy.cmd |
| skills/buddy/scripts/buddy.ps1 | skills/hey-my-buddy/scripts/hey-my-buddy.ps1 |
| console/assets/assets/index-BlojrtyL.js | console/assets/assets/index-D63rv1aD.js |

下表处数按未压缩源码和测试的实际差异计，取删除行中的旧拼写与添加行中的新拼写出现数的较小值；复合路径与前缀存在交叠，各行不求和。产品裸词项按词边界计算净减少数；不把新增旧代专用测试或压缩资产重复计作改名。标记“清单外”的是清单未单独列出的具体拼写，通用 buddy-/buddy_ 规则已经覆盖其命名方向。

| 旧名 | 新名 | 处数 | 依据 |
| --- | --- | ---: | --- |
| `buddy / Buddy（产品短名）` | `hey-my-buddy` | 303 | 清单 |
| `.agents/skills/buddy` | `.agents/skills/hey-my-buddy` | 3 | 清单 |
| `.buddy-build-` | `.hey-my-buddy-build-` | 1 | 清单 |
| `.buddy-install-` | `.hey-my-buddy-install-` | 2 | 清单 |
| `.buddy-install-previous-` | `.hey-my-buddy-install-previous-` | 1 | 清单 |
| `.buddy-integrated` | `.hey-my-buddy-integrated` | 1 | 清单外，同族 |
| `.buddy-source-` | `.hey-my-buddy-source-` | 1 | 清单 |
| `.buddy-upgrade-previous-` | `.hey-my-buddy-upgrade-previous-` | 3 | 清单 |
| `.buddy-upgrade-stage-` | `.hey-my-buddy-upgrade-stage-` | 4 | 清单 |
| `.claude/skills/buddy` | `.claude/skills/hey-my-buddy` | 4 | 清单 |
| `BUDDY_AGENT_CREDENTIAL` | `HMB_AGENT_CREDENTIAL` | 105 | 清单 |
| `BUDDY_AGENT_CREDENTIAL_FILE` | `HMB_AGENT_CREDENTIAL_FILE` | 49 | 清单 |
| `BUDDY_AGENT_SKILLS_DIR` | `HMB_AGENT_SKILLS_DIR` | 8 | 清单 |
| `BUDDY_ATTEMPT_ID` | `HMB_ATTEMPT_ID` | 6 | 清单 |
| `BUDDY_BOOTSTRAP_PYTHON` | `HMB_BOOTSTRAP_PYTHON` | 7 | 清单 |
| `BUDDY_CHECKS_JOBS` | `HMB_CHECKS_JOBS` | 6 | 清单 |
| `BUDDY_CHECKS_TMPDIR` | `HMB_CHECKS_TMPDIR` | 88 | 清单 |
| `BUDDY_CLAUDE_CLI` | `HMB_CLAUDE_CLI` | 29 | 清单 |
| `BUDDY_CLAUDE_FIXTURE_CASE` | `HMB_CLAUDE_FIXTURE_CASE` | 19 | 清单 |
| `BUDDY_CLAUDE_FIXTURE_STATE` | `HMB_CLAUDE_FIXTURE_STATE` | 12 | 清单 |
| `BUDDY_CLAUDE_SKILLS_DIR` | `HMB_CLAUDE_SKILLS_DIR` | 6 | 清单 |
| `BUDDY_CODEX_CLI` | `HMB_CODEX_CLI` | 30 | 清单 |
| `BUDDY_CODEX_FIXTURE_CASE` | `HMB_CODEX_FIXTURE_CASE` | 16 | 清单 |
| `BUDDY_CODEX_FIXTURE_STATE` | `HMB_CODEX_FIXTURE_STATE` | 48 | 清单 |
| `BUDDY_CONSOLE_PORT` | `HMB_CONSOLE_PORT` | 29 | 清单 |
| `BUDDY_DEBUG` | `HMB_DEBUG` | 2 | 清单 |
| `BUDDY_DEV_SOURCE` | `HMB_DEV_SOURCE` | 92 | 清单 |
| `BUDDY_DISTRIBUTION_NO_BUILD_ISOLATION` | `HMB_DISTRIBUTION_NO_BUILD_ISOLATION` | 1 | 清单 |
| `BUDDY_DSH_CLI` | `HMB_DSH_CLI` | 3 | 清单 |
| `BUDDY_HARNESS_RECORD_FILE` | `HMB_HARNESS_RECORD_FILE` | 10 | 清单 |
| `BUDDY_INQUIRY_OWNER_MATERIAL_ROOT` | `HMB_INQUIRY_OWNER_MATERIAL_ROOT` | 1 | 清单 |
| `BUDDY_LEASE_SECONDS` | `HMB_LEASE_SECONDS` | 3 | 清单 |
| `BUDDY_LIVE_TEST_MATERIALS` | `HMB_LIVE_TEST_MATERIALS` | 1 | 清单 |
| `BUDDY_LOG_DIR` | `HMB_LOG_DIR` | 1 | 清单 |
| `BUDDY_MAX_CONCURRENT` | `HMB_MAX_CONCURRENT` | 50 | 清单 |
| `BUDDY_MAX_DECISIONS` | `HMB_MAX_DECISIONS` | 2 | 清单 |
| `BUDDY_MODEL_CATALOG_FILE` | `HMB_MODEL_CATALOG_FILE` | 39 | 清单 |
| `BUDDY_MODEL_FACTS_FILE` | `HMB_MODEL_FACTS_FILE` | 17 | 清单 |
| `BUDDY_NODE` | `HMB_NODE` | 5 | 清单 |
| `BUDDY_NOT_A_REAL_SETTING` | `HMB_NOT_A_REAL_SETTING` | 2 | 清单 |
| `BUDDY_PREVIEW_FIXTURES` | `HMB_PREVIEW_FIXTURES` | 3 | 清单 |
| `BUDDY_PREVIEW_PYTHON` | `HMB_PREVIEW_PYTHON` | 1 | 清单 |
| `BUDDY_PREVIEW_REPORT` | `HMB_PREVIEW_REPORT` | 2 | 清单 |
| `BUDDY_PROJECT_DIR` | `HMB_PROJECT_DIR` | 7 | 清单外，同族 |
| `BUDDY_PYTHON` | `HMB_PYTHON` | 8 | 清单 |
| `BUDDY_R1_PROBE_STUB` | `HMB_R1_PROBE_STUB` | 1 | 清单外，同族 |
| `BUDDY_RUNTIME` | `HMB_RUNTIME` | 210 | 清单 |
| `BUDDY_RUNTIME_IDENTITY` | `HMB_RUNTIME_IDENTITY` | 30 | 清单 |
| `BUDDY_RUNTIME_ROOT` | `HMB_RUNTIME_ROOT` | 124 | 清单 |
| `BUDDY_SKILL_DIR` | `HMB_SKILL_DIR` | 8 | 清单外，同族 |
| `BUDDY_STATE_DIR` | `HMB_STATE_DIR` | 283 | 清单 |
| `BUDDY_SUPERVISOR_START_ID` | `HMB_SUPERVISOR_START_ID` | 11 | 清单 |
| `BUDDY_TASK_FILE` | `HMB_TASK_FILE` | 1 | 清单 |
| `BUDDY_TASK_ID` | `HMB_TASK_ID` | 6 | 清单 |
| `BUDDY_UNKNOWN_FUTURE_CREDENTIAL` | `HMB_UNKNOWN_FUTURE_CREDENTIAL` | 3 | 清单 |
| `BUDDY_UNKNOWN_PIN` | `HMB_UNKNOWN_PIN` | 2 | 清单 |
| `BUDDY_UNLISTED_TEST_PIN` | `HMB_UNLISTED_TEST_PIN` | 2 | 清单 |
| `BUDDY_WAIT_CAPACITY` | `HMB_WAIT_CAPACITY` | 18 | 清单 |
| `BUDDY_WORKER` | `HMB_WORKER` | 68 | 清单外，同族 |
| `BUDDY_WORKER_ID` | `HMB_WORKER_ID` | 47 | 清单 |
| `BUDDY_WORKER_STATE` | `HMB_WORKER_STATE` | 20 | 清单 |
| `BUDDY_ZCODE_CLI` | `HMB_ZCODE_CLI` | 15 | 清单 |
| `BUDDY_ZCODE_TEST_CASE` | `HMB_ZCODE_TEST_CASE` | 63 | 清单 |
| `Buddy snapshot` | `hey-my-buddy snapshot` | 2 | 清单外，同族 |
| `ConvertTo-BuddyNativeArgument` | `ConvertTo-HeyMyBuddyNativeArgument` | 2 | 清单外，同族 |
| `Invoke-BuddyProcess` | `Invoke-HeyMyBuddyProcess` | 3 | 清单外，同族 |
| `X-Buddy-CSRF` | `X-Hey-My-Buddy-CSRF` | 20 | 清单 |
| `X-Buddy-Preview` | `X-Hey-My-Buddy-Preview` | 3 | 清单外，同族 |
| `X-Buddy-Preview-Scenario` | `X-Hey-My-Buddy-Preview-Scenario` | 1 | 清单外，同族 |
| `_is_buddy_skill` | `_is_product_skill` | 5 | 清单外，同族 |
| `buddy-1` | `hey-my-buddy-1` | 3 | 清单外，同族 |
| `buddy-2` | `hey-my-buddy-2` | 1 | 清单外，同族 |
| `buddy-3` | `hey-my-buddy-3` | 1 | 清单外，同族 |
| `buddy-999` | `hey-my-buddy-999` | 2 | 清单外，同族 |
| `buddy-attach-` | `hey-my-buddy-attach-` | 2 | 清单外，同族 |
| `buddy-attach-fixture` | `hey-my-buddy-attach-fixture` | 1 | 清单外，同族 |
| `buddy-attempt` | `hey-my-buddy-attempt` | 8 | 清单 |
| `buddy-build-` | `hey-my-buddy-build-` | 2 | 清单外，同族 |
| `buddy-c2-readiness-` | `hey-my-buddy-c2-readiness-` | 1 | 清单外，同族 |
| `buddy-catalog-test-` | `hey-my-buddy-catalog-test-` | 1 | 清单外，同族 |
| `buddy-checks-` | `hey-my-buddy-checks-` | 7 | 清单外，同族 |
| `buddy-checks-link` | `hey-my-buddy-checks-link` | 1 | 清单外，同族 |
| `buddy-checks-test-` | `hey-my-buddy-checks-test-` | 2 | 清单外，同族 |
| `buddy-claude-config-` | `hey-my-buddy-claude-config-` | 1 | 清单外，同族 |
| `buddy-claude-empty-` | `hey-my-buddy-claude-empty-` | 1 | 清单外，同族 |
| `buddy-claude-evidence-` | `hey-my-buddy-claude-evidence-` | 1 | 清单外，同族 |
| `buddy-claude-native-usage-` | `hey-my-buddy-claude-native-usage-` | 1 | 清单外，同族 |
| `buddy-claude-seam-` | `hey-my-buddy-claude-seam-` | 1 | 清单外，同族 |
| `buddy-claude-test-` | `hey-my-buddy-claude-test-` | 1 | 清单外，同族 |
| `buddy-codex-catalog-` | `hey-my-buddy-codex-catalog-` | 11 | 清单外，同族 |
| `buddy-codex-discovery-boundary-` | `hey-my-buddy-codex-discovery-boundary-` | 1 | 清单外，同族 |
| `buddy-codex-facts-` | `hey-my-buddy-codex-facts-` | 1 | 清单外，同族 |
| `buddy-codex-seam-` | `hey-my-buddy-codex-seam-` | 1 | 清单外，同族 |
| `buddy-codex-test-` | `hey-my-buddy-codex-test-` | 1 | 清单外，同族 |
| `buddy-codex-tool-evidence-` | `hey-my-buddy-codex-tool-evidence-` | 1 | 清单外，同族 |
| `buddy-codex-usage-` | `hey-my-buddy-codex-usage-` | 1 | 清单外，同族 |
| `buddy-cold-` | `hey-my-buddy-cold-` | 1 | 清单外，同族 |
| `buddy-console` | `hey-my-buddy-console` | 5 | 清单外，同族 |
| `buddy-console-expiry` | `hey-my-buddy-console-expiry` | 1 | 清单外，同族 |
| `buddy-console-preview` | `hey-my-buddy-console-preview` | 1 | 清单外，同族 |
| `buddy-control` | `hey-my-buddy-control` | 4 | 清单 |
| `buddy-controller-launch-failure-` | `hey-my-buddy-controller-launch-failure-` | 1 | 清单外，同族 |
| `buddy-controller-no-harness-` | `hey-my-buddy-controller-no-harness-` | 1 | 清单外，同族 |
| `buddy-controller-signals-` | `hey-my-buddy-controller-signals-` | 1 | 清单外，同族 |
| `buddy-daemon-reaper` | `hey-my-buddy-daemon-reaper` | 1 | 清单外，同族 |
| `buddy-dist-` | `hey-my-buddy-dist-` | 1 | 清单外，同族 |
| `buddy-dsh-probe-` | `hey-my-buddy-dsh-probe-` | 1 | 清单外，同族 |
| `buddy-dsh-role-` | `hey-my-buddy-dsh-role-` | 1 | 清单外，同族 |
| `buddy-dsh-spec-` | `hey-my-buddy-dsh-spec-` | 1 | 清单外，同族 |
| `buddy-finish-turn` | `hey-my-buddy` | 1 | 清单 |
| `buddy-fixture-checkout` | `hey-my-buddy-fixture-checkout` | 1 | 清单外，同族 |
| `buddy-fixture-input` | `hey-my-buddy-fixture-input` | 1 | 清单外，同族 |
| `buddy-fixture.pth` | `hey-my-buddy-fixture.pth` | 1 | 清单外，同族 |
| `buddy-help-source-` | `hey-my-buddy-help-source-` | 1 | 清单外，同族 |
| `buddy-identity-repository-` | `hey-my-buddy-identity-repository-` | 1 | 清单外，同族 |
| `buddy-index-` | `hey-my-buddy-index-` | 1 | 清单外，同族 |
| `buddy-install-` | `hey-my-buddy-install-` | 3 | 清单外，同族 |
| `buddy-ipc-` | `hey-my-buddy-ipc-` | 2 | 清单外，同族 |
| `buddy-launch-` | `hey-my-buddy-launch-` | 1 | 清单外，同族 |
| `buddy-lease-sweeper` | `hey-my-buddy-lease-sweeper` | 1 | 清单外，同族 |
| `buddy-lifecycle-repository-` | `hey-my-buddy-lifecycle-repository-` | 1 | 清单外，同族 |
| `buddy-lightweight-1` | `hey-my-buddy-lightweight-1` | 1 | 清单 |
| `buddy-live-holder-` | `hey-my-buddy-live-holder-` | 1 | 清单外，同族 |
| `buddy-live-lifetime-` | `hey-my-buddy-live-lifetime-` | 1 | 清单外，同族 |
| `buddy-lock-` | `hey-my-buddy-lock-` | 1 | 清单外，同族 |
| `buddy-micro-task` | `hey-my-buddy-micro-task` | 16 | 清单外，同族 |
| `buddy-native-fixture` | `hey-my-buddy-native-fixture` | 2 | 清单外，同族 |
| `buddy-no-tool-` | `hey-my-buddy-no-tool-` | 3 | 清单外，同族 |
| `buddy-no-tool-codex-` | `hey-my-buddy-no-tool-codex-` | 1 | 清单外，同族 |
| `buddy-no-tool-dsh-` | `hey-my-buddy-no-tool-dsh-` | 1 | 清单外，同族 |
| `buddy-objects-` | `hey-my-buddy-objects-` | 1 | 清单外，同族 |
| `buddy-outcome-` | `hey-my-buddy-outcome-` | 1 | 清单外，同族 |
| `buddy-plane` | `hey-my-buddy-plane` | 1 | 清单外，同族 |
| `buddy-preparation-repository-` | `hey-my-buddy-preparation-repository-` | 1 | 清单外，同族 |
| `buddy-preview-` | `hey-my-buddy-preview-` | 3 | 清单外，同族 |
| `buddy-probe` | `hey-my-buddy-probe` | 3 | 清单外，同族 |
| `buddy-quota-redetect` | `hey-my-buddy-quota-redetect` | 2 | 清单外，同族 |
| `buddy-quota-run-` | `hey-my-buddy-quota-run-` | 1 | 清单外，同族 |
| `buddy-role-boundary-` | `hey-my-buddy-role-boundary-` | 1 | 清单外，同族 |
| `buddy-role-prompts-` | `hey-my-buddy-role-prompts-` | 1 | 清单外，同族 |
| `buddy-roles-model-check-` | `hey-my-buddy-roles-model-check-` | 1 | 清单外，同族 |
| `buddy-roles-router-` | `hey-my-buddy-roles-router-` | 1 | 清单外，同族 |
| `buddy-roles-seam-` | `hey-my-buddy-roles-seam-` | 1 | 清单外，同族 |
| `buddy-router` | `hey-my-buddy-router` | 24 | 清单 |
| `buddy-router-prompts-` | `hey-my-buddy-router-prompts-` | 1 | 清单外，同族 |
| `buddy-rpc-daemon-` | `hey-my-buddy-rpc-daemon-` | 1 | 清单外，同族 |
| `buddy-run-contract-` | `hey-my-buddy-run-contract-` | 1 | 清单外，同族 |
| `buddy-runtime-` | `hey-my-buddy-runtime-` | 4 | 清单外，同族 |
| `buddy-runtime-build-` | `hey-my-buddy-runtime-build-` | 1 | 清单外，同族 |
| `buddy-runtime-format` | `hey-my-buddy-runtime-format` | 1 | 清单外，同族 |
| `buddy-runtime-real-` | `hey-my-buddy-runtime-real-` | 1 | 清单外，同族 |
| `buddy-service-dev-` | `hey-my-buddy-service-dev-` | 1 | 清单外，同族 |
| `buddy-service-env-` | `hey-my-buddy-service-env-` | 1 | 清单外，同族 |
| `buddy-service-supervisor-` | `hey-my-buddy-service-supervisor-` | 1 | 清单外，同族 |
| `buddy-service-upgrade-` | `hey-my-buddy-service-upgrade-` | 1 | 清单外，同族 |
| `buddy-service-worker-` | `hey-my-buddy-service-worker-` | 1 | 清单外，同族 |
| `buddy-shared-` | `hey-my-buddy-shared-` | 5 | 清单外，同族 |
| `buddy-skill-build-` | `hey-my-buddy-skill-build-` | 1 | 清单外，同族 |
| `buddy-skill-content-` | `hey-my-buddy-skill-content-` | 1 | 清单外，同族 |
| `buddy-skill-current-` | `hey-my-buddy-skill-current-` | 1 | 清单外，同族 |
| `buddy-source-identity-` | `hey-my-buddy-source-identity-` | 2 | 清单外，同族 |
| `buddy-staged-worker-` | `hey-my-buddy-staged-worker-` | 1 | 清单外，同族 |
| `buddy-submission-repository-` | `hey-my-buddy-submission-repository-` | 1 | 清单外，同族 |
| `buddy-supervisor-reaper` | `hey-my-buddy-supervisor-reaper` | 1 | 清单外，同族 |
| `buddy-test` | `hey-my-buddy-test` | 13 | 清单外，同族 |
| `buddy-test-` | `hey-my-buddy-test-` | 10 | 清单外，同族 |
| `buddy-test-clean` | `hey-my-buddy-test-clean` | 1 | 清单外，同族 |
| `buddy-test-daemon` | `hey-my-buddy-test-daemon` | 1 | 清单外，同族 |
| `buddy-test-drain` | `hey-my-buddy-test-drain` | 1 | 清单外，同族 |
| `buddy-test-orphan` | `hey-my-buddy-test-orphan` | 1 | 清单外，同族 |
| `buddy-test-state` | `hey-my-buddy-test-state` | 1 | 清单外，同族 |
| `buddy-test-stuck` | `hey-my-buddy-test-stuck` | 1 | 清单外，同族 |
| `buddy-test-supervisor` | `hey-my-buddy-test-supervisor` | 1 | 清单外，同族 |
| `buddy-test-unlinked` | `hey-my-buddy-test-unlinked` | 1 | 清单外，同族 |
| `buddy-tests` | `hey-my-buddy-tests` | 2 | 清单外，同族 |
| `buddy-upgrade-env-` | `hey-my-buddy-upgrade-env-` | 1 | 清单外，同族 |
| `buddy-upgrade-read-` | `hey-my-buddy-upgrade-read-` | 1 | 清单外，同族 |
| `buddy-version-` | `hey-my-buddy-version-` | 1 | 清单外，同族 |
| `buddy-wait` | `hey-my-buddy-wait` | 1 | 清单 |
| `buddy-win-paths-` | `hey-my-buddy-win-paths-` | 1 | 清单外，同族 |
| `buddy-worker` | `hey-my-buddy-worker` | 10 | 清单外，同族 |
| `buddy-worker-endpoint-` | `hey-my-buddy-worker-endpoint-` | 1 | 清单外，同族 |
| `buddy-worker-env-` | `hey-my-buddy-worker-env-` | 1 | 清单外，同族 |
| `buddy-worker-live-` | `hey-my-buddy-worker-live-` | 1 | 清单外，同族 |
| `buddy-worker-marker-` | `hey-my-buddy-worker-marker-` | 1 | 清单外，同族 |
| `buddy-worker-prep-` | `hey-my-buddy-worker-prep-` | 1 | 清单外，同族 |
| `buddy-worker-renewal` | `hey-my-buddy-worker-renewal` | 1 | 清单外，同族 |
| `buddy-worker-services-` | `hey-my-buddy-worker-services-` | 1 | 清单外，同族 |
| `buddy-worker-source-` | `hey-my-buddy-worker-source-` | 1 | 清单外，同族 |
| `buddy-worker-stop-` | `hey-my-buddy-worker-stop-` | 1 | 清单外，同族 |
| `buddy-workspace-` | `hey-my-buddy-workspace-` | 4 | 清单外，同族 |
| `buddy-workspace-batch-` | `hey-my-buddy-workspace-batch-` | 1 | 清单外，同族 |
| `buddy-workspace-git-errors-` | `hey-my-buddy-workspace-git-errors-` | 1 | 清单外，同族 |
| `buddy-workspace-repository-` | `hey-my-buddy-workspace-repository-` | 1 | 清单外，同族 |
| `buddy-zcode-evidence-` | `hey-my-buddy-zcode-evidence-` | 1 | 清单外，同族 |
| `buddy-zcode-facts-` | `hey-my-buddy-zcode-facts-` | 1 | 清单外，同族 |
| `buddy-zcode-finish-` | `hey-my-buddy-zcode-finish-` | 1 | 清单外，同族 |
| `buddy-zcode-input-probe-` | `hey-my-buddy-zcode-input-probe-` | 1 | 清单外，同族 |
| `buddy-zcode-inquiry-` | `hey-my-buddy-zcode-inquiry-` | 1 | 清单外，同族 |
| `buddy-zcode-journal-` | `hey-my-buddy-zcode-journal-` | 1 | 清单外，同族 |
| `buddy-zcode-live-` | `hey-my-buddy-zcode-live-` | 11 | 清单外，同族 |
| `buddy-zcode-live-a-` | `hey-my-buddy-zcode-live-a-` | 1 | 清单外，同族 |
| `buddy-zcode-live-a2-` | `hey-my-buddy-zcode-live-a2-` | 1 | 清单外，同族 |
| `buddy-zcode-live-f-` | `hey-my-buddy-zcode-live-f-` | 1 | 清单外，同族 |
| `buddy-zcode-live-frame-` | `hey-my-buddy-zcode-live-frame-` | 1 | 清单外，同族 |
| `buddy-zcode-live-j-` | `hey-my-buddy-zcode-live-j-` | 1 | 清单外，同族 |
| `buddy-zcode-live-o-` | `hey-my-buddy-zcode-live-o-` | 1 | 清单外，同族 |
| `buddy-zcode-live-r-` | `hey-my-buddy-zcode-live-r-` | 1 | 清单外，同族 |
| `buddy-zcode-live-rej-` | `hey-my-buddy-zcode-live-rej-` | 1 | 清单外，同族 |
| `buddy-zcode-live-s-` | `hey-my-buddy-zcode-live-s-` | 1 | 清单外，同族 |
| `buddy-zcode-live-sup-` | `hey-my-buddy-zcode-live-sup-` | 1 | 清单外，同族 |
| `buddy-zcode-native-usage-` | `hey-my-buddy-zcode-native-usage-` | 1 | 清单外，同族 |
| `buddy-zcode-seam-` | `hey-my-buddy-zcode-seam-` | 2 | 清单外，同族 |
| `buddy-zcode-seam-w-` | `hey-my-buddy-zcode-seam-w-` | 1 | 清单外，同族 |
| `buddy-zcode-test-` | `hey-my-buddy-zcode-test-` | 1 | 清单外，同族 |
| `buddy.cmd` | `hey-my-buddy.cmd` | 3 | 清单 |
| `buddy.ps1` | `hey-my-buddy.ps1` | 4 | 清单 |
| `buddy/SKILL.md` | `hey-my-buddy/SKILL.md` | 4 | 清单 |
| `buddy/scripts/buddy` | `hey-my-buddy/scripts/hey-my-buddy` | 6 | 清单 |
| `buddy/skill.json` | `hey-my-buddy/skill.json` | 4 | 清单 |
| `buddy@localhost` | `hey-my-buddy@localhost` | 2 | 清单外，同族 |
| `buddy_answer_inquiry` | `hey_my_buddy_answer_inquiry` | 101 | 清单 |
| `buddy_build_skill` | `hey_my_buddy_build_skill` | 1 | 清单外，同族 |
| `buddy_checkpoint` | `hey_my_buddy_checkpoint` | 88 | 清单 |
| `buddy_console_session` | `hey_my_buddy_console_session` | 5 | 清单 |
| `buddy_finish_turn` | `hey_my_buddy_finish_turn` | 170 | 清单 |
| `buddy_inquiry_reply` | `hey_my_buddy_inquiry_reply` | 2 | 清单 |
| `buddy_probe_unknown` | `hey_my_buddy_probe_unknown` | 9 | 清单外，同族 |
| `buddy_snapshot_part` | `hey_my_buddy_snapshot_part` | 3 | 清单外，同族 |
| `buddy_snapshot_row` | `hey_my_buddy_snapshot_row` | 3 | 清单外，同族 |
| `buddy_something_else` | `hey_my_buddy_something_else` | 1 | 清单外，同族 |
| `hey_my_buddy/_distribution/scripts/buddy` | `hey_my_buddy/_distribution/scripts/hey-my-buddy` | 1 | 清单 |
| `permissions.buddy-router` | `permissions.hey-my-buddy-router` | 4 | 清单 |
| `permissions.buddy-router.filesystem` | `permissions.hey-my-buddy-router.filesystem` | 1 | 清单 |
| `permissions.buddy-router.network` | `permissions.hey-my-buddy-router.network` | 1 | 清单 |
| `refs/buddy` | `refs/hey-my-buddy` | 36 | 清单 |
| `refs/buddy/workspaces` | `refs/hey-my-buddy/workspaces` | 27 | 清单 |
| `scripts/buddy` | `scripts/hey-my-buddy` | 29 | 清单 |
| `scripts/buddy.cmd` | `scripts/hey-my-buddy.cmd` | 1 | 清单 |
| `scripts/buddy.ps1` | `scripts/hey-my-buddy.ps1` | 1 | 清单 |
| `skills/buddy` | `skills/hey-my-buddy` | 18 | 清单 |
| `skills/buddy/SKILL.md` | `skills/hey-my-buddy/SKILL.md` | 3 | 清单 |
| `skills/buddy/scripts` | `skills/hey-my-buddy/scripts` | 6 | 清单 |
| `skills/buddy/scripts/buddy` | `skills/hey-my-buddy/scripts/hey-my-buddy` | 4 | 清单 |
| `tool:buddy_answer_inquiry` | `tool:hey_my_buddy_answer_inquiry` | 27 | 清单 |
| `tool:buddy_checkpoint` | `tool:hey_my_buddy_checkpoint` | 5 | 清单 |
| `tool:buddy_inquiry_reply` | `tool:hey_my_buddy_inquiry_reply` | 2 | 清单 |
| `x-buddy-preview` | `x-hey-my-buddy-preview` | 7 | 清单外，同族 |

改后已在 src、tests/python、tests/probes、apps/console、packaging 中不分大小写搜 buddy，并另搜 BUDDY_。工程模块路径、类型/局部变量/列名和目录页面里的参与者名称全部保留；`buddy-mismatch`、`buddy-unavailable`、`buddy-catalog-unrecognized`、`buddy-selection-unrecognized`、`buddy-readback-unconfirmed` 描述参与者的 native tuple 或读回事实，继续使用原名。前端 `BUDDY_VIEWS`、`BUDDY_PAGE_SIZE`、`.buddy-page`、buddy 页面截图名也描述参与者视图。以下列出其余保留位置，行号对应本次最终源码。

| 保留位置 | 原因 |
| --- | --- |
| `apps/console/src/App.tsx:127` | 语义存在参与者/产品歧义，按 Host 指令保留；工具本身已改全名 |
| `apps/console/src/ui.tsx:20` | 语义存在参与者/产品歧义，按 Host 指令保留；工具本身已改全名 |
| `apps/console/src/use-editor.ts:17` | 语义存在参与者/产品歧义，按 Host 指令保留；工具本身已改全名 |
| `packaging/generate-upgrade-fixture.py:54` | 固定 0.34.1 样板生成器，Host 明定冻结 |
| `packaging/generate-upgrade-fixture.py:56` | 固定 0.34.1 样板生成器，Host 明定冻结 |
| `packaging/generate-upgrade-fixture.py:57` | 固定 0.34.1 样板生成器，Host 明定冻结 |
| `packaging/generate-upgrade-fixture.py:132` | 固定 0.34.1 样板生成器，Host 明定冻结 |
| `packaging/generate-upgrade-fixture.py:346` | 固定 0.34.1 样板生成器，Host 明定冻结 |
| `src/hey_my_buddy/buddy/harnesses/claude/config.py:48` | Worker 参与者措辞，保留并列入歧义核对 |
| `src/hey_my_buddy/buddy/harnesses/claude/native_run.py:363` | Worker 参与者措辞，保留并列入歧义核对 |
| `src/hey_my_buddy/buddy/harnesses/codex/native_run.py:126` | Worker 参与者措辞，保留并列入歧义核对 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:94` | 语义存在参与者/产品歧义，按 Host 指令保留；工具本身已改全名 |
| `src/hey_my_buddy/buddy/harnesses/registry.py:101` | 语义存在参与者/产品歧义，按 Host 指令保留；工具本身已改全名 |
| `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py:769` | 语义存在参与者/产品歧义，按 Host 指令保留；工具本身已改全名 |
| `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py:776` | 语义存在参与者/产品歧义，按 Host 指令保留；工具本身已改全名 |
| `src/hey_my_buddy/buddy/harnesses/zcode/protocol.py:782` | 语义存在参与者/产品歧义，按 Host 指令保留；工具本身已改全名 |
| `src/hey_my_buddy/buddy/roles/texts/worker.txt:5` | 语义存在参与者/产品歧义，按 Host 指令保留；工具本身已改全名 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:92` | 语义存在参与者/产品歧义，按 Host 指令保留；工具本身已改全名 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:133` | 语义存在参与者/产品歧义，按 Host 指令保留；工具本身已改全名 |
| `src/hey_my_buddy/buddy/roles/worker_services.py:134` | 语义存在参与者/产品歧义，按 Host 指令保留；工具本身已改全名 |
| `src/hey_my_buddy/install/upgrade.py:94` | 仅升级边界：与固定 0.34.1 旧代对话、持久化回退设置或核对旧 skill 归属 |
| `src/hey_my_buddy/install/upgrade.py:116` | 仅升级边界：与固定 0.34.1 旧代对话、持久化回退设置或核对旧 skill 归属 |
| `src/hey_my_buddy/install/upgrade.py:125` | 仅升级边界：与固定 0.34.1 旧代对话、持久化回退设置或核对旧 skill 归属 |
| `src/hey_my_buddy/install/upgrade.py:578` | 仅升级边界：与固定 0.34.1 旧代对话、持久化回退设置或核对旧 skill 归属 |
| `src/hey_my_buddy/install/upgrade.py:579` | 仅升级边界：与固定 0.34.1 旧代对话、持久化回退设置或核对旧 skill 归属 |
| `src/hey_my_buddy/install/upgrade.py:592` | 仅升级边界：与固定 0.34.1 旧代对话、持久化回退设置或核对旧 skill 归属 |
| `src/hey_my_buddy/protocol/rpc_config.py:5` | C-Two buddy 内存分配池的标准词，与产品名字无关 |
| `tests/python/blackboard/store/test_upgrade_fixture.py:65` | 归档样板引用，只检验证据，不使当前代码接受旧引用 |
| `tests/python/buddy/runtime/test_run.py:86` | 负向断言：已退休的旧命令/CLI 别名不得出现 |
| `tests/python/buddy/runtime/test_run.py:99` | 负向断言：已退休的旧命令/CLI 别名不得出现 |
| `tests/python/buddy/runtime/test_run.py:100` | 负向断言：已退休的旧命令/CLI 别名不得出现 |
| `tests/python/cli/test_blocking.py:158` | 负向断言：已退休的旧命令/CLI 别名不得出现 |
| `tests/python/cli/test_blocking.py:231` | 负向断言：已退休的旧命令/CLI 别名不得出现 |
| `tests/python/cli/test_blocking.py:248` | 负向断言：已退休的旧命令/CLI 别名不得出现 |
| `tests/python/cli/test_blocking.py:251` | 负向断言：已退休的旧命令/CLI 别名不得出现 |
| `tests/python/cli/test_blocking.py:511` | 负向断言：已退休的旧命令/CLI 别名不得出现 |
| `tests/python/cli/test_cli.py:209` | 负向断言：已退休的旧命令/CLI 别名不得出现 |
| `tests/python/cli/test_skill_workflow.py:74` | 断言冻结文档或 SKILL.md 正文；Host 后续文档微任务同步改 |
| `tests/python/install/test_distribution.py:71` | 负向断言：已退休的旧命令/CLI 别名不得出现 |
| `tests/python/install/test_first_install.py:174` | 断言冻结文档或 SKILL.md 正文；Host 后续文档微任务同步改 |
| `tests/python/install/test_first_install.py:183` | 断言冻结文档或 SKILL.md 正文；Host 后续文档微任务同步改 |
| `tests/python/install/test_launcher_selection.py:46` | 负向夹具：退休的 src/buddy 布局必须拒绝 |
| `tests/python/install/test_upgrade.py:23` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:24` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:25` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:26` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:27` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:28` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:30` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:37` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:48` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:51` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:52` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:64` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:66` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:68` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:83` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:86` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:95` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:109` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:110` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:112` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:165` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:212` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:214` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:215` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade.py:235` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade_0341_names.py:32` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade_0341_names.py:33` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade_0341_names.py:35` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade_0341_names.py:38` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade_0341_names.py:39` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade_0341_names.py:40` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade_0341_names.py:41` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/install/test_upgrade_0341_names.py:46` | 旧代边界的私有测试，证明新旧代隔离、旧 skill 归属、清理与回退 |
| `tests/python/protocol/test_rpc_config.py:473` | C-Two buddy 内存分配池的标准词，与产品名字无关 |

Python 检查全部使用 Host 指定的 clean.sh + 固定解释器 + `PYTHONPATH=src` + `python -m hey_my_buddy.cli.checks`，进程数使用默认值，未向该解释器环境写入依赖；检查运行器自行建立并清除系统临时目录中的私有状态、运行时和主目录。原始日志留在忽略的 `tmp/`。

完整检查第一轮：234 个文件中 228 个通过，6 个失败，退出 1，私有根已移除。`blackboard.store.test_blackboard` 在源码和构建资产尚有并行写入时，冷启动后重新计算内容标识未找到同名 READY；稳定源码单独复跑 58 项通过。`blackboard.store.test_upgrade_fixture` 的两个测试把旧 refs/buddy 归档交给当前新 namespace 契约，改为独立校验 Git 引用、tree、patch 和 snapshot 的哈希，并证明当前 host seal 拒绝旧记录，固定样板未变；单独修正后复跑通过。`buddy.harnesses.claude.test_claude_protocol`、`buddy.harnesses.claude.test_native_run` 的请求 ID 断言仍用 buddy-1 等，更新后分别复跑通过。`cli.test_blocking` 的一处恢复命令 argv 断言仍用 buddy，更新后复跑通过。`install.test_launcher_selection` 的模拟运行时缺 READY 元数据，补齐读回 mock 后单独复跑通过。这些失败都保留了首轮与复跑结果，不标为忽略。

局部检查过程：最早的 schema_worker/skill_workflow/packaging 检查有两个文件失败（固定 prompt 哈希需要更新、测试模块名误把工程 buddy.harnesses 改成产品名），均已修；skill_workflow 首轮通过。升级子任务首轮 7 个文件中有 2 个夹具/旧路径断言失败，修正后 test_upgrade + first_install 34 项通过，其余 5 个首轮通过。补回退容量与 READY 读取后，test_upgrade + service_environment 35 项复跑通过；再补链接归属和清理恢复故障检查后，test_upgrade 26 项通过。第一轮归档证据改写的测试用了错误的 repositoryPath 层级和错误码，第二次复跑已修并通过。

0.34.1 真实旧代码的私有安装演练通过（`install.test_upgrade_0341_names`，1 项，27.1 秒）：在私有根从固定提交 archive 出源包、写入该提交的 source metadata，经旧启动器安装和启动旧服务；停止旧服务使 control.json 缺失，再由新安装路径启动旧代、归档和创建新板、验证新服务、新 skill 和新链接、清理旧目录和旧链接。开始准备演练时两次夹具失败分别是没有安装的源树要求显式开发模式，以及 package 目录缺 active pointer；改为完整私有安装演练后通过。此演练无 native 模型调用，结束后停止私有服务，运行器删除私有根；不代表日常安装或 Windows 验收。

前端：Node 24.21.0；`npm --prefix apps/console ci` 通过；最终 `npm --prefix apps/console test` 78 文件、1091 项通过；最终 `npm --prefix apps/console run test:browser` 96 项通过。浏览器命令运行 synthetic preview，清除继承的 BUDDY_/HMB_ 和虚拟环境 pins，npm 缓存及 Chromium 下载目录都位于检出内 tmp；前端已有构建命令重建 console/assets，产物 JS 为 index-D63rv1aD.js，没有改布局、CSS 或页面结构。预览头和 quota-redetect 存储键补齐后重新跑了最终前端/浏览器检查。

最终完整检查结果：235 个文件、3888 项测试全部通过，skipped 0，退出 0，私有根已移除（`tmp/full-check-final.log`）。完整检查过程中最终词扫描补齐了 `buddy-upgrade-read-` 和 `buddy-fixture.pth`，对应 upgrade、skill_install、upgrade_host_cutover 的独立文件 subprocess 共 34 项在最终源码上通过；这两个拼写之外源码保持稳定。`git diff --check` 已通过；SKILL 正文和冻结文件逐字节比对通过。未验证原生 Windows、真实用户 harness 回合、日常安装和 Host 最终验收。

首轮失败 6 个文件的逐个单文件复跑全部通过：`blackboard.store.test_blackboard` 58 项、`blackboard.store.test_upgrade_fixture` 6 项、`buddy.harnesses.claude.test_claude_protocol` 28 项、`buddy.harnesses.claude.test_native_run` 74 项、`cli.test_blocking` 27 项、`install.test_launcher_selection` 5 项，共 198 项；每次均只传一个文件、默认进程数、退出 0、私有根移除。各文件首轮失败的原因见前文，单文件日志为 `tmp/solo-*.log`。
