# ADR-021 L18 升级交付

本文件及相邻 JSON 是原固定交付的历史记录；其中命令、提交、changedPaths/sourceHashes 和旧摘要保持原始历史地址与含义，不代表本轮执行或验收。

本轮基于 `cccf3714bd2631ea7b09a1f9343379dfc3492361`，原样接入 Host 已验收的 fixture 提交 `513e97da90477bc9e8dd12bda5efaa89b5db8924`（artifact `bb884da8-44f8-4b2f-80d7-c4f16be9558d`，integration `int-f287794d-a091-4cd5-8f7a-ad5ec7872efd`）。9 文件逐项与共享 Git 对象字节及 SHA-256 一致，未复制 generator/inventory 文档或重生成夹具。产品源码与本轮输入一致。

唯一还原入口为 `blackboard.store.upgrade_fixture.load_fixture`：先完成私有路径、基点旧身份与摘要绑定，仍保持 schema 15；之后才取保留基线和备份。旧 SQL-only loader 已移除。进程内公开测试调用真实 `migrate_workspace_identity`，验证升级前后历史保留，再读取 Router、unknown 目录和待结论 delivered，写真实 Host 结论并原请求重放。结论及事件各一条，独立 Host 封存文件与结论产物摘要绑定，原 Worker 产物保持不变。相同快照按既有身份复用原 output 行。

`install.test_upgrade_service` 的正式编号直接要求相同行为和 delivered executionStatus；旧负向方法及子类挂接点已移除。已列入退休清单的临时严格入口完整保留，等待 Host 机械删除。本轮没有启动服务或重试已知受限 socket。

唯一显式 15→16、独立迁移 DDL、Router 源 marker 1 边界、完整冻结正文、五类事实表、目录 unknown 投影、结构化失败原因及事务回滚保持前轮实现。备份验证与 upgrade 使用同一迁移；当前读者只读当前形式。旧终态 Router、原始观察、模型 available/enabled、旧指针及历史行/哈希保留，L14 原有身份别名边界保持。

本轮实际验证由 **Codex Worker** 执行，使用 uv 和 `child_environment` 清除继承绑定、设置私有状态/运行时/HOME/TMPDIR：

| 精确命令 | 结果 | 原始日志目录 |
| --- | --- | --- |
| `sh tmp/adr021-l18-upgrade/focused.sh --label accepted-fixture-green-18 blackboard.store.test_migrations install.test_upgrade_migration` | migrations 18 项通过；upgrade_migration 13/14 通过，新增封存行类型断言有误 | `tmp/adr021-l18-upgrade/accepted-fixture-green-18/` |
| `sh tmp/adr021-l18-upgrade/focused.sh --label accepted-fixture-green-19 install.test_upgrade_migration` | 按现有产物身份契约修正断言后 14 项通过，exit 0 | `tmp/adr021-l18-upgrade/accepted-fixture-green-19/` |

最终受影响的两个模块共 32 项通过；migrations 无后续变动，无重复运行。未运行完整 checks/discovery、无变化的 Router 或 fixture 封存模块。前轮 342/32/180 项及其精确命令、执行者、摘要和日志在三个固定输出提交的同名交付记录中保持可复核，临时证据未清理。

Host 提供的账户绑定 5 项、accepted fixture 封存 5 项及 `d8c55d94` + accepted fixture 的真实服务探针分别记为 **Codex 实现 Host** 执行；Worker 未读范围外日志或代跑。探针读写 delivered 成功且幂等，进程/锁空，原始引用详见 [交付 JSON](l18-upgrade-delivery.json)。最新固定产物的正式服务测试仍待 Host。

本轮显式编号新增 6（原样依赖 5、Worker 正向定义 1），撤换旧负向编号 1，正式服务编号保留并加强。相对原输入当前累计新增 47、原有内容移除 12、待整文件退休移除 9；撤换编号原为本微任务新增，不增加相对原基点的移除数。精确编号、前轮记录及本轮增量在 [测试编号 JSON](l18-test-changes.json)，继承展开和最终编号由 Host 退休后收集。

[退休清单](l18-retirements.json) 的 10 个确切路径、逐文件 SHA-256、理由及引用检查均保持不变；清单 SHA-256 为 `4f3f55e1d1e9da0000a4e5cd959482de318dc7985f75cc06047f3564b074027f`。Worker 未删除任何仓库文件或目录，原有临时严格入口也保持完整。固定产物及当前源摘要见 [交付 JSON](l18-upgrade-delivery.json)。脚本、日志和审计全部保留在 `tmp/adr021-l18-upgrade/`，本轮审计为 `accepted-fixture-import.json` 和 `accepted-fixture-delivery-audit.json`。

Codex 实现 Host 在最新固定源码及允许私有端口的环境运行 `sh tmp/adr021-l18-upgrade/focused.sh --label host-final-delivered-service-20 install.test_upgrade_service`。入口使用已经准备的私有 uv venv/cache；固定 archive 复跑时复制忽略目录中的 `focused.sh`、`run_focused.py` 并指向 Host 的私有环境。测试自动创建独立状态/运行时，原始 daemon 日志保留在对应批次私有 `tmp/adr021-l18-service.log`；无需安装产品或更改日常服务。

当前停到 Host 验收边界：实现 Host 负责受影响验收、正式服务复跑、机械退休和自己的验收记录；完整检查、逐处变异、空 HOME 旧包升级演练及最终 operations/architecture 文档由合入后的整合 Host 负责。
