# 检查运行器复用调用者 uv 缓存（2026-10-14）

本微任务基于提交 `2f36659b`，只修改 `src/hey_my_buddy/cli/checks.py` 的 `test_environment`：调用者已有 `UV_CACHE_DIR` 时原样保留；没有时，在替换主目录之前，以调用者环境执行 `uv cache dir`，将结果传给测试进程。缓存沿用 uv 已有的位置，本轮调用者的位置是 `~/.cache/uv`。没有新增缓存目录、开关或环境变量名称，也没有将 `UV_OFFLINE` 固定进运行器。

`child_environment` 继续提供私有主目录、harness 配置目录、XDG 目录、状态目录、运行时目录与临时目录。新增 `tests/python/cli/test_checks_uv_cache.py` 的三项检查，在真实子进程中读取环境，验证默认缓存的解析发生在替换主目录之前、缓存位于该测试的私有根之外、显式缓存值（含空格及相对路径）原样传递，以及其余目录仍在私有根内。其中一项实际调用 uv，核对调用者默认目录。

## 测试自行指定的缓存

修改前搜索了全仓库的 `UV_CACHE_DIR`、`UV_OFFLINE`、`--cache-dir`、`--no-cache`、`uv cache` 以及测试里的缓存断言，未发现依赖 uv 缓存为空的断言。控制台缓存、模型缓存、插件缓存与 `__pycache__` 的断言不属于 uv 缓存。本微任务未改现有测试断言、等待时间或并行度。

唯一自行设置 uv 缓存的测试是 `tests/python/install/test_distribution.py` 的 `test_wheel_and_sdist_are_complete_and_clean`：它将 `UV_CACHE_DIR` 覆盖为本次临时目录下的 `cache`，用于构建 wheel、sdist 与从 sdist 再构建 wheel。这个设置及其断言全部保留。`packaging/generate-upgrade-fixture.py` 也自行设置缓存，但它是生成工具，不是本轮检查的测试文件。

## 实际核对

Python 检查均从本检出根以 `PYTHONPATH=src <Host 指定解释器> -m hey_my_buddy.cli.checks` 执行，没有使用 `uv run`，没有写入该解释器环境。前端依赖先以 Node `24.21.0` 执行 `npm --prefix apps/console ci`，退出 0；npm 报告一个 high 级依赖漏洞，本微任务未调整依赖。缓存填充与完整检查清除临时调用环境中的 `UV_OFFLINE`，离线核对只在该次调用中设置 `UV_OFFLINE=1`；安装相关与完整检查清除 `BUDDY_CHECKS_JOBS`，均采用默认的 4 个进程。

安装相关两遍使用完全相同的选择：`tests/python/install`、`tests/python/blackboard/store/test_blackboard.py`、`tests/python/buddy/runtime/test_worker_runtime.py`，合计 20 个文件。用时是 `/usr/bin/time -p` 的 `real`，包含运行器准备与清理。

| 检查 | 用时 | 结果 |
| --- | ---: | --- |
| 最终 CLI 目录检查 | 18.23 秒 | 13 个文件、169 项通过，退出 0 |
| 安装相关文件，在线填充或复用缓存 | 84.41 秒 | 20 个文件、218 项通过，退出 0 |
| 相同安装相关文件，`UV_OFFLINE=1` | 97.31 秒 | 19 个文件通过；发行测试文件失败，退出 1 |
| 发行测试文件单独离线复跑 | 0.89 秒 | 相同构建错误，退出 1 |
| 不带参数的完整检查 | 775.44 秒 | 234 个文件、3,862 项通过，无跳过，退出 0 |

以上每次运行均报告私有根已移除。离线轮中运行器的汇总为 19 个成功文件、216 项；失败的发行文件另外执行两项，其中入口检查通过、构建检查失败。发行构建的 uv 输出是：解析 `build-system.requires` 中的 `hatchling` 与 `portalocker>=3, <4` 失败，明确指出新的缓存里没有 `hatchling`，网络已禁用。单独复跑确认同一原因；之后恢复在线的完整检查中发行文件通过。

首次 CLI 检查用时 20.61 秒，新增测试曾因 mock 保存可变环境字典的引用而失败：后续主目录替换改变了它观察到的值。仅修正新增测试，在 uv 调用发生时复制环境供断言使用；该文件单独复跑三项通过（0.82 秒），随后最终 CLI 目录与完整检查均通过。

## 尚未满足的边界

未自行覆盖缓存的 19 个选定文件已在填充缓存后离线通过；“相同 20 个文件全部离线通过”未满足。发行测试每次新建自己的临时缓存，因此调用者缓存填得再完整，也不会为它的新缓存提供构建依赖。这不涉及依赖空缓存的断言，但保留它的缓存准备方式与本次要求的全部离线通过之间仍需 Host 作出决定。若仍要求发行测试也离线通过，需要另行授权调整 `tests/python/install/test_distribution.py` 的缓存准备；该文件在本微任务写入范围之外。

本轮没有清空调用者缓存；在线一遍可能复用此前已有内容，不能作为空缓存基线。在线和离线轮受同机其他工作的影响，用时差不能用来声称提速。核对只发生在本机 macOS，未验证 Linux 或 Windows，也没有安装产品或改动日常服务与 Worker。结果等待 Host 审查与验收。
