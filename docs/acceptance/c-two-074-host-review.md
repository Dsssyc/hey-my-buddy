# C-Two 0.7.4 接入：Host 复核

## 结论

2026-10-10 复核 `socu/c-two-073@8e793196`，整合基线为 `12eb4fcd`。本次退回返修，尚未合入、安装或回收实施检出。发现一个阻塞回归：未设置 `BUDDY_STATE_DIR` 的正常 Host CLI 和公开 `BoardClient` 用法失败。内部缺状态根即拒绝的规则应保留；公开入口必须先解析默认目录，再把同一目录传到服务探测和实际 RPC。

## 已核对的证据

实施方完整检查绑定 `e0ce3d2b`：默认并行数，213 个文件全部通过，执行 3,474 项，其中跳过 1 项，退出 0，690.917 秒。Host 核对原始 `final-short-check.log` 与结果，并确认该提交至候选的 `src`、`tests`、依赖、锁文件、打包、skill 与前端差集为空；后续提交是文档更新。因此未重复完整检查或前端检查。前三次失败仍保留在实施记录中。

Host 从候选提交另建源码副本，用已锁定的 0.7.4 解释器、空 HOME、私有状态和运行时目录分别执行下列测试。各文件独立进程，均退出 0，共 218 项；外层空 HOME 没有新增内容，检查运行器报告收尾成功。未运行新的付费模型回合。

| 文件 | 项数 |
| --- | ---: |
| `protocol.test_rpc_config` | 24 |
| `protocol.test_transport_attach` | 28 |
| `buddy.harnesses.test_inquiry_owner` | 15 |
| `console.test_console` | 20 |
| `cli.test_support_cleanup` | 8 |
| `buddy.harnesses.test_c_two_live` | 70 |
| `buddy.roles.test_role_live_seam` | 18 |
| `buddy.runtime.test_worker_live_wiring` | 5 |
| `protocol.test_ctwo_integration` | 2 |
| `buddy.harnesses.zcode.test_zcode_checkpoint` | 10 |
| `buddy.harnesses.dsh.test_session_records` | 18 |

Host 另用本项目最终 `rpc_config` 在两个自建进程之间运行真实连接探针：30 次短调用、闲置 62 秒后调用、再次闲置 62 秒后调用、20 次 3 MiB 往返。服务端 Unix 文件描述符基线为 4，各阶段均为 5；两次闲置后连接的内核身份变化而数量不增，客户端退出后回到 4。探针退出 0。这支持原 0.6.0 闲置驱逐泄漏在发布版 0.7.4 与本项目配置下没有复现，不外推为日常环境已经完成升级。

实施方已获逐次批准的 Codex、Claude Code、DSH 各一次真实冒烟，其结果文件均记录交付后签收、控制器退出与原生进程组停止；本次只核对已有结果，没有重跑。ZCode 按用户决定未调用真实模型，保留未验证。真实冒烟也显式指定私有状态根，不能覆盖下面的默认入口缺口。

## 阻塞问题：默认目录没有传入实际 RPC

`protocol/transport.py` 的 `call_service` 与 `call_board` 先经 `get_state_dir` 间接解析默认目录、探测服务，随后却把原参数 `state_dir=None` 传给 `_request`。新增的 `rpc_config.configure_client(state_dir, create=False)` 只接受显式参数或 `BUDDY_STATE_DIR`，因此抛出 `PRIVATE_STATE_REQUIRED`。`protocol/client.py` 的 `BoardClient(autostart=False)` 同样先用解析后的目录 attach，再把原 `self.state_dir=None` 传下去。源码启动器虽然计算过默认目录，也没有把该值交给 CLI 的实际调用。

Host 在独立空 HOME 的默认数据路径下启动真实私有服务，禁用自建服务的 Worker 池并用仓库的静态模型目录夹具，避免模型发现与委派。只读 health/ping 与该服务的停止均经过真实 C-Two，没有替换连接或响应：

| 调用 | 退出码与结果 |
| --- | --- |
| 显式 `BUDDY_STATE_DIR` 的 CLI `health` | 0，`status=ok` |
| 删除该变量后的同一 CLI `health` | 1，`PRIVATE_STATE_REQUIRED` |
| 删除该变量后的源码启动器 `health` | 1，`PRIVATE_STATE_REQUIRED` |
| 删除该变量后的 `BoardClient(autostart=False).ping()` | 1，`PRIVATE_STATE_REQUIRED` |
| 只在公开 CLI 调用边界把 `get_state_dir(state_dir)` 传入的对照 | 0，`status=ok` |
| 显式指定私有目录，停止持有的测试服务 | 0，服务进程退出 0 |

最后一个正向对照仅在探针进程内替换入口函数的参数传递，未改产品源码。它说明正常默认路径已有且可达，失败来自 Host 边界没有把解析结果向下传递。内部缺状态根的防护无需放宽。现有全检与真实冒烟都给了显式私有根，故没有检出这一普通使用路径。

原始材料保留在整合检出的忽略目录 `tmp/c074-acceptance/`：`binding.json`、`focused-results.json`、各模块日志、`idle-probe.log`、`idle-probe-result.json`、`default_cli_probe.py`、`default-cli-results.json` 与私有根台账。探针的汇总进程正常退出不代表其中所有被测命令成功；默认入口三项的实际退出码均为 1，见上表。

## 返修范围与复验

在公开 CLI 与公开 Python 客户端边界解析一次状态目录，并将同一个值传给服务探测、冷启动和最终请求；兼顾 `autostart=False`、已有服务的 `stop`/`restart` 分支、显式参数及环境覆盖，保留只读 attach 不创建、不 chmod 的约束。不得给内部 `_request`、`rpc_config`、Worker 或控制器增加默认目录回退，不要求用户新增配置，不改日常数据与服务。

补真实私有服务回归：空 HOME 下不设置 `BUDDY_STATE_DIR`，普通 CLI、启动器和公开 `BoardClient` 的默认路径可用；显式目录仍优先、禁止 autostart 的客户端仍不会冷启动。对新增测试做删除目录传递的故障注入，明确命中相应断言；保留所有既有测试编号和内部缺根拒绝测试。聚焦通过后，在最终代码上按本批规则执行一次完整检查，保留本次红灯；已有三次付费冒烟保留原提交绑定，不因记录变化重跑。

本次只记录退回结论；C-Two 实施源码未合入。`socu/buddy-core` 保持 `12eb4fcd`，避免另一条实施线追随纯记录变化。验收通过前不进行源码安装或日常服务切换。
