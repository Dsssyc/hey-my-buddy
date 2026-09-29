# B 批第一阶段源码与验证记录

2026-09-30。基线 `socu/integration-0.22` / `62fd32b`（A 批已合入，A 批后续修正由 Host 合并）。实施分支 `socu/worker-accounts`，独立 worktree `/Users/soku/.codex/worktrees/worker-accounts/hey-my-buddy`。源码与契约候选 `0.23.0`，schema 保持 15；没有修改 `backup.py`、`upgrade.py`、`storage.py` 或既有适配器的 attempt 目录布局。第 11 条与第二阶段均未实施。

## 源码范围与逐项结果

| 第一阶段条目 | 源码与覆盖 | 验证状态 |
| --- | --- | --- |
| 1，控制台文案 | [227 项编号记录](console-copy-b.md)，全部按 Host 更严标准复核；短状态、可处理错误、破坏性确认保留，路由数据流向移入 `?`；同步断言。 | 子分支前端与浏览器通过，组合树完整检查及浏览器待最终记录。 |
| 2，取消发起者 | 从各 run 本次取消的持久事件取 actor/reason；续做后的下一次取消使用自己的事件，未知保持未知；控制台、CLI get/result 投影一致。 | 定点覆盖 Host/控制台/历史未知、重复取消、长 Host 标识、已完成结果及续做后再取消。 |
| 3，计费与耗尽 | 原生 metadata 的 subscription/metered/unknown 标注；与启用、偏好、候选排序分离。原生明确耗尽或适用零余额按 provider/limit 暂时过滤；重置或新可用观测才恢复，展示过期与未知不恢复，利用率及临时 rate limit 仅提醒；Host 明确选择保留并提醒。 | 原生协议夹具、持久记录、候选/Router/pin/显式选择、恢复与模型/服务商范围覆盖。 |
| 4，Codex 主动查询 | 只发 initialize、account/read（refreshToken:false）与 account/rateLimits/read；按需限频 180 秒，force 与改代不绕过；只保存脱敏 facts，所有持有进程结束后才采用观测。 | 模拟 CLI/限频与健康代际测试；真实账户读取未运行。 |
| 5，原生检查 | [N1–N9 方案](../design/adr019-native-checks.md) 覆盖四种私有目录、非 TTY 链接/设备码、取消、密钥通路和余额接口；每次单独授权，用户本人完成浏览器登录。 | N1 已向用户请求单次同意，尚未收到同意或执行；其他各项未请求执行、未运行。 |
| 6，审阅验证数据化 | 原有 0.157.0 证据转为数据证书；CLI harness-verify 与配置页显式授权，现有 Worker 队列/并发/回执负责执行；版本/平台/命令绑定、九项严格检查、同回合格式纠正、失败原因与未知停止均有数据。 | 模拟 native raw/command 关联、拒绝、伪证、截断、版本变化、预算、停止、重放与 CLI/HTTP/UI 覆盖；本轮不认证真实 0.159.0。 |

## 独立审查与修正

固定候选 `dd070a3` 的两项独立只读审查发现三处实际问题，均已修正并补回归：多 Codex 额度桶全部明确可用时，清除全局及覆盖的 limit 耗尽，未知桶仍保持；未确认验证请求在当前标签的 sessionStorage 只保存 requestId/profileId/revision 等非秘密字段，折叠或重载后核对同一请求，不生成第二次付费调用；失败界面按记录中的版本/路径、预算或停止原因给短处理办法，停止未确认会禁用再次执行。审阅证书正式身份仍为 harness/version/platform，命令与位置指纹仅用于本次启动和完成的代际核对，不扩大为新的契约身份。

## 完整检查与产物

待最终冻结提交、`npm --prefix apps/console ci`、`uv run --frozen python -m buddy.checks`、组合树浏览器与候选产物验证后填入真实结果，不以子代理完成消息代替验收。

## 原生检查与未验证项

本轮没有执行真实 harness 账户查询、登录、登出、密钥变更、付费模型权限探针或 DeepSeek 余额接口。既有 macOS Codex 0.157.0 证据仅来自此前接受的记录，未被本轮重新扩大；用户报告升级到 0.159.0，本轮未独立确认安装或原生事件格式。Windows/Linux 真机、账户隔离、系统凭据库、安全密钥写入及浏览器登录取消仍待逐次授权验证。

所有源码测试使用私有状态与空运行时根，清除继承 runtime/Worker/agent/venv 变量。没有读取或修改日常 `~/.local/share/hey-my-buddy`、安装或升级日常服务、修改用户配置、合并到 buddy-core 或发布。完成的 backlog “取消原因一律显示为用户取消”已删除；ZCode 快照与 Claude ANTHROPIC_API_KEY 条目保留到相应阶段。

## 后续边界

第一阶段验收完成后暂停。建议由 Host 在 A 批验收与修正完成后将本分支合入 `socu/buddy-core`；安装单独授权。第 11 条目前未并行实施，用户通知它合入后，先合入该前提再继续第二阶段，不能提前实现独立账户或替换用户凭据。
