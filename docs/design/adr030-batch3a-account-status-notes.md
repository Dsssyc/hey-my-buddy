# ADR-030 批次 3a：Claude Code 目录的账户事实修正

2026-10-12，基于 `5eb872a44bac83b0f675aaf0be78ff89d4dc6bbf` 的受管检出完成源码修正；版本 0.30.0、schema 17 均未变。本记录是 Worker 的修改与测试说明，Host 尚未验收，装好的运行时尚未包含本次修改。

## 根因与官方依据

Host 在真实 Claude Code 2.1.284 的 initialize-only 发现中读到 `account` 的字段为 `apiProvider`、`email`、`organization`、`subscriptionType`，没有 `tokenSource`。旧 `_catalog` 只接受非空且不等于 `none` 的字符串 `tokenSource`，使这一正常订阅形状每次都被上报为 `unknown`；默认假程序恰好提供 `tokenSource: "subscription"`，掩盖了缺陷。本回合未运行真实 CLI，真实形状的依据是 Host 给定的复现事实，夹具使用虚构账户值。

2026-10-12 查阅 [Claude Code 认证文档](https://code.claude.com/docs/en/authentication)：原生支持订阅、Console 与云供应方等认证方式，这份文档没有给出 initialize 的完整账户字段契约。[CLI 参考](https://code.claude.com/docs/en/cli-reference)介绍的是独立的 `claude auth status` 命令及其 `authMethod`，不能把那份答复的字段当成 initialize 的账户形状，也不能因此添加第二次登录状态读取。

同日查阅 [Agent SDK TypeScript 参考的 AccountInfo](https://code.claude.com/docs/en/agent-sdk/typescript#accountinfo)与 [SDKControlInitializeResponse](https://code.claude.com/docs/en/agent-sdk/typescript#sdkcontrolinitializeresponse)：initialize 结果中的 `account` 使用 `AccountInfo`，其 `email`、`organization`、`subscriptionType`、`tokenSource`、`apiKeySource` 五项均为可选字符串，没有任何一项必须存在的声明。网页读取工具因页面过大失败，随后读取同一官方地址的 `.md` 版本并核对这两节；未把网页读取失败当成字段定义缺失。

另外核对 Anthropic 发布的 [Agent SDK 0.3.220 类型声明](https://app.unpkg.com/@anthropic-ai/claude-agent-sdk@0.3.220/files/sdk.d.ts)：`AccountInfo` 还包含可选的 `apiProvider`，说明其表示活动 API 后端，第三方认证可以在外部完成。此处只用发布的接口定义核对字段，不读取或复制 Claude Code 的实现。供应方字段本身不证明原生给出了账户；仅有供应方的第三方对象与仅有供应方的第一方对象都上报 `unknown`，符合本微任务冻结的判据。

## 修改与边界

`native_run._catalog` 删除只检查 `tokenSource` 及其 `none` 值的判据，改为：`account` 是对象且 `apiProvider` 以外有任意非空值，就报告 `confirmed`；缺失、非对象、空对象、仅供应方或者其余字段都为空，报告 `unknown`。这是同一次 initialize 中是否给出了账户事实的报告；采信目录仍由黑板按 ADR-027 决定。

不使用字段白名单：官方文档未明确的未来账户字段同样按非空值处理，不按供应方或登录方式分支。非空使用 JSON 值在 Python 中的真值语义：`null`、空字符串、空对象、空数组、`false`、`0` 不构成非空事实；非空字符串、对象与数组等构成事实。`"none"` 是非空字符串，作为原生事实报告，不再由这一层解释成未登录；`confirmed` 不证明凭据有效、套餐可用或一次模型调用能够成功。没有添加字段内容校验、拒绝、登录状态回退或用户配置读取。

三个假 CLI 的默认账户都换成 Host 复现的订阅形状，删除默认的假 `tokenSource: "subscription"`。`fake_claude.py` 用具名用例覆盖 token 来源、密钥来源、空对象、无效对象、缺失、`null` 与仅供应方；旧的 `no-auth`、`missing-token` 用例及把正常订阅的空 token 当作未知账户的说明被替换。`null-token` 现在在有完整订阅事实时追加 `tokenSource: null`，用于确认空 token 不会遮蔽其他账户事实。

保留假 CLI 的独立 `auth status` 答复并增加调用计数，因为原生启动器检测已有用途；发现测试检查这项计数不存在，防止目录发现额外读取登录状态。曾尝试让这个夹具分支直接失败，发现会干扰启动器检测，已在最终改动中撤回；产品的发现与启动器检测均未增加路径。发现用例还核对一次 initialize、一次原生进程运行、零用户消息。

`test_native_run.py` 增加 `_catalog` 直接测试与 initialize-only 测试，覆盖真实订阅、可选字段、token 与密钥来源、未来字段、供应方独立性，以及缺失、无效或无账户事实。原有未知账户的执行测试改用仅供应方对象，继续验证这项未知事实不会阻止原生执行。`test_claude.py` 的发现断言与未知账户用例同步修正；`test_claude_worker.py` 的目录规则测试和 `test_catalog_observations.py` 的直接 `_catalog` 调用同步换成真实订阅形状，后者显式核对 `confirmed` 并继续检查重复目录读取保留已有 buddy 的启用、可用、偏好及创建版本。

其他 harness、`blackboard/catalog/` 源码、目录采信规则、skill、README、参考文档与前端均未修改。

## 本回合验证

新增直接账户测试先在旧判据下运行，3 项测试出现 25 个失败子用例，其中包括无 `tokenSource` 的真实订阅形状；修正后通过。最终源码的下列选择共 68 项测试通过，退出码 0，运行 10.803 秒；`git diff --check` 通过。所有测试使用既有只读解释器、当前检出的源码与假 CLI，没有模型调用。

从检出根目录运行，`BUDDY_VERIFY_PYTHON` 由 Host 指向任务指定的现成只读解释器；不安装依赖，不使用 `uv run`：

```sh
env -u BUDDY_STATE_DIR -u BUDDY_RUNTIME_ROOT \
  -u BUDDY_RUNTIME -u BUDDY_RUNTIME_IDENTITY \
  -u BUDDY_WORKER_STATE -u BUDDY_WORKER_ID \
  -u BUDDY_AGENT_CREDENTIAL -u BUDDY_AGENT_CREDENTIAL_FILE \
  -u VIRTUAL_ENV -u UV_PROJECT_ENVIRONMENT \
  BUDDY_DEV_SOURCE=1 PYTHONPATH=src:tests/python \
  "$BUDDY_VERIFY_PYTHON" -m unittest \
  buddy.harnesses.claude.test_native_run \
  buddy.harnesses.claude.test_claude.ClaudeAvailabilityTests \
  buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_discovery_catalog_uses_resolved_ids_without_a_user_turn \
  buddy.harnesses.claude.test_claude.ClaudeAdapterTests.test_discovery_records_unknown_login_without_a_proof_process \
  buddy.harnesses.claude.test_claude_worker.ClaudeCatalogRuleTests \
  blackboard.catalog.test_catalog_observations
```

一次初始组合运行遗漏检查器通常提供的全局 `BUDDY_DEV_SOURCE=1`，95 项产生 2 个失败、44 个错误，主要在准备阶段报告启动器不可用；这份失败结果不作为修正的验证。补齐该条件后，独立发现通过，但 `test_governed_turn_leaves_unknown_login_to_the_native_cli` 仍因控制器子进程未交出完整结果而失败：`runner.stdout.log` 报 `role-controller-failed`、`shutdownConfirmed: false`，`runner.stderr.log` 为空。控制器源码在这条路径启动 C-Two live endpoint；沙盒不支持该路径，本回合没有绕过限制或改控制器来使它通过，也没有从这个诊断推断原生进程已经停止。一次试图直接重放控制器的诊断碰到请求文件的独占写入保护，未得到更多启动错误细节，故不将底层 errno 说成已证实。

以下测试选择依赖控制器、C-Two 本机通信或真实启动器，由 Host 在允许这些能力的环境运行；本回合未声称这些文件全部通过：

- `buddy.harnesses.claude.test_claude.ClaudeAdapterTests`：整个类，只有上述两项独立发现已通过；特别包括本次修正的 `test_governed_turn_leaves_unknown_login_to_the_native_cli`。
- `buddy.harnesses.claude.test_claude_worker.ClaudeWorkerTests.test_governed_worker_seals_only_a_proven_claude_result`。
- `buddy.harnesses.claude.test_claude_worker.ClaudeWorkerTests.test_quota_failure_is_persisted_as_harness_error_without_a_capability_sample`。
- `buddy.harnesses.claude.test_claude_tool_evidence.RunnerReceiptTests`：整个类，覆盖本次同步修改默认账户的 `mock_claude.py` 经控制器交付回执的路径。

完整检查、故障注入、真实模型回合与安装验证依任务分工留给 Host；前端未受影响，无前端检查。

## Host 的真实 initialize-only 验证

Host 在最终检出根目录运行以下命令，使用当前 `PATH` 中的真实 Claude Code 与用户现有原生登录；这里只运行产品的 `run_discovery`，先读版本，再执行 initialize，随后确认停止，不发送用户消息，不调用模型，不启动黑板服务，不读写日常看板。源码路径断言排除误用安装后的旧包，临时目录只存发现过程的本次证据；此命令本回合没有执行。

```sh
env -u BUDDY_STATE_DIR -u BUDDY_RUNTIME_ROOT \
  -u BUDDY_RUNTIME -u BUDDY_RUNTIME_IDENTITY \
  -u BUDDY_WORKER_STATE -u BUDDY_WORKER_ID \
  -u BUDDY_AGENT_CREDENTIAL -u BUDDY_AGENT_CREDENTIAL_FILE \
  -u VIRTUAL_ENV -u UV_PROJECT_ENVIRONMENT \
  BUDDY_DEV_SOURCE=1 PYTHONPATH=src:tests/python \
  "$BUDDY_VERIFY_PYTHON" - <<'PY'
import json
import os
import shutil
import tempfile
from pathlib import Path
from hey_my_buddy.buddy.harnesses.claude import native_run

checkout = Path.cwd().resolve()
assert Path(native_run.__file__).resolve().is_relative_to(checkout / "src")
command = shutil.which("claude")
assert command is not None, "Host must provide the real Claude Code on PATH"
os.environ["BUDDY_CLAUDE_CLI"] = command
with tempfile.TemporaryDirectory(prefix="buddy-claude-account-probe-") as temporary:
    root = Path(temporary)
    catalog = native_run.run_discovery(
        cwd=str(checkout), invocation_root=root / "invocation",
        native_root=root / "native", timeout_seconds=30, cancelled=lambda: False)
    print(json.dumps({"harnessVersion": catalog["harnessVersion"],
                      "discoveries": catalog["discoveries"],
                      "modelCount": sum(len(provider["models"]) for provider in catalog["providers"])},
                     ensure_ascii=False))
    assert catalog["discoveries"] == [
        {"adapter": "claude", "status": "complete", "accountStatus": "confirmed"}]
PY
```

预期在 Host 已复现的订阅登录上得到 `accountStatus: confirmed` 且退出码 0。这能验证最终源码对真实账户形状的发现结果；日常 49 个 buddy 是否恢复还需 Host 安装并按自己的验收流程检查，不能由源码测试或此探针代替。
