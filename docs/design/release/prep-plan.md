# 发版准备里不用 Windows 的三项：执行计划

2026-10-25。依据[待办](../backlog.md)“顺序”第 5 步的“随它做”。开发者同日同意在等 Windows 测试机的这段时间先做。三项不改同一个文件。R1 与 R2 都要在本机跑重的检查，依次做；R3 轻，可以与 R1 并行。R1 与 R2 改到检查运行器与 E2a 动过的测试，建在 D2、E、F1 的最终输出 `f6221103` 上，随那一串一起验收合入；R3 建在整合分支上。

## R1 完整检查纳入前端检查

- 不带路径的完整检查在 Python 测试之外，跑一次前端的类型检查与单元测试，与持续集成的前端一项相同（`tsc --noEmit`、`vitest run`）。只给路径的聚焦运行仍只跑那些 Python 文件。
- 前端这一项是运行器线程池里的一个作业，排在 Python 文件之前；Vitest 只用一个 worker，让 `--jobs` 仍是整轮的并行数，`--jobs 1` 仍是一个接一个。它用与 Python 文件同一份私有子进程环境；找 Node 与前端依赖的办法与 `tests/python/cli/test_host_preview.py` 相同（`HMB_NODE` 或 PATH 上的 `node`，入口在 `apps/console/node_modules` 里）。
- 找不到 Node 或前端依赖时，这一项失败，说明先运行 `npm --prefix apps/console ci`，退出码照现在的规则为 1，不跳过。待办里 2026-10-08 写的“没有安装时明确报告跳过”作废：`test_host_preview` 已经要求这些依赖，跳过会多出一条出路。
- 浏览器检查仍是单独的命令：它要装 Chromium、要先构建，只在改到布局时才要求。
- 顺带：删掉 `SLOWEST_FILES_FIRST` 与按它排序的代码和测试。[2026-10-24 的量测](../../acceptance/check-runner/check-speed-2026-10-24.md)表明四个并行时墙钟约等于各文件用时之和除以 4，尾部空等十几秒；清单里还写着已删除的模块名。
- 检查：运行器自己的测试文件；最终源码上跑一次完整检查，因为运行器是所有测试共用的一层。
- 文档由 Host 改：AGENTS.md 的 Verification 一段与待办。

## R2 机器忙时 `test_parallel_dispatch` 的偶发超时

- 现象：`blackboard.tasks.test_parallel_dispatch` 的 `test_command_processes_use_free_slots_then_complete` 在 `--jobs 4` 的完整检查里等两个命令执行启动，60 秒到时失败，失败信息里的服务日志是空的；单独跑都通过，约 50 秒。2026-10-23 与 2026-10-25 各撞到一次。
- 先量：在负载下复现，给出从提交到两个执行写下启动标记之间每一段的用时（服务受理、调度准入、执行进程创建、身份发布、命令启动），并查明日志为什么是空的。计时只加在检出的 `tmp/` 或已有的日志里，不往产品加钩子。
- 再按原因改：产品里真有的等待（例如准入要等下一次扫描，或锁被慢的路径占着）在产品里修；只是机器忙时冷启动慢，测试的上限按量到的用时定，并写明它防的是什么。没有量清楚之前不放宽时限。
- 检查：在复现失败的同一种负载下重跑，给出次数与结果；改到产品时再跑受影响的文件。
- 只在 Windows 上见过的偶发失败（`tools.test_export_board_sample` 的快照竞争、Claude native-run 的问询用例、Codex fast-correction 的第二轮）等测试机开机，不在这一项里。

## R3 PyPI 的项目页

- Host 已核对：PyPI 渲染 README 用的 readme-renderer（46.0）保留 `<picture>` 与 `<img>`（`img` 只留 align、alt、class、height、src、width），去掉 `<source>`，所以 PyPI 上只剩浅色的横幅与架构图；相对路径的图片与链接在 PyPI 上解析不到。`main` 现在还没有 `docs/assets`，远端的实际显示要等发版合入 `main` 之后才能看。
- 做法：README 原文不改，构建时用成熟的插件（例如 hatch-fancy-pypi-readme）把相对的图片与链接改写成指向仓库 `main` 的绝对地址，页内锚点不改。`pyproject.toml` 加 `readme`、SPDX 形式的 `license` 与 `license-files`、分类（含开发者 2026-10-24 定的 `Development Status :: 4 - Beta`）与 `[project.urls]`。操作系统的分类等 V 的结果再定；作者信息由开发者定，先不写。
- 检查：构建 sdist 与 wheel，`twine check` 通过；从构建出的元数据渲染长描述，确认没有剩下相对的 `src`、`srcset`、`href`，每个改写出的地址都对应源码树里存在的文件。PyPI 的图片代理是否显示 SVG 只能上传之后看，发版时核对。
- 社交分享的预览图：用 `apps/console` 已有的 Playwright 把浅色横幅渲染成 1280×640 的 PNG 候选（横幅有渐显动画，取全部显示的一帧），交给开发者看，不进仓库；上传到 GitHub 的设置由开发者做。
- 调研记一条 `docs/research/` 笔记，照那里 README 的格式，不写别的项目的地址。
