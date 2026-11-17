# 发版流程固定字节：Host 验收

2026-10-27。微任务“发版流程固定字节”（run `c986d0ac`，经路由，Router 选了 Codex CLI 的 `gpt-6.1-sol / max`），基点 `8e6e2235`，封存提交 `59782567`，合入整合分支 `bba62f18`。起因是 0.54.1 发版时发现的缺口，开发者定为事故：发出去的包不由提交固定（[发版记录](../installs/0.54.1.md)）。

## 改了什么

- `.github/workflows/build-distribution.yml`：删掉 `actions/setup-node` 与用 npm 构建控制台的两步，直接打包提交里的资源，与源码安装共用一套。提交里的资源与新构建一致，由完整检查保证。
- `pyproject.toml`：构建依赖固定为 `hatchling==1.32.4`、`hatch-fancy-pypi-readme==25.1.0`。`portalocker` 删掉：实现方查明构建钩子在构建时不导入它，锁操作才按需导入；Host 在没有它的隔离构建环境里构建成功。

## Host 核对的

- 读过差异。
- 从封存提交本地构建两次，wheel 与 sdist 逐字节相同（`cbfda41f…`、`49e4e950…`）。wheel 里 28 个控制台资源与提交逐字节相同，`Generator: hatchling 1.32.4`，记录的源码提交是 `59782567`。
- 把封存提交推到 `ci/release-path`，用 `workflow_dispatch` 试跑只构建的发版流程（run `40455567263`，Ubuntu，不装 Node）：产物与本地构建逐字节相同。
- 空 HOME、空 uv 缓存上用候选 wheel 安装（`uvx --from <wheel> hey-my-buddy install`）：退出码 0，健康正常，运行时的构建拿到了固定版本的构建后端。
- 完整 CI（run `40455566728`）：三个 Python 作业因计时等待超时失败，都与这次改动无关，重跑后全部通过。它们是 macOS 的 `install.test_execution_install_fence` 与 `blackboard.service.test_execution_scheduler`、Windows 的 `buddy.harnesses.codex.test_codex`，交给“CI 偶发失败与分片”。

## 随之改的文档

- AGENTS.md 写明发版：标签打在装上的那个提交上（开发者 2026-10-27 定）；发版流程打包提交里的资源、构建后端固定；手动触发只构建不发布；发布之后与本地构建逐字节比对。
- 待办里的事故条目删掉，由这份记录注明。

## 没有核对的

- 真正推标签发布要等下一次发版；这次只试跑了只构建的流程。
