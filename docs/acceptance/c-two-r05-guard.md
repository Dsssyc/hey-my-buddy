# R-05 记录拒绝诱饵返修：待 Host 原生验证

本微任务 2-E 的固定基线为 `27b91e0dfc732dfb88ff00f077c49d37154e688a`，唯一工作区产物为 `tests/python/console/test_console.py` 与本记录。旧 2-B 固定 `35373f49` 的签收与历史保留；Host 已在 `c-two-repair-host.md` 撤回“catalog-loss 真实变异命中拒绝诱饵”的旧结论。原失败发生在 `Browser.bootstrap` 的 `assert status == 200`，响应为 HTTP 500 / `INTERNAL_ERROR` / `console could not read snapshot`；未核对诱饵记录或 file source/content，因此不是有效 R-05 目标证据。该描述引用 Host 保留的失败，本轮未复跑旧失败，也未修改旧记录。

返修只改 `ConsoleDaemonTests.test_r05_private_daemon_reads_owned_catalog_without_native_discovery`：将最终诱饵记录核对放进包住真实 daemon/CLI/HTTP 路径的 `finally`。只要记录存在，即使先发生 CLI、bootstrap 或 HTTP 失败，最终断言也明确报告 `R-05: attempted native discovery/start; rejecting decoy calls:` 并附自建脚本的 JSON 行记录；没有记录时保留原异常，不把任意 HTTP 500 或环境失败改判为目标红灯。文件来源与第一次、改写后模型内容断言增加 `R-05: private fixture ...` 标签，仍核对真实返回值。

既有 `sitecustomize.py` 在 native `candidate_snapshot` 进入前替换入口，运行自建拒绝脚本记录 adapter 并拒绝继续；四个 `BUDDY_*_CLI` 变量均只指此脚本。正常路径仍使用已有 `BoardTestCase`、`Browser`、真实私有 daemon、CLI、C-Two 与 HTTP，验证 capabilities、私有 catalog 来源及修改后重读、无诱饵调用和空私有 HOME；没有新增产品模式、传输或模型调用。由于本轮 bind 被阻断，正常路径这些运行期断言尚未完成验证。

## 本轮实际证据

任务专用根为 `/tmp/r05-VEKKgO`，在本机 canonical 路径为 `/private/tmp/r05-VEKKgO`；这两者是同一目录。私有 HOME、TMPDIR、BUDDY_CHECKS_TMPDIR、uv 环境/cache、wheel 材料、固定基线比较副本、绿色副本、单点变异副本及原始日志均位于此根。启动 uv 和测试时清除继承的 `BUDDY_*`、`ANTHROPIC_*`、`C2_*`、`VIRTUAL_ENV`、`UV_PROJECT_ENVIRONMENT`，测试仅使用显式私有 HOME/state/runtime。未安装或改变日常 runtime。

公开已锁材料取自 `/tmp/c073-h-x6lhuldm/delegate-materials`，逐份核对 provenance SHA256 和 wheel metadata 后复制入任务根，离线使用 `uv venv --python /opt/homebrew/bin/python3.13 <task-root>/venv`、`uv pip install --python <task-root>/venv/bin/python --no-index --find-links <task-root>/materials --require-hashes -r <task-root>/materials/locked-dependencies.txt`，两步均退出 0。实际导入的发布版 C-Two 为 `0.7.4`；wheel SHA256 为 `bb625650f186c6066992cac0948c0f44b95a22af46778c8e63bba30d80ee88b8`。这是依赖安装/导入证据，不是原生 socket 往返证据。

| 命令或检查 | 项数 | 退出码与耗时 | 实际结果 |
| --- | --- | --- | --- |
| `<task-root>/venv/bin/python <task-root>/run-focused.py green-target` | 执行 R-05 1 项 | 1；unittest 0.450 秒，命令 4.698 秒 | daemon 启动失败，未进入 catalog 目标 |
| 私有解释器加载 `console.test_console`、编译最终测试源码并导入 C-Two | 加载 20，执行 0 | 0；0.448 秒 | loader 无错误，版本 0.7.4；不能算 20 项绿跑 |
| AST 与固定基线逐方法比较 | 20 个测试编号 | 0；未单独计时 | 删除集合为空，新增集合为空，原 19 项测试方法 AST 完全相同，仅新增 R-05 方法内容改变 |
| `git diff --check` | 不适用 | 0；未单独计时 | 无空白错误 |

本轮原始失败为 `AssertionError: daemon exited early: buddy: CONSOLE_PORT_IN_USE: Cannot bind 127.0.0.1:0; free the port or configure BUDDY_CONSOLE_PORT`。发生于 daemon context 进入阶段；记录未提供更底层 errno，不能声称已测到 EPERM 或端口实际占用原因。诱饵未执行，无目标记录。按授权停止受限 socket 尝试；没有继续运行 console 全文件或变异测试，也没有把此次失败算作目标证据。未运行完整检查、frontend 套件、support 测试或其它微任务文件；模型调用为零。

原始材料为 `<task-root>/run-green-target/raw.log`、`result.json`、`evidence/buddy-test-*/test-daemon.log`、`children-after-cleanup.json`；编号与范围证据为 `<task-root>/source-boundary.json`、`loader.log`、`loader-result.json`；依赖证据为 `wheel-verification.json`、`dependency-setup.log`。这里的星号仅表示已保留证据目录的命名模式，不用于删除。

## 交 Host 的聚焦与单点变异助手

`<task-root>/run-focused.py` 可重复执行，每次新建独立运行目录，并在测试框架正常收尾前保留自有 fixture 的 daemon 日志、诱饵 JSON 行记录和 child 停止状态。`green` 与 `mutant` 都由固定基线 `git archive` 的任务副本生成，仅覆盖最终 R-05 测试文件；绿色副本的 support SHA256 与基线同为 `e19593779da1c5dc47e3cb390887ff750959cd52b4dbb751e63c67f7b932015b`。没有修改工作区 support 或其它公共产物。

唯一故障注入仅在 `<task-root>/mutant/tests/python/support.py` 的 `BoardTestCase.daemon` 将 `environment = self.child_environment(env)` 改为 `environment = _child_environment(self.directory, env)`，CLI helper 保持不变，详见 `<task-root>/mutation.patch`。该副本尚未执行，不存在可报告的目标红灯或诱饵证据。

Host 在允许本地 bind 的执行环境中按以下三个命令补核，不需要真实 harness、账号或模型；助手使用任务根内已装私有环境与固定源码副本，不进入日常 state/ipc。

```sh
/tmp/r05-VEKKgO/venv/bin/python /tmp/r05-VEKKgO/run-focused.py green-target
/tmp/r05-VEKKgO/venv/bin/python /tmp/r05-VEKKgO/run-focused.py green-file
/tmp/r05-VEKKgO/venv/bin/python /tmp/r05-VEKKgO/run-focused.py mutant-target
```

验收条件：最终 R-05 单项与 console 全文件 20 项均真实退出 0；同一 R-05 在唯一变异副本退出 1，最终明确目标断言附自建诱饵记录，或明确 file source/content 目标断言失败。助手的 `result.json` 仅在退出 1 且目标 native 断言伴随可解析诱饵记录、或明确 fixture 断言时设置 `targetFailureEvidence:true`；任意 AssertionError、HTTP500、bind、导入或依赖失败不计为有效目标证据。Host 仍需核对完整原始日志、编号、项数及所有 child 停止证据，不能仅依赖布尔分类。补核应产生每条命令的 `result.json`、`raw.log`、fixture evidence（变异含 `native-cli-calls.jsonl`）并将实际结论整合回本记录；此前 R-05 保持未验证。

本轮自建 daemon PID `80219` 已退出，框架收尾后的 `returncode` 为 `2`，测试运行器也已退出；没有已知未停止自建进程。仅测试框架执行其正常 fixture 收尾，Worker 未手工删除任何文件或目录，未 stash、提交或改变分支/标签。任务根及助手、uv 环境、材料与副本全部保留，交 Host 验收后按上述确切根回收。
