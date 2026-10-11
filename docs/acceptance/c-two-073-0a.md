# C-Two 0.7.3 微任务 0-A：续接用量边界测试

基线为 `481ba22f`。本微任务只增加会话记录边界测试，没有修改生产代码或公共文件。临时夹具和两份变异源码副本均放在任务专用临时目录 `<TASK_TMP>`；由 Host 回收该目录。

## 新增测试

- `ResumeBoundaryTests.test_a_non_session_first_line_keeps_the_resume_usage_unknown`：真实 JSONL 第一行是带目标会话 ID 的 `user/message`，下一行才是会话头；调用真实 `freeze_session_records` 与 `resumed_session_record_facts`，断言冻结不可靠、边界未证明且 usage 为 `None`。
- `ResumeBoundaryTests.test_a_freeze_read_stopped_at_the_size_bound_keeps_usage_unknown`：真实普通 JSONL 含有效会话头及 66 行各约 1 MiB 的 padding，行长低于单行上限、文件总量超过总读取上限；调用真实冻结与续接投影，断言冻结不可靠、边界未证明且 usage 为 `None`。

## 验证记录

受限环境没有可用的项目依赖。为验证新增测试本身，使用系统 Python 和任务临时目录中的 `portalocker` 导入桩运行两个新增测试；被测代码路径不调用锁。执行命令为 `env -i PATH="$PATH" HOME=<TASK_TMP> TMPDIR=<TASK_TMP> BUDDY_CHECKS_TMPDIR=<TASK_TMP> BUDDY_STATE_DIR=<TASK_TMP>/state BUDDY_RUNTIME_ROOT=<TASK_TMP>/runtime PYTHONPATH=<TASK_TMP>/stubs:src python3 -m unittest tests.python.buddy.harnesses.dsh.test_session_records.ResumeBoundaryTests.test_a_non_session_first_line_keeps_the_resume_usage_unknown tests.python.buddy.harnesses.dsh.test_session_records.ResumeBoundaryTests.test_a_freeze_read_stopped_at_the_size_bound_keeps_usage_unknown`，退出码 0，运行 2 项并通过。

V-01 变异：在 `<TASK_TMP>/mutant-head/src/hey_my_buddy/buddy/harnesses/dsh/session_records.py` 临时副本中移除 `_matching_lines` 对首行必须为 session 头的拒绝判断；将该副本排在 `PYTHONPATH` 首位运行 `ResumeBoundaryTests.test_a_non_session_first_line_keeps_the_resume_usage_unknown`。命令为 `python3 -m unittest tests.python.buddy.harnesses.dsh.test_session_records.ResumeBoundaryTests.test_a_non_session_first_line_keeps_the_resume_usage_unknown`，退出码 1，实际失败断言为 `self.assertFalse(baseline.reliable)`，错误为 `AssertionError: True is not false`。

V-02 变异：在 `<TASK_TMP>/mutant-cap/src/hey_my_buddy/buddy/harnesses/dsh/session_records.py` 临时副本中把 `return tuple(digests), not stream.truncated` 改为 `return tuple(digests), True`；将该副本排在 `PYTHONPATH` 首位运行 `ResumeBoundaryTests.test_a_freeze_read_stopped_at_the_size_bound_keeps_usage_unknown`。命令为 `python3 -m unittest tests.python.buddy.harnesses.dsh.test_session_records.ResumeBoundaryTests.test_a_freeze_read_stopped_at_the_size_bound_keeps_usage_unknown`，退出码 1，实际失败断言为 `self.assertFalse(baseline.reliable)`，错误为 `AssertionError: True is not false`。

曾尝试运行整个 `tests.python.buddy.harnesses.dsh.test_session_records` 文件。`uv run --frozen` 首次因默认缓存目录不可访问而退出 2；将 UV 缓存改到 `<TASK_TMP>` 后，退出 1，原因是隔离网络无法解析并获取构建依赖 `portalocker`。系统 Python 也没有 `portalocker`；因此没有声称整个测试文件通过。未运行完整检查、模型调用或原生 DSH。
