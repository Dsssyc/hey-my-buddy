# ADR-025 第三步原生冒烟

本记录覆盖实际已安装 harness 经新请求、运行模块、结果格式与共享 Worker 角色的最小检查；不替代模拟协议回归或整步检查。原始探针、请求/结果与摘要在 `tmp/adr025-host/step34-20261006-105245/`，凭据文件内容未读取，日常运行时未安装或升级。探针使用创建时登记的私有任务根，控制文件与业务材料均在其中。

## Codex

基线 `ef856ef`，codex-cli 0.160.0；openai / gpt-6-luna / low。实际 Worker 运行 1 次，28.973 秒；原生用量记录数为 2，不能把它说成一次供应方请求。任务只要求用一个原生工具读取私有 `smoke.txt` 并通过原生 outputSchema 返回最小完成值。

存储的 RunRequest、输出 RunResult 与外层持有身份完全相同，经过唯一 `hey_my_buddy.buddy.roles.run_controller`。共享角色签收 disposition=completed、summary=ADR025_CODEX_WORKER_OK，outputSchemaValidated=true、nativeTurnCompleted=true，原生 thread/session 关联一致；本 harness 的完成机制为 native-schema。原生 commandExecution 有成对 start/end，toolCalls=1、unsettledToolCalls=0、streamComplete=true。原生组 gone、leaderExited=true、exitCode=0，持有外层 handle 的停止确认与两层合成停止均为 true。

用量完整度 complete，inputTokens=31,243（includes-cached）、cachedInputTokens=15,104、outputTokens=106、reasoningOutputTokens=0，nativeRecords=2。Host 逐项核对并留存五份结果引用的实际文件、字节数与 SHA-256（quota、回合来源、checkpoint、私有 home 事实、原生 stderr）；随后只整体删除已登记的该冒烟根，未读取凭据文件内容。

## Claude Code

基线 `5a83d06`，调用共享无模型发现 1 次，使用私有 CLAUDE_CONFIG_DIR、保留 HOME、私有 BUDDY 状态与运行时根。发现返回 ADAPTER_UNAVAILABLE，原因是所选 CLI 要求原生登录；真实 Worker 运行 0 次，记为未验证。没有反复尝试，没有启动登录，没有改用户配置或凭据。此结论只描述本次私有检查环境，不能据此推断用户日常 Claude 会话已退出登录。

Codex 与 Claude 的模拟原生测试、守护变异和默认并行完整检查另见整合记录。所有真实模型运行次数以本记录为准；准备阶段另有已登记的 ZCode 1 次，不混算到第三步。
