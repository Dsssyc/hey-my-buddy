import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import type { Snapshot, Task } from "./types";
import type { TimelineRow } from "./objective-types";
import { WorkflowPanel } from "./WorkflowPanel";
import { taskExecutor, taskHost, taskProject } from "./console-data";
import { excerpt, resultText, taskStatus, taskTitle } from "./task-state";
import { Status } from "./ui";
import { DecisionDetails } from "./DecisionDetails";
import { TaskActivityView } from "./task-activity";

/**
 * One delegation's detail (0.15.1 U4): read-only browsing of the recorded
 * facts. The task_cancel/task_retry/task_acknowledge controls and their record
 * drafts are gone — execution history stays inspectable and navigation is
 * never locked; the objective-level 停止目标 lives on the work-objective
 * header instead.
 */
export function TaskDetails({ task, snapshot, api, refresh, selectTask, active, onTaskUpdate, hideBackButton = false, initialSection, stopStatusNode, overviewRow }: {
  task: Task; snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  selectTask: (runId: string | null) => void; active: boolean;
  onTaskUpdate: (value: Task) => void;
  /** Timeline context: the locator bar is the return entry, so hide the built-in back button. */
  hideBackButton?: boolean;
  /** Section of the existing detail tabs to preselect once on open. */
  initialSection?: string;
  /** Objective-level stop status for the overview tab (timeline context only). */
  stopStatusNode?: ReactNode;
  /** The timeline's own row for the fixed three-row overview block. */
  overviewRow?: TimelineRow | null;
}) {
  const [detail, setDetail] = useState<unknown>(null), [error, setError] = useState("");
  const [routingRequest, setRoutingRequest] = useState(0);
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
  const project = taskProject(task);
  const decision = task.spec?.decision as { decisionId?: string } | undefined;
  const recordInfo = <details className="detail-section"><summary>来源与标识</summary><dl className="facts">
    <dt>原始委派方</dt><dd>{taskHost(task)}</dd><dt>当前 Host</dt><dd>{task.delegation?.currentHostId || task.workflow?.hostId || "未记录"}</dd>
    <dt>来源项目</dt><dd>{project.path || "未记录"}</dd><dt>委派 ID</dt><dd>{task.runId}</dd>
    <dt>上级目标</dt><dd>{task.delegation?.parentRunId || "无"}</dd><dt>执行位置</dt><dd>{task.cwd}</dd>
  </dl></details>;
  return <>
    <header className="detail-header">
      <div className="row-between">{!hideBackButton && <button className="button small-button mobile-back" onClick={() => selectTask(null)}>返回委派列表</button>}
        <span className="small muted truncate" title={project.path || project.label}>{project.label}</span><Status status={taskStatus(task)} /></div>
      <h2 title={task.task}>{excerpt(taskTitle(task), 100)}</h2>
      <p className="assignment-line"><span title={taskHost(task)}>委派方：{taskHost(task)}</span>
        {task.workflow ? <button className="routing-link" aria-label="查看选择依据" title={taskExecutor(task)} onClick={() => setRoutingRequest(n => n + 1)}>
          <span>→ {taskExecutor(task)}</span><span>查看选择依据</span></button> : <span title={taskExecutor(task)}>→ {taskExecutor(task)}</span>}</p>
    </header>
    {task.workflow ? <WorkflowPanel task={task} snapshot={snapshot} api={api} refresh={refresh}
        selectTask={selectTask} active={active} onTaskUpdate={onTaskUpdate} recordInfo={recordInfo} routingRequest={routingRequest}
        initialSection={initialSection} stopStatusNode={stopStatusNode} overviewRow={overviewRow} /> : <div className="detail-body">
      {error && <p role="alert" className="error-message">{error}</p>}
      <h3>{task.spec?.adapter === "decision" ? "内部决策计算" : "执行记录"}</h3>
      <p className="small muted">来源字段只展示已记录的信息；执行结束与验收分别记录。此页只读，取消与重试由 Host 通过既有 CLI 流程完成。</p>
      {recordInfo}
      <details className="detail-section"><summary>原始任务</summary><p className="read-text">{task.task}</p></details>
      <TaskActivityView task={task} />
      <section className="detail-section"><h3>交付结果</h3><pre className="result-text">{detail ? resultText(detail) : "正在读取结果…"}</pre></section>
      {task.spec?.adapter === "decision" && <section className="detail-section">
        <p className="small muted">此计算记录无需业务验收。</p>
        {decision?.decisionId ? <DecisionDetails decisionId={decision.decisionId} api={api} csrfToken={snapshot.csrfToken} active={active} refreshKey={task.status} />
          : <p className="small muted">未记录关联的决策 ID。</p>}
      </section>}
    </div>}
  </>;
}
