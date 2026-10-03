# ADR-025 第零步修正：资源覆盖邻居用例（用户授权第 2 项）

本记录只覆盖用户授权三处修正中的第 2 项，在固定基线 `32760edf00e46dec0f38141e678715ee4c8f338f` 的独立隔离 worktree 上完成；原实现微任务的既有验收记录不动、不取消，公共值、角色模块、注册表与最终整合归 Host，本回合不开始第零步的任何下一步。

## 修订内容

唯一生产/测试改动是 `tests/python/install/test_runtime.py` 中 `test_a_resource_must_be_covered_by_a_declared_asset` 的资源邻居一行：`src/buddy-neighbor/runner.py` 改为 `src/hey_my_buddy-neighbor/runner.py`；`git diff` 恰 1 insertion/1 deletion，测试名不变、断言未扩大、生产 `src/hey_my_buddy/install/runtime.py` 的 `_covered` 零改动（`git diff -- src` 为空），其余 tracked 树零改动。

修订理由：合成清单的已声明目录资产是 `src/hey_my_buddy`（`DEFAULT_ASSETS`），旧邻居 `src/buddy-neighbor/runner.py` 与它没有公共前缀，在正确实现与"去斜杠前缀"变异下都不被覆盖，该 subTest 因此从未行使目录前缀边界；新邻居恰以 `src/hey_my_buddy` 为字符串前缀却不在该目录内，只有带斜杠的目录前缀判断才会正确拒绝它，用例从此真正钉住边界行为。

## 变异验证（同一变异、真实运行）

变异定义为 `_covered` 目录覆盖判断从 `relative.startswith(asset.rstrip("/") + "/")` 改为 `relative.startswith(asset.rstrip("/") )`（不带斜杠的前缀），只施加在本次执行创建的私有源码副本 `<worktree>/tmp/adr025-step0-fix-resource-knsz7wvr/mutant/src/hey_my_buddy/install/runtime.py` 上（`cp -R` 自 canonical 后按唯一命中整行替换，`logs/mutant.diff` 确认与 canonical 恰一行之差）；canonical 源码、生产逻辑与其它人的 checkout 零改动。

运行一律用检查运行器同款子环境：每次运行经项目 `checks.create_private_root` 在本任务容器下新建私有根，再以 `checks.child_environment` 派生子进程（继承的 `BUDDY_STATE_DIR`/`BUDDY_RUNTIME_ROOT`/`BUDDY_RUNTIME`/`BUDDY_RUNTIME_IDENTITY`/`BUDDY_WORKER_STATE`/`BUDDY_WORKER_ID`/`BUDDY_AGENT_CREDENTIAL`/`BUDDY_AGENT_CREDENTIAL_FILE`/`VIRTUAL_ENV`/`UV_PROJECT_ENVIRONMENT` 等由该函数清除），另加容器内私有 `HOME`；命令形如 `uv run --frozen python <worktree>/tmp/adr025-step0-fix-resource-knsz7wvr/run_focused.py {single,single-mutant,show-loaded,module}`，原始日志与退出码文件全在 `<worktree>/tmp/adr025-step0-fix-resource-knsz7wvr/logs/`。

变异前先证明被测进程加载的确实是变异副本：同环境 `show-loaded` 打印 `runtime.__file__` 指向该私有副本、`inspect.getsource(_covered)` 为变异后文本（`logs/mutant-loaded.txt`，退出 0）。

按顺序三态闭环，全部针对同一测试 ID `install.test_runtime.DeclaredManifestTests.test_a_resource_must_be_covered_by_a_declared_asset`：正常通过（修订后对 canonical 运行，退出 0，`single-pass-1.log`）→ 变异失败（退出 1，`single-mutant.log`，唯一失败 subTest 为 `relative='src/hey_my_buddy-neighbor/runner.py'`，失败即 `AssertionError: BoardError not raised`——变异后该邻居被已声明目录 `src/hey_my_buddy` 的无斜杠前缀错误判为已覆盖，`load_manifest` 不再抛 `RUNTIME_MANIFEST_INVALID`，失败正好来自该邻居与已声明目录的前缀边界）→ 恢复通过（同一命令对 canonical 重跑，退出 0，`single-pass-2.log`）。

变异态下另两个 subTest（`undeclared/runner.py`、`pyproject.toml/runner.py`）仍然通过：前者对正确与变异实现都不覆盖，后者由 file 资产分支精确匹配、不经目录分支——失败没有其它来源冒充。

## 焦点模块与测试 ID 集合

聚焦模块 `install.test_runtime` 在修订前（原始树）整模块运行 32 用例全部通过、退出 0（`module-before.log`），修订后整模块再运行 32 用例全部通过、退出 0（`module-after.log`），两次均无失败、无错误、无新增 skip。

测试 ID 由真实 unittest loader 展平收集（`collect_ids.py`，非 AST 计数）：修订前后各 32 条叶子 ID，`diff` 为空（`ids-before.txt`/`ids-after.txt`/`ids-diff.txt`），本测试 ID 未变，无新增无删除。

本回合未运行完整检查、Console 或额外构建（整合后由 Host 统一运行一次完整检查）；本项验证未调用原生 harness/模型（不含路由与执行 buddy）；除上述焦点模块与指定故障注入外无其它测试执行。

## 清单与收尾

本回合未修改 ADR、`SKILL.md`、Host 指南、README（含中文版）、`docs/README.md`、`AGENTS.md` 与任何既有验收记录；未安装或升级日常运行时，未改用户配置、凭据与日常数据，未读取凭据文件内容；所有测试状态、运行时根、TMPDIR 与 HOME 均在任务容器私有根内。

精确 cleanup 只删除本次执行自己创建、且创建时已记录确切路径的对象，全部用逐项确切路径、无通配符，`rm -r` 不带 `-f`、不屏蔽报错：删除私有变异副本 `<worktree>/tmp/adr025-step0-fix-resource-knsz7wvr/mutant`（整目录，含唯一变异文件 runtime.py）；删除六次测试运行各自创建的六枚私有根 `<worktree>/tmp/adr025-step0-fix-resource-knsz7wvr/buddy-checks-cwp2e9mx`、`buddy-checks-nx4blwlp`、`buddy-checks-ok1hxh47`、`buddy-checks-p1b6hj2o`、`buddy-checks-q98ctylu`、`buddy-checks-u4fkk1_k`（确切路径清单在 `logs/private-roots-recorded.txt`，删除循环逐项防护"必须位于本任务容器下且名为 buddy-checks-*"）。7 项全部一次删除成功，无失败、无重试。

保留材料全部在 ignored 的 `<worktree>/tmp/adr025-step0-fix-resource-knsz7wvr/`（本检出 `tmp/` 目录在本回合前不存在、由本回合新建，故无其它会话遗留，也未触碰其它微任务或 0-B2 既有材料）：两份驱动脚本 `run_focused.py`、`collect_ids.py`，创建登记 `created-here.json`，`logs/` 下全部原始日志、退出码文件与变异 diff。另 `.venv/`（uv frozen 项目环境，gitignored）由 `uv run` 首次运行创建，保留供本 worktree 后续回合复用，未删除。
