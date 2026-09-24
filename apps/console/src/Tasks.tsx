import { useEffect, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import type { Snapshot, Task } from "./types";
import { Badge, display, Empty, formatDate, Icon, Status } from "./ui";
import { effortText } from "./profile-display";
import { canRetry, excerpt, needsReview, resultText, taskStatus } from "./task-state";
import { WorkflowPanel } from "./WorkflowPanel";

export function Tasks({
  snapshot,
  api,
  refresh,
}: {
  snapshot: Snapshot;
  api: ConsoleApi;
  refresh: () => Promise<Snapshot | null>;
}) {
  const [filter, setFilter] = useState("all"),
    [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<unknown>(null),
    [detailError, setDetailError] = useState("");
  const [remoteTask, setRemoteTask] = useState<Task | null>(null);
  const [note, setNote] = useState(""),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("");
  const tasks = snapshot.tasks.runs;
  const task = tasks.find((t) => t.runId === selected) || (remoteTask?.runId === selected ? remoteTask : undefined);
  useEffect(() => {
    if (!selected || task?.workflow) return;
    let active = true;
    setDetail(null);
    setDetailError("");
    api
      .task(selected)
      .then((value) => {
        if (active) {
          setDetail(value);
          if (value && typeof value === "object" && "runId" in value && value.runId === selected && "task" in value && typeof value.task === "string") setRemoteTask(value as Task);
        }
      })
      .catch((error) => {
        if (active) setDetailError(errorText(error));
      });
    return () => {
      active = false;
    };
  }, [api, selected, task?.revision, !!task?.workflow]);
  const visible = tasks.filter(
    (t) =>
      (filter === "all" ||
        (filter === "active"
          ? ["queued", "running", "cancelling", "waiting-host", "waiting-assistance"].includes(t.status) || t.workflow?.awaitingHost
          : filter === "host" ? t.workflow?.awaitingHost === true : needsReview(t))) &&
      `${t.task} ${t.cwd} ${t.runId}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  async function command(operation: string, params: Record<string, unknown>) {
    setBusy(true);
    setNotice("");
    setDetailError("");
    try {
      await api.command(operation, params, snapshot.csrfToken);
      setNotice("操作已记录。");
      await refresh();
    } catch (error) {
      setDetailError(errorText(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="workspace-grid">
      <section className="panel task-panel" aria-label="任务列表">
        <div className="panel-toolbar">
          <div className="segmented" aria-label="任务筛选">
            {[
              ["all", "全部"],
              ["active", "进行中"],
              ["host", "待决定"],
              ["review", "待验收"],
            ].map(([key, label]) => (
              <button
                key={key}
                aria-pressed={filter === key}
                onClick={() => setFilter(key)}
              >
                {label}
              </button>
            ))}
          </div>
          <span className="muted small">
            {tasks.length} / {snapshot.tasks.total} 个任务
          </span>
        </div>
        <label className="search">
          <span className="sr-only">搜索任务</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索任务、工作区或 ID"
          />
        </label>
        {visible.length ? (
          <ul className="task-list">
            {visible.map((t) => (
              <li key={t.runId}>
                <button
                  className={`task-row ${selected === t.runId ? "selected" : ""}`}
                  onClick={() => {
                    setSelected(t.runId);
                    setNote("");
                    setNotice("");
                  }}
                  aria-pressed={selected === t.runId}
                >
                  <div className="row-between">
                    <Status status={taskStatus(t)} />
                    <time className="muted small" dateTime={t.createdAt}>
                      {formatDate(t.createdAt)}
                    </time>
                  </div>
                  <h2>{excerpt(t.task?.trim().split("\n", 1)[0] || "未命名任务")}</h2>
                  <div className="row-between task-meta">
                    <span className="truncate" title={t.cwd}>
                      {t.cwd.split("/").filter(Boolean).slice(-2).join("/") ||
                        "工作区未记录"}
                    </span>
                    <span>
                      {t.acceptanceVerdict === "accepted"
                        ? "已验收"
                        : t.acceptanceVerdict === "rejected"
                          ? "验收未通过"
                          : "尚未验收"}
                    </span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <Empty title={tasks.length ? "没有匹配的任务" : "等待第一项委派"}>
            由 Host 发起任务后，这里会显示真实执行状态、工作区和验收记录。
          </Empty>
        )}
      </section>
      <aside className="panel detail-panel" aria-label="任务详情">
        {task ? (
          <>
            <div className="panel-heading">
              <span className="eyebrow">TASK DETAIL</span>
              <h2>任务详情</h2>
              <Status status={taskStatus(task)} />
            </div>
            <p className="task-description">{excerpt(task.task, 240)}</p>
            {task.task.length > 240 && <details className="detail-section"><summary>查看原始任务全文</summary><p className="task-description">{task.task}</p></details>}
            <dl className="facts">
              <dt>任务 ID</dt>
              <dd className="mono">{task.runId}</dd>
              <dt>负责人</dt>
              <dd>{task.workflow?.hostId || task.owner}</dd>
              <dt>原始模型约束</dt>
              <dd>
                {task.spec?.model ? display(task.spec.model) : "自动路由"}{" "}
                <span className="muted">
                  /{" "}
                  {typeof task.spec?.effort === "string"
                    ? effortText(task.spec.effort)
                    : display(task.spec?.effort)}
                </span>
              </dd>
              <dt>工作区</dt>
              <dd className="mono wrap">{task.cwd}</dd>
              <dt>等待原因</dt>
              <dd>{task.queueReason || "无"}</dd>
              <dt>验收</dt>
              <dd>
                {task.acceptanceVerdict === "accepted"
                  ? "已接受"
                  : task.acceptanceVerdict === "rejected"
                    ? "已拒绝"
                    : "未验收"}
              </dd>
            </dl>
            {task.workflow ? <WorkflowPanel key={task.runId} task={task} snapshot={snapshot} api={api} refresh={refresh} selectTask={setSelected} /> : <>
            <section className="detail-section">
              <h3>交付结果</h3>
              {detail ? (
                <pre className="result-text">{resultText(detail)}</pre>
              ) : (
                <p className="muted">正在读取结果…</p>
              )}
            </section>
            {detailError && (
              <p className="error-message" role="alert">
                {detailError}
              </p>
            )}
            {notice && (
              <p className="success-message" role="status">
                {notice}
              </p>
            )}
            <div className="actions">
              {["queued", "running", "cancelling"].includes(task.status) && (
                <button
                  className="button danger"
                  disabled={busy || task.status === "cancelling"}
                  onClick={() =>
                    void command("task_cancel", { runId: task.runId })
                  }
                >
                  取消任务
                </button>
              )}
              {task.spec?.adapter !== "decision" && ["failed", "cancelled"].includes(task.status) && (
                <button
                  className="button"
                  disabled={busy || !canRetry(task)}
                  onClick={() =>
                    void command("task_retry", { runId: task.runId })
                  }
                >
                  重新尝试
                </button>
              )}
            </div>
            {task.spec?.adapter === "decision" && <p className="small muted">这是一次决策计算。结论与依据请在“决策配置”查看，需要再尝试时明确提交新请求。</p>}
            {needsReview(task) && (
                <section className="detail-section">
                  <h3>记录验收</h3>
                  <label className="field">
                    <span>检查依据</span>
                    <textarea
                      rows={3}
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      placeholder="记录实际检查过的产物、测试或问题"
                      maxLength={2000}
                    />
                  </label>
                  <div className="actions">
                    <button
                      className="button primary"
                      disabled={
                        busy || !note.trim() || task.shutdownConfirmed !== true
                      }
                      onClick={() =>
                        void command("task_acknowledge", {
                          runId: task.runId,
                          verdict: "accepted",
                          note: note.trim(),
                        })
                      }
                    >
                      接受交付
                    </button>
                    <button
                      className="button"
                      disabled={
                        busy || !note.trim() || task.shutdownConfirmed !== true
                      }
                      onClick={() =>
                        void command("task_acknowledge", {
                          runId: task.runId,
                          verdict: "rejected",
                          note: note.trim(),
                        })
                      }
                    >
                      记录问题
                    </button>
                  </div>
                </section>
              )}
            </>}
          </>
        ) : (
          <div className="detail-placeholder">
            <Icon name="tasks" size={28} />
            <h2>每项工作都有据可查</h2>
            <p role={detailError ? "alert" : "status"}>{selected ? detailError || "正在读取所选任务…" : "选择左侧任务，查看它的执行配置、交付结果和验收状态。"}</p>
            <Badge>关闭页面不影响后台任务</Badge>
          </div>
        )}
      </aside>
    </div>
  );
}
