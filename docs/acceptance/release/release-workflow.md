# 发版流程：Host 验收

2026-10-26。微任务“发版流程：PyPI 与 ARM64”（run `3e37bc79`，经路由，Router 选了 Codex CLI 的 `gpt-6.1-sol / max`），基点 `d908b53b`，产物 `6de83e33`，合入整合分支 `7a7f26ad`。只改 `.github/workflows/` 下的两个文件。

- **发布：** 推送 `v*` 标签时，先核对标签等于 `v` 加 `pyproject.toml` 的版本，不等就失败；构建之后，发布任务取构建任务上传的那份产物、核对 SHA-256，再经 PyPI 的可信发布上传，发布的 action 固定在 `pypa/gh-action-pypi-publish` 的 v1.14.3（Host 在 GitHub 上核对过这个标签存在）。`workflow_dispatch` 只构建，不发布。
- **持续集成：** Python 矩阵加上 GitHub 给公开仓库的 ARM 版 Windows（`windows-11-arm`），分片沿用 x64 Windows 的用时；前端任务装上与其他任务同版本的 uv，因为预览测试在没设 `HMB_PREVIEW_PYTHON` 时用 `uv run --no-project python`（上一轮 CI 这个文件因此失败）。
- **核对：** 两个文件的差异读过；actionlint 1.7.12 退出 0。
- **开发者要在 PyPI 设置的：** 可信发布者（首次发布可在账户的 Publishing 页面建 pending publisher）：项目名 `hey-my-buddy`，GitHub owner `Dsssyc`，仓库 `hey-my-buddy`，工作流文件名 `build-distribution.yml`，environment 名 `pypi`。
- **验证边界：** ARM 版 Windows 与前端任务的 uv 要等下一次推送到 `ci/cross-platform` 才真正运行；发布任务只能在发版时、PyPI 一侧设好之后验证。
- **消耗：** 见看板上这个微任务的记录。
