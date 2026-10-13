# 开发者称谓疑义清单

以下位置保留原词，供 Host 复核。

1. 文件：`docs/acceptance/adr025-step-1.md`；行号：26；原句：71 项聚焦证据、用户默认 DSH 路径防护的红/恢复绿和改名/新增/删除表在[整改 Host 记录](adr025-dsh-acp-host-fixes-host.md)与[6 行变化表](adr025-dsh-acp-host-fixes-test-map.tsv)，不把该线写作第四步抽取或已获外部验收。；倾向：用户；原因：可能描述任何用户的默认路径防护，也可能特指开发者环境中的验证；保留原词。

2. 文件：`docs/acceptance/adr025-step-5.md`；行号：5；原句：清理只由Host按创建时记下的确切根进行；Worker不删除、不用stash、不创建/切换分支、不移动/删除分支标签；受管检出与用户仓库共用Git状态的边界已写进后续描述。；倾向：用户；原因：用户仓库可能是技术称呼，也可能特指此次开发者的具体仓库；保留原词。

3. 文件：`docs/acceptance/adr025-step-5.md`；行号：46；原句：DSH所有hey-my-buddy启动的运行关闭session-title-llm/session-telemetry-otel已接受差异延续，用户自己交互使用DSH不受影响。；倾向：用户；原因：像是在陈述任何使用者自行使用 DSH 的行为边界，也可能指此次开发者本人；保留原词。

4. 文件：`docs/acceptance/adr025-step5-integration.md`；行号：147；原句：今后微任务描述新增共享Git规则：受管检出与用户仓库共用stash/分支/标签，Worker禁止git stash，不移动或删除分支/标签；基线对比用新临时副本。；倾向：用户；原因：用户仓库可能是技术称呼，也可能特指此次开发者的具体仓库；保留原词。

5. 文件：`docs/acceptance/l3-adr021.md`；行号：272；原句：根因：测试私有根创建于 macOS 按用户的 `/var/folders/...`，而 Git 与部分解析返回 `/private/var/...`，同一目录在同一测试进程内出现两种路径身份，storage 的固定边界比较失配。；倾向：用户；原因：可能指操作系统按账户分配的目录，也可能特指开发者机器上的实验目录；保留原词。

6. 文件：`docs/research/2026-10-11-role-text-against-user-instructions.md`；行号：19；原句：| harness | 用户一侧的指令 | 角色说明放在哪里 | 回复用的语言 | 结论 |；倾向：用户；原因：表头像是在命名指令所属的产品角色，也可能是在指这次实验中开发者的实际指令；保留角色称呼。

7. 文件：`docs/research/2026-10-11-role-text-against-user-instructions.md`；行号：54；原句：放在提示词最前面这一种做法，在两个 harness 上都压过了用户一侧的指令，而且它不依赖任何一个 harness 的专门机制。；倾向：用户；原因：推断沿用表头的指令来源角色，也是在叙述开发者配置上的实际实验结果；倾向保留产品角色。

8. 文件：`docs/research/2026-10-13-provider-concurrency-limits.md`；行号：31；原句：现在没有使用者；等用户经套餐类的提供方跑多个 Worker、并且遇到按账户的限流时再定。；倾向：开发者；原因：像是在等待开发者的实际使用触发设计决定，但也可指任何产品用户遇到该场景；保留原词。

9. 文件：`docs/research/2026-10-13-provider-concurrency-limits.md`；行号：36；原句：用户开始经套餐类的提供方同时跑多个 Worker。；倾向：开发者；原因：沿用本次调查的日常使用背景，也可能是面向任何产品用户的调研失效条件；保留原词。
