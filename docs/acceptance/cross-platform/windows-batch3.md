# Windows 第三批：Host 验收

2026-10-26。依据[跨系统适配计划](../../design/cross-platform/plan.md)的“Windows 分批计划”第三批与开发者 2026-10-21 的五件事。微任务“Windows skill 联接与检出链接”（run `0073b10a`，Codex CLI 的 `gpt-6.1-sol / max`），基点 `0b52ef43`，产物 `e5ac31e1`，合入整合分支 `b65f022c`。W05（Git 在 `core.symlinks=false` 时检出的占位文件）等开发者定，不在本批；W09 挪到首次发版之后。

- **W04：** Windows 上 Claude Code 的 skill 链接改为 NTFS 目录联接（CPython 3.12 的 `_winapi.CreateJunction`），创建后核对指向唯一的那份 skill；认链接、核对、报告、回退的每一处都认得联接，删除只删联接本身。建不出来时报出原因；协助安装的 agent 怎样帮忙写进了 [operations](../../reference/operations.md)。
- **W06：** 检出里的链接类型由 Git 树证明（相对与仓库内的绝对目标、`..`、链接链、Windows 的大小写别名），悬空、指出仓库、依赖盘符当前目录、循环与穿过文件的目标明确拒绝并报出路径。实现方在测试机上另查出一处：目标里写 `/` 的链接能建成却读不出（NTFS 原样保存替代名），改为替代名用 Windows 分隔符、Git 原文留在显示名，封存的内容不变。
- **W08：** 运行时 `.pth` 的核对由目标解释器的 `sysconfig` 给出目录，相对行按 `.pth` 所在目录解析，没扫到不当作稳定的证据。
- **Host 的核对：** 产品差异读过；合并后受影响的 25 个文件 335 项在 macOS 上通过；空 HOME 经启动器从 0.53.1 装上合并后的源码，Claude 的链接照旧是符号链接。持续集成运行 `40226437259` 在 Windows x64 与 ARM64 上：`test_skill_install`、`test_first_install`、`test_runtime`、`test_upgrade`、`test_windows_workspace`、`test_workspace_boundaries`、`test_console_version_info`、`test_windows_files` 通过，失败文件的集合与上一轮相同，没有回退。
- **验证边界：** “不开开发者模式也能建联接”只有实现方在测试机上关掉建链特权核对过；CI 的机器有这个特权，留给最后在测试机上的独立复跑。
- **取证缺口：** 实现方最后一次清场时，一个 `TerminateProcess` 返回错误 5，报错前没有把那个对象的身份落盘；之后只读的扫描没有找到这个任务的进程，工作目录已删除。
