import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { ConsoleApi } from "./api";
import { errorText, readVerificationText } from "./api";
import type { Snapshot, Task } from "./types";
import type { TimelineRow } from "./objective-types";
import { WorkflowPanel } from "./WorkflowPanel";
import { taskExecutor, taskHost, taskProject } from "./console-data";
import { excerpt, resultText, taskStatus, taskTitle, titleTooltip, TASK_TITLE_SOURCE_LABEL } from "./task-state";
import { Status } from "./ui";
import { DecisionDetails } from "./DecisionDetails";
import { TaskActivityView } from "./task-activity";
import { tokenUsageView } from "./host-workflow";

/**
 * One delegation's detail (0.15.1 U4): read-only browsing of the recorded
 * facts. The task_cancel/task_retry/task_acknowledge controls and their record
 * drafts are gone — execution history stays inspectable and navigation is
 * never locked; the objective-level 停止目标 lives on the work-objective
 * header instead.
 */
export function TaskDetails({ task, snapshot, api, refresh, selectTask, active, onTaskUpdate, hideBackButton = false, initialSection, routingDecisionId, stopStatusNode, overviewRow }: {
  task: Task; snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  selectTask: (runId: string | null) => void; active: boolean;
  onTaskUpdate: (value: Task) => void;
  /** Timeline context: the locator bar is the return entry, so hide the built-in back button. */
  hideBackButton?: boolean;
  /** Section of the existing detail tabs to preselect once on open. */
  initialSection?: string;
  routingDecisionId?: string | null;
  /** Objective-level stop status for the overview tab (timeline context only). */
  stopStatusNode?: ReactNode;
  /** The timeline's own row for the fixed three-row overview block. */
  overviewRow?: TimelineRow | null;
}) {
  const [detail, setDetail] = useState<unknown>(null), [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [checked, setChecked] = useState<{ runId: string; revision: number; workflowRevision: number; at: number | null } | null>(null);
  const [routingRequest, setRoutingRequest] = useState(0);
  const update = useRef(onTaskUpdate);
  update.current = onTaskUpdate;
  const currentTask = useRef(task);
  currentTask.current = task;
  const propagateUpdate = useCallback((next: Task) => {
    const current = currentTask.current;
    if (next.runId === current.runId && next.revision >= current.revision
      && (next.workflow?.revision ?? 0) >= (current.workflow?.revision ?? 0)) update.current(next);
  }, []);
  const request = useRef<{ sequence: number; controller: AbortController | null }>({ sequence: 0, controller: null });
  const lastRead = useRef<{ runId: string } | null>(null);
  const readTask = useCallback(async () => {
    request.current.controller?.abort();
    const controller = new AbortController();
    const sequence = ++request.current.sequence;
    request.current.controller = controller;
    setBusy(true); setError("");
    try {
      const value = await api.task(task.runId, controller.signal);
      if (controller.signal.aborted || sequence !== request.current.sequence) return;
      if (!value || typeof value !== "object" || !("runId" in value) || value.runId !== task.runId || !("task" in value)) {
        throw new Error("委派详情不完整。");
      }
      const next = value as Task;
      if (next.revision < currentTask.current.revision
        || (next.workflow?.revision ?? 0) < (currentTask.current.workflow?.revision ?? 0)) return;
      lastRead.current = { runId: next.runId };
      setDetail(value);
      setChecked({ runId: next.runId, revision: next.revision, workflowRevision: next.workflow?.revision ?? 0, at: api.readVerifiedAt?.(value) ?? null });
      update.current(next);
    } catch (reason) {
      if (!controller.signal.aborted && sequence === request.current.sequence) setError(errorText(reason));
    } finally {
      if (!controller.signal.aborted && sequence === request.current.sequence) setBusy(false);
    }
  }, [api, task.runId]);
  useEffect(() => {
    setBusy(false);
    if (!active) return;
    if (!task.workflow && lastRead.current?.runId !== task.runId) void readTask();
    return () => {
      ++request.current.sequence;
      request.current.controller?.abort();
    };
  }, [active, task.runId, task.revision, !!task.workflow, readTask]);
  const verifiedAt = checked?.runId === task.runId && checked.revision === task.revision
    && checked.workflowRevision === (task.workflow?.revision ?? 0) ? checked.at : api.readVerifiedAt?.(task) ?? null;
  const project = taskProject(task);
  const decision = task.spec?.decision as { decisionId?: string } | undefined;
  const recordInfo = <details className="detail-section"><summary>来源与标识</summary><dl className="facts">
    <dt>原始委派方</dt><dd>{taskHost(task)}</dd><dt>当前 Host</dt><dd>{task.delegation?.currentHostId || task.workflow?.hostId || "未记录"}</dd>
    <dt>来源项目</dt><dd>{project.path || "未记录"}</dd><dt>委派 ID</dt><dd>{task.runId}</dd>
    <dt>上级目标</dt><dd>{task.delegation?.parentRunId || "无"}</dd><dt>执行位置</dt><dd>{task.cwd}</dd>
  </dl></details>;
  // T1: the one title rule — explicit title, task first line, 未命名委派; the
  // Worker resultSummary never appears here.
  const title = taskTitle(task);
  const titleNote = title.source === "task" ? TASK_TITLE_SOURCE_LABEL.task : "";
  // One execution's own recorded usage; unknown stays unknown (ADR-018 §22).
  const usage = tokenUsageView(task.tokenUsage ?? task.selectedAttempt?.tokenUsage);
  const quotaFailureLabel = task.quotaFailure?.code === "quota-exceeded" ? "额度耗尽" : task.quotaFailure?.code === "rate-limited" ? "原生服务限流" : null;
  return <>
    <header className="detail-header">
      <div className="row-between">{!hideBackButton && <button className="button small-button mobile-back" onClick={() => selectTask(null)}>返回委派列表</button>}
        <span className="small muted truncate" title={project.path || project.label}>{project.label}</span><Status status={taskStatus(task)} /></div>
      {!task.workflow && quotaFailureLabel && <p className="error-message">执行原因：{quotaFailureLabel}</p>}
      <h2 className="detail-title" title={titleTooltip(title)}>
        {excerpt(title.text, 100)}{titleNote && <span className="title-source-note">{titleNote}</span>}
      </h2>
      <div className="local-read-toolbar" aria-label="微任务详情读取">
        <button type="button" className="button small-button" aria-label="刷新微任务详情" title="只重新读取当前微任务详情" disabled={!active || busy} onClick={() => { void readTask(); }}>{busy ? "正在读取…" : "刷新详情"}</button>
        <span className="small muted" title="当前微任务详情读取的服务器核对时间">{readVerificationText(verifiedAt)}</span>
      </div>
      {error && <p role="alert" className="error-message">{error}
        <button type="button" className="button small-button" disabled={!active || busy} onClick={() => { void readTask(); }}>重试读取</button></p>}
      <p className="assignment-line"><span title={taskHost(task)}>委派方：{taskHost(task)}</span>
        {task.workflow ? <button className="routing-link" aria-label="查看选择依据" title={taskExecutor(task)} onClick={() => setRoutingRequest(n => n + 1)}>
          <span>→ {taskExecutor(task)}</span><span>查看选择依据</span></button> : <span title={taskExecutor(task)}>→ {taskExecutor(task)}</span>}</p>
    </header>
    {task.workflow ? <WorkflowPanel task={task} snapshot={snapshot} api={api} refresh={refresh}
        selectTask={selectTask} active={active} onTaskUpdate={propagateUpdate} recordInfo={recordInfo} routingRequest={routingRequest}
        initialSection={initialSection} routingDecisionId={routingDecisionId} stopStatusNode={stopStatusNode} overviewRow={overviewRow} /> : <div className="detail-body">
      <h3>{task.spec?.adapter === "decision" ? "内部决策计算" : "执行记录"}</h3>
      <p className="small muted" title={usage.title}>用量：{usage.text}</p>
      {recordInfo}
      <details className="detail-section"><summary>原始任务</summary><p className="read-text">{task.task}</p></details>
      <TaskActivityView task={task} />
      <section className="detail-section"><h3>交付结果</h3><pre className="result-text">{detail ? resultText(detail) : "正在读取结果…"}</pre></section>
      {task.spec?.adapter === "decision" && <section className="detail-section">
        {decision?.decisionId ? <DecisionDetails decisionId={decision.decisionId} api={api} csrfToken={snapshot.csrfToken} active={active} refreshKey={task.status} />
          : <p className="small muted">决策 ID 未记录</p>}
      </section>}
    </div>}
  </>;
}
