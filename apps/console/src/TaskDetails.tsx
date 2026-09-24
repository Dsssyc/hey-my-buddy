import { useEffect, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import type { Snapshot, Task } from "./types";
import { WorkflowPanel } from "./WorkflowPanel";
import { useRecordDraft } from "./record-drafts";
import { taskExecutor, taskHost, taskProject } from "./console-data";
import { canRetry, excerpt, needsReview, resultText, taskStatus } from "./task-state";
import { Status } from "./ui";

export function TaskDetails({ task, snapshot, api, refresh, selectTask, active, onLockChange, onTaskUpdate, navigationLocked }: {
  task: Task; snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  selectTask: (runId: string | null) => void; active: boolean; onLockChange: (value: boolean) => void;
  onTaskUpdate: (value: Task) => void; navigationLocked: boolean;
}) {
  const [detail, setDetail] = useState<unknown>(null), [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useRecordDraft(task.runId, "executionNote", "");
  useEffect(() => {
    if (!active || task.workflow) return;
    let current = true;
    setDetail(null); setError("");
    api.task(task.runId).then(value => { if (current) {
      setDetail(value);
      if (value && typeof value === "object" && "runId" in value && value.runId === task.runId && "task" in value) onTaskUpdate(value as Task);
    } })
      .catch(reason => { if (current) setError(errorText(reason)); });
    return () => { current = false; };
  }, [api, active, task.runId, task.revision, !!task.workflow, onTaskUpdate]);
  async function command(operation: string, params: Record<string, unknown>) {
    setBusy(true); onLockChange(true); setError("");
    try { await api.command(operation, params, snapshot.csrfToken); await refresh(); }
    catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); onLockChange(false); }
  }
  const project = taskProject(task);
  const recordInfo = <details className="detail-section"><summary>来源与标识</summary><dl className="facts">
    <dt>原始委派方</dt><dd>{taskHost(task)}</dd><dt>当前 Host</dt><dd>{task.delegation?.currentHostId || task.workflow?.hostId || "未记录"}</dd>
    <dt>来源项目</dt><dd>{project.path || "未记录"}</dd><dt>委派 ID</dt><dd>{task.runId}</dd>
    <dt>上级目标</dt><dd>{task.delegation?.parentRunId || "无"}</dd><dt>执行位置</dt><dd>{task.cwd}</dd>
  </dl></details>;
  return <>
    <header className="detail-header">
      <div className="row-between"><button className="button small-button mobile-back" disabled={busy || navigationLocked} onClick={() => selectTask(null)}>返回委派列表</button>
        <span className="small muted truncate" title={project.path || project.label}>{project.label}</span><Status status={taskStatus(task)} /></div>
      <h2 title={task.task}>{excerpt(task.task.split("\n", 1)[0], 100)}</h2>
      <p className="assignment-line"><span title={taskHost(task)}>委派方：{taskHost(task)}</span><span title={taskExecutor(task)}>→ {taskExecutor(task)}</span></p>
    </header>
    {task.workflow ? <WorkflowPanel task={task} snapshot={snapshot} api={api} refresh={refresh}
      selectTask={selectTask} active={active} onLockChange={onLockChange} onTaskUpdate={onTaskUpdate} recordInfo={recordInfo} /> : <div className="detail-body">
      {error && <p role="alert" className="error-message">{error}</p>}
      <h3>{task.spec?.adapter === "decision" ? "内部决策计算" : "执行记录"}</h3>
      <p className="small muted">来源字段只展示已记录的信息；执行结束与验收分别记录。</p>
      {recordInfo}
      <details className="detail-section"><summary>原始任务</summary><p className="read-text">{task.task}</p></details>
      <section className="detail-section"><h3>交付结果</h3><pre className="result-text">{detail ? resultText(detail) : "正在读取结果…"}</pre></section>
      <div className="actions">
        {["queued", "running", "cancelling"].includes(task.status) && <button className="button danger" disabled={busy || task.status === "cancelling"}
          onClick={() => void command("task_cancel", { runId: task.runId })}>取消任务</button>}
        {canRetry(task) && <button className="button" disabled={busy} onClick={() => void command("task_retry", { runId: task.runId })}>重新尝试</button>}
      </div>
      {task.spec?.adapter === "decision" && <p className="small muted">推荐依据可在“路由配置”查看。此记录无需业务验收。</p>}
      {needsReview(task) && <section className="detail-section"><h3>记录验收</h3>
        <label className="field"><span>检查依据</span><textarea rows={3} value={note} maxLength={2000} onChange={e => setNote(e.target.value)} /></label>
        <div className="actions">{(["accepted", "rejected"] as const).map(verdict => <button key={verdict} className="button" disabled={busy || !note.trim() || task.shutdownConfirmed !== true}
          onClick={() => void command("task_acknowledge", { runId: task.runId, verdict, note: note.trim() })}>{verdict === "accepted" ? "接受交付" : "记录验收问题"}</button>)}</div>
      </section>}
    </div>}
  </>;
}
