# PyPI 的项目页：Host 验收

2026-10-25。微任务 `328c89ba`（发版准备，[执行计划](../../design/release/prep-plan.md)的 R3），封存产物 `939236a9`，输出 `a853abba`，合入整合分支 `a96a3a83`。没有安装。

## 交付了什么

- `pyproject.toml`：长描述由 hatch-fancy-pypi-readme 25.1.0 从 README 原文生成，构建时把相对的图片改写成仓库 `main` 的 `raw.githubusercontent.com` 地址，文件链接改写成 `blob/main`、目录链接改写成 `tree/main`，页内锚点不动；SPDX 的 `license = "MIT"` 与 `license-files`；六条分类（含开发者 2026-10-24 定的 `Development Status :: 4 - Beta`）；仓库与文档的地址。README 原文没有改。操作系统的分类与作者信息没有写，前者等 V 的结果，后者由开发者定。
- README 与 LICENSE 进了运行时的资源清单（`packaging/runtime-assets.json`）：用户安装时运行时要做一次 editable 构建，元数据要读这两个文件。原先单独复制 LICENSE 的一行随之删掉。
- 调研记录 [2026-10-25-pypi-project-page](../../research/2026-10-25-pypi-project-page.md)。
- 社交分享预览图的候选（1280×640 PNG）不进仓库，已交给开发者看；上传到 GitHub 的设置由开发者做。

## Host 自己核对的

- 读完差异。
- 从 `a853abba` 自己构建 sdist 与 wheel：`twine check` 两个都通过。wheel 的长描述去掉三个改写前缀之后与 README 逐字节相同。用 readme-renderer 46.0 渲染后共 35 处引用，没有相对地址；指向本仓库的 20 处都对应源码树里存在的文件。`<picture>` 保留，`<source>` 被去掉，所以 PyPI 上只显示浅色的横幅与架构图。
- 安装路径：在空 HOME 上、不设状态目录、经候选自己的启动器做一次首次安装。安装退出 0；服务报 0.53.0、schema 29、正常，运行时稳定；运行时目录里有 README 与 LICENSE；装出的元数据带 `License-Expression: MIT` 与改写后的长描述。停掉服务后没有进程留下，根已删除。
- 在候选上跑安装与打包相关的 11 个测试文件（`test_distribution`、`test_packaging`、`test_first_install`、`test_runtime`、`test_skill_install`、`test_publication_paths`、`test_source_identity`、`test_skill_workflow`、`test_console_version_info`、`test_publication_bytes`、`test_repository_hygiene`）：130 项，退出 0。

## 验证的边界

- 远端的 `main` 还没有 `docs/assets`。改写出的地址要等发版合入 `main` 之后才能打开；PyPI 的图片代理能否显示 SVG，要上传之后才能看。两者都在发版时核对。
- 运行时的构建多了一个构建依赖：用户安装时，要从 uv 缓存或网络拿到 hatch-fancy-pypi-readme，与已有的 hatchling 一样。
