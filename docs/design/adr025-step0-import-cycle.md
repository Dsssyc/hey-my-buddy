# ADR-025 第零步：包初始化循环的搬动表修订提案

状态：待用户确认。本提案不实施源码修改。第零步的实施 worktree 已收到停止并报告范围问题的问询，现有改动尚未验收；独立的 F-D2 核对可继续。

0-B1 以 `053b5c4` 为输入，按原表搬动后，首轮完整检查出现包初始化循环。旧 `src/buddy/adapters/__init__.py` 是有实际注册表内容的文件，会立即导入所有适配器；旧 `src/buddy/worker/__init__.py` 会立即导入 Supervisor 与 Worker。两者不能在新的分组中继续兼任自动执行的包入口，同时允许其他目录独立导入它们的子模块。

已观察到的循环包括 `buddy.roles.turn_io → buddy.harnesses.base → buddy.harnesses.__init__ → harness 协议 → buddy.roles.turn_io`。原表还把 `command.py` 放入 `buddy.runtime/`，产生 `harnesses 注册表 → runtime.command → runtime.__init__ → runtime.worker → harnesses 注册表` 的循环。原源码中 Worker 在模块顶层导入 `ExecutionContext`、`adapter` 与 `supported_capabilities`，而注册表在导入所有适配器之后才定义后两个函数。

0-B1 的进行中修改已经用 `TYPE_CHECKING` 和函数内导入尝试绕过这些循环。它们改变了导入执行时机，超出本步“只搬动、只改导入路径”的边界，不能按原计划验收。Host 已要求该微任务保留失败日志并停下，不通过兼容转发、测试 skip 或业务逻辑变化绕过。

建议仅修订两行搬动目的地，其余文件表不变；两个旧文件仍完整保留，通过 `git mv` 搬动，不拆分、不改变注册表或 Worker API 的函数体：

| 现有文件 | 原批准目的地 | 建议目的地 |
| --- | --- | --- |
| `src/buddy/adapters/__init__.py` | `src/hey_my_buddy/buddy/harnesses/__init__.py` | `src/hey_my_buddy/buddy/harnesses/registry.py` |
| `src/buddy/worker/__init__.py` | `src/hey_my_buddy/buddy/runtime/__init__.py` | `src/hey_my_buddy/buddy/runtime/api.py` |

新的 `harnesses/__init__.py` 与 `runtime/__init__.py` 按原计划的新包规则保持为空。原来显式使用适配器注册表的调用方改为导入 `harnesses.registry`，原来显式使用 Worker 包导出值的调用方改为导入 `runtime.api`；其他调用方直接按文件表导入具体子模块。这样两组目录的初始化不再隐式加载注册表或 Worker，对应整文件导入仍在原调用位置执行。不能保留旧的包属性转发或兼容 namespace。

确认后由同一个 0-B1 微任务通过 continue 完成：撤销本次新增的 `TYPE_CHECKING`、函数内导入等加载时机变更；按修订表完成两处 `git mv` 和调用方路径更新；重新运行此前失败的模块及完整检查；比较移除导入/路径改写后的函数体和受保护文件摘要。源码/资产搬动仍为 114 个原文件，测试/fixture 仍为 178 个原文件，另外两份空包入口作为新增文件登记。测试模块的改名表不受这两行源码映射修订影响。

本提案尚未验证建议布局的完整检查通过；它用于让用户审阅明确的范围调整，不把提案写成已修复。若仍出现需要改变行为或拆分旧文件的依赖问题，继续按用户给定的暂停规则处理。ADR、SKILL.md 与 Host 指南均不修改。
