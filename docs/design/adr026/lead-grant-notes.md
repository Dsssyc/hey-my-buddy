# 第 12 项：授予委派权并指定 Lead

Host 提交微任务可携带布尔字段 `delegationAuthority`，省略或 `false` 都表示没有委派权；`true` 要求显式提供完整的 `harness`、`provider`、`model`、`effort`，缺项、空值或空字符串会说明缺少哪一段。授予只接受 `executionWorkspace.access="full"`，同时沿用放开档位必须提供单行 `accessReason` 的规则；显式 buddy 经过现有权限能力与目录可用性校验，再在写事务中复核其已启用且可用。完整的 buddy 直接执行，不创建路由请求。协助任务只要携带 `delegationAuthority` 字段就被拒绝，包括值为 `false`；本阶段执行凭据仍不允许提交微任务，因此 Lead 也不能授予委派权。

授予属于 `workflow_runs` 的一行，新增 `delegation_authority INTEGER NOT NULL DEFAULT 0 CHECK (delegation_authority IN (0,1))`，担任的完整 buddy 沿用 `buddy_json`；授予事件与微任务写入在同一事务中，事件记录授予的 Host 与原始 buddy。`get`、CLI 简要投影和列表的 `lineage` 都返回布尔授予值及担任的 buddy；授予为 `true` 也进入提交指纹，重复请求不能改变授予。现有 schema 19 到 20 的升级重建 `workflow_runs`，把全部存量授予设为 `0`，沿用已有的微任务输入转换，schema 版本保持 20；基点样板的历史、原始回合输入及其哈希保持原有验证边界。

每个新执行回合把布尔授予写入冻结输入的 `context.delegationAuthority`，共享的 `worker_prompt` 据此在固定 Worker 角色文字后接已有 `roles.lead` 资源，然后放完成渠道、任务文字与输入；四个 harness 使用同一个组装点。没有委派权的微任务不接 Lead 文字。续接保留授予，拒绝降低权限档位或重新路由；允许 Host 用现有完整 buddy 换手流程指定新的担任者，并保留既有的启用、可用性与可选 buddy 锁定规则。

控制台只在现有微任务详情的担任者位置显示“带委派权”及完整 buddy，不增添入口或样式；打包资产随构建更新。

Python 验证只使用 `PYTHONPATH=src <Host 指定解释器> -m hey_my_buddy.cli.checks`，保持默认进程数，未运行完整检查。受影响的 `blackboard/store/`、`blackboard/tasks/`、`blackboard/routing/`、`buddy/roles/` 与相关 CLI、控制台、协议、服务、目录、评价、升级测试共 116 个文件；其中 115 个文件的 1,647 项测试通过，旧的列表字段断言补上 `delegationAuthority` 后，剩余文件单独复跑的 58 项测试通过。另 11 个受影响的 harness 与运行时测试文件的 363 项测试通过；最终覆盖 127 个 Python 文件、2,068 项测试，无跳过，每次检查的私有根都已由运行器清除。新增测试覆盖完整 buddy 的逐项缺失及空值、布尔类型、放开档位、协助任务拒绝授予、启用与可用性、授予的事务记录及读取、执行输入与四个 harness 的角色组装、幂等重放、执行凭据不能提交、续接换手与约束；基点升级测试证明存量授予均为 `0`，升级后结构与新建看板一致。

已先运行 `npm --prefix apps/console ci`，因此 Python 的 `tests/python/cli/test_host_preview.py` 使用真实前端解析器并通过。控制台最终源码通过 `npm --prefix apps/console test` 的 78 个文件、1,092 项测试，以及 `npm --prefix apps/console run test:browser` 的 96 项浏览器检查，最终验证把前端与浏览器并发分别限制为 2；类型检查与 Vite 构建成功，打包资产已更新。早先高并发运行出现的前端及浏览器超时，在限制并发后全部通过；没有修改项目的测试阈值或配置。最终 `git diff --check` 通过，独立只读源码审查未发现本次范围内的实质缺陷；这次审查没有执行测试，也不表示 Host 已验收。

与任务要求不一致或没有覆盖到的情形：没有。本阶段只记录授予并接入角色文字，Lead 名下的 Host 权限、微任务归属与生命周期、泳道仍由后续步骤实现；没有运行完整 Python 检查或真实 harness 探针，没有操作日常服务、向日常环境安装产品或进行模型调用，测试结果不表示已安装运行时具备实际委派能力。
