import { Fragment, useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task } from "./types";
import type { IntegrationRecord } from "./workflow-types";
import type { TimelineRow } from "./objective-types";
import { useWorkflow } from "./use-workflow";
import { RoutingPanel } from "./RoutingPanel";
import { Status } from "./ui";
import { excerpt } from "./task-state";
import { DetailTabs } from "./DetailTabs";
import { RoutingDetails } from "./RoutingDetails";
import { TaskActivityView } from "./task-activity";
import { finalArtifact, finalIntegration, recordedIntegrations } from "./integration";
import { formatDate } from "./ui";
import { NativeSessionView } from "./native-session";
import { DelegationDetailRows } from "./ObjectiveOverview";
import {
  artifactKindLabel, artifactStateView, configurationLockText, cumulativePatchView,
  hostConclusionView, integrationHostPaths, tokenUsageView,
} from "./host-workflow";

const integrationLabel = (record: IntegrationRecord) =>
  record.state === "not-required" ? "整合：Host 记录无需整合" : "整合：已验证";

/**
 * Governed delegation detail (0.15.1 U4): read-only facts over the recorded
 * workflow — goal and turns, routing basis, requests and helpers, artifacts
 * and acceptance evidence, execution records. Every Host write form, the
 * helper composer and the record drafts are gone; browsing needs no Host
 * credential and locks nothing.
 */
export function WorkflowPanel({ task, snapshot, api, refresh, selectTask, active = true, onTaskUpdate, recordInfo, routingRequest = 0, initialSection, routingDecisionId, stopStatusNode, overviewRow }: {
  task: Task;
  snapshot: Snapshot;
  api: ConsoleApi;
  refresh: () => Promise<Snapshot | null>;
  selectTask: (runId: string) => void;
  active?: boolean;
  onTaskUpdate?: (task: Task) => void;
  recordInfo?: ReactNode;
  routingRequest?: number;
  /** Section to preselect once when opened from the timeline; later tab use stays the user's. */
  initialSection?: string;
  routingDecisionId?: string | null;
  /** Objective-level stop status for this detail's overview tab (timeline context). */
  stopStatusNode?: ReactNode;
  /** The timeline's own row powering the fixed three-row overview block. */
  overviewRow?: TimelineRow | null;
}) {
  void refresh;
  const state = useWorkflow(api, task, snapshot, active);
  const { value } = state;
  const [section, setSection] = useState("");
  const selectedSection = section || (value?.state === "delivered" ? "artifacts" : value?.activeRequest || value?.awaitingHost ? "assistance" : "overview");
  const tabsId = "workflow-" + task.runId;
  useEffect(() => {
    if (routingRequest > 0) {
      setSection("routing");
      document.getElementById(tabsId + "-routing-tab")?.focus();
    }
  }, [routingRequest]);
  // Seeding preselects the tab once; later tab use stays the user's own choice.
  useEffect(() => { if (initialSection) setSection(initialSection); }, [initialSection, routingDecisionId]);
  useEffect(() => { if (value) onTaskUpdate?.({ ...task, ...value.task }); }, [value, onTaskUpdate]);
  const artifact = finalArtifact(value);
  const integration = finalIntegration(value, artifact);
  const conclusion = hostConclusionView(value?.hostConclusion);
  const lockText = configurationLockText(value?.configurationLocked);
  const hostPaths = integrationHostPaths(integration);
  const quotaFailureLabel = value?.task?.quotaFailure?.code === "quota-exceeded" ? "额度耗尽" : value?.task?.quotaFailure?.code === "rate-limited" ? "原生服务限流" : null;
  // Each execution carries its own usage; a turn without a recorded value is
  // shown as unknown. Session-cumulative totals are never derived here.
  const usageTurns = (value?.turns?.length ? value.turns : value?.currentTurn ? [value.currentTurn] : [])
    .map(turn => ({
      turnId: turn.turnId, turnIndex: turn.turnIndex, attemptId: turn.attemptId,
      tokenUsage: turn.tokenUsage,
      executionConfiguration: "executionConfiguration" in turn ? turn.executionConfiguration ?? null : null,
    }));
  // `recordedIntegrations` is newest first, so the first record per artifact is
  // the newest one — the same record acceptance binds.
  const integrationsByArtifact = new Map<string, IntegrationRecord>();
  for (const record of recordedIntegrations(value)) {
    if (!integrationsByArtifact.has(record.artifactId)) integrationsByArtifact.set(record.artifactId, record);
  }
  return <div className="workflow-panel">
    {state.error && <p className="error-message" role="alert">{state.error}</p>}
    {(value?.awaitingHost || value?.activeRequest?.state === "open") && <div className="attention-bar" role="status"><span>等待决定：{excerpt(value!.activeRequest?.summary || value!.waitReason, 120)}</span><button className="button small-button" onClick={() => setSection("assistance")}>查看请求</button><span className="small muted">由 Host 处理</span></div>}
    <DetailTabs id={tabsId} label="委派详情栏目" value={selectedSection}
      items={[["overview", "概览"], ["routing", "路由依据"], ["assistance", "协作与待办"], ["artifacts", "产物与验收"], ["execution", "执行记录"]]} onChange={setSection} />
    <div className="detail-body">
    {!value ? <p role="status">正在读取协作记录…</p> : <>
      <div id={tabsId + "-overview"} role="tabpanel" aria-labelledby={tabsId + "-overview-tab"} hidden={selectedSection !== "overview"}>
      <section className="detail-section">
        <h3>概览</h3>
        {/* T4: the overview never repeats the detail heading's title; only the
            task summary and the Worker's own result line remain. The timeline's
            own row is authoritative when available. */}
        <DelegationDetailRows
          taskSummary={overviewRow ? overviewRow.taskSummary : (task.task.replace(/\s+/g, " ").trim().slice(0, 120) || null)}
          resultSummary={overviewRow ? overviewRow.summary : (value.currentTurn?.summary || task.workflow?.resultSummary || null)} />
        {stopStatusNode}
        {quotaFailureLabel && <p className="error-message">执行原因：{quotaFailureLabel}</p>}
        {conclusion && <section className={"detail-section host-conclusion" + (conclusion.failed ? " failed" : conclusion.cancelled ? " cancelled" : "")} aria-label="Host 结论">
          <h3>Host 结论（不改变执行结果）</h3>
          <dl className="facts">
            <dt>目标结果</dt><dd>{conclusion.statusLabel}（执行结果本身未改变，不计入已验收）</dd>
            <dt>记录 Host</dt><dd>{conclusion.actor}{conclusion.ownerGeneration === null ? "" : ` · 第 ${conclusion.ownerGeneration} 代`}</dd>
            <dt>记录时间</dt><dd>{formatDate(conclusion.createdAt)}</dd>
            <dt>目标版本</dt><dd>{conclusion.runRevision === null ? "未记录" : `V${conclusion.runRevision}`}</dd>
            {conclusion.references.map(reference => <Fragment key={reference.label}>
              <dt>{reference.label}</dt><dd className="mono wrap">{reference.value}</dd></Fragment>)}
          </dl>
          <p className="task-description">{conclusion.note}</p>
          {conclusion.evidence.length > 0 && <details><summary>结论依据（{conclusion.evidence.length}）</summary>
            <ul className="conclusion-evidence">{conclusion.evidence.map((item, index) => <li key={index}>{item}</li>)}</ul>
          </details>}
        </section>}
        <details className="record-facts">
          <summary>记录细节（目标与回合）</summary>
          <div className="record-facts-body">
            <Status status={value.state} />
            <dl className="facts">
              <dt>当前 Host</dt><dd>{value.task.delegation?.currentHostId || value.hostId}</dd>
              <dt>协作版本</dt><dd>V{value.revision}</dd>
              <dt>接续次数</dt><dd>{value.continuationCount}</dd>
              <dt>已记录回合</dt><dd>{value.counts?.turns == null ? "未记录" : `共 ${value.counts.turns} 个回合`}</dd>
              <dt>接续方式</dt><dd>{value.currentTurn?.resumeMode === "native-session" ? "恢复原生会话" : value.currentTurn?.resumeMode === "reconstructed-new-session" ? "新会话，从持久上下文重建" : "首次执行"}</dd>
              {value.executionConfiguration && <Fragment>
                <dt>执行配置</dt><dd>{
                  `${value.executionConfiguration.adapter} · ${value.executionConfiguration.model} / ${value.executionConfiguration.effort}`
                  + (lockText ? `｜${lockText}` : "｜配置来源未记录")
                }</dd></Fragment>}
              <dt>输入提交</dt><dd className="mono wrap">{value.workspace?.inputCommit || "未记录"}</dd>
              <dt>等待原因</dt><dd className="wrap">{value.waitReason}</dd>
              <dt>执行停止</dt><dd>{value.shutdown?.selfConfirmed ? "本任务已确认停止" : "本任务尚未确认停止"}{value.shutdown && !value.shutdown.descendantsConfirmed ? `；目标范围内共 ${value.shutdown.unconfirmedCount} 项执行尚未核实` : ""}</dd>
              <dt>验收记录</dt><dd>{value.task.acceptanceVerdict === "accepted" ? "已验收" : value.task.acceptanceVerdict === "rejected" ? "验收问题" : "未验收"}</dd>
            </dl>
            <p className="small muted">接续次数来自持久轮次记录，不是模型调用次数；控制台不根据轮询或接续次数推算模型调用。</p>
            <p className="task-description">{excerpt(value.currentTurn?.summary || "本轮尚未提交结构化结果。", 360)}</p>
            {(value.currentTurn?.summary?.length || 0) > 360 && <details><summary>展开回合摘要</summary><p className="task-description">{value.currentTurn?.summary}</p></details>}
            {value.currentTurn?.summaryTruncated && <p className="small muted">当前摘要已截断。完整记录可通过 get 的 includeAudit 选项读取。</p>}
            {!!value.currentTurn?.remaining?.length && <ul>{value.currentTurn.remaining.map((item, i) => <li key={i}>{item}</li>)}</ul>}
            <details><summary>展开完整任务</summary><p className="read-text">{task.task}</p></details>
            <TaskActivityView task={task} />
          </div>
        </details>
      </section>
      </div>
      <div id={tabsId + "-routing"} role="tabpanel" aria-labelledby={tabsId + "-routing-tab"} hidden={selectedSection !== "routing"}>
        {selectedSection === "routing" && <RoutingDetails key={value.runId} value={value} api={api} csrfToken={snapshot.csrfToken} active={active} initialDecisionId={routingDecisionId} />}
      </div>
      <div id={tabsId + "-assistance"} role="tabpanel" aria-labelledby={tabsId + "-assistance-tab"} hidden={selectedSection !== "assistance"}>
      <RoutingPanel key={value.activeRequest?.requestId || value.runId} value={value} />
      {value.children.length > 0 && <section className="detail-section"><h3>关联执行</h3><ul className="workflow-children">
        {value.children.map(child => <li key={child.taskId}>
          <span>{child.role === "router" ? "路由计算" : "协助任务"}</span><button className="button small-button mono" onClick={() => selectTask(child.taskId)}>{child.taskId}</button><Status status={child.state} />
        </li>)}
      </ul></section>}
      {value.activeRequest && <section className="detail-section">
        <h3>{value.activeRequest.kind === "assistance" ? "协助请求" : "需要决定"}</h3>
        {(value.counts?.openRequests || 0) > 1 && <p className="small muted">共有 {value.counts?.openRequests} 项待决定；Host 处理当前请求后会显示下一项。</p>}
        <p>{value.activeRequest.summary}</p>
        {value.activeRequest.preparationError && <p role="alert"><code>{value.activeRequest.preparationError.code}</code> · {value.activeRequest.preparationError.message}</p>}
        <dl className="facts"><dt>已尝试</dt><dd>{value.activeRequest.attempted}</dd><dt>需要完成</dt><dd>{value.activeRequest.neededWork.join("\n")}</dd><dt>验收条件</dt><dd>{value.activeRequest.acceptance}</dd><dt>请求状态</dt><dd>{value.activeRequest.state === "open" ? "等待 Host 决定" : value.activeRequest.state}</dd></dl>
        {!!value.activeRequest.expectedArtifacts.length && <p className="small">期望产物：{value.activeRequest.expectedArtifacts.join("、")}</p>}
        {value.activeRequest.childTaskId && <button className="button" onClick={() => selectTask(value.activeRequest!.origin?.runId || value.activeRequest!.childTaskId!)}>打开需要协助的子任务</button>}
      </section>}
      </div>
      <div id={tabsId + "-artifacts"} role="tabpanel" aria-labelledby={tabsId + "-artifacts-tab"} hidden={selectedSection !== "artifacts"}>
      {value.artifacts.length > 0 ? <section className="detail-section"><h3>固定产物引用</h3><ul className="artifact-list">
        {value.artifacts.map(a => {
          const state = artifactStateView(a);
          const patch = cumulativePatchView(a.cumulativePatch);
          const record = integrationsByArtifact.get(a.artifactId);
          const artifactHostPaths = integrationHostPaths(record);
          return <li key={a.artifactId} className={state.partial ? "artifact-partial" : undefined}>
            <details><summary>{artifactKindLabel(a.kind)} · {!a.sourceTaskId || a.sourceTaskId === value.runId ? "本任务" : "关联任务"} · {(a.outputCommit || a.commit || a.artifactId).slice(0, 12)}</summary>
              <code>{a.artifactId}</code>
              {state.partial && <code className="artifact-flags">{state.text}</code>}
              {(a.outputCommit || a.commit) && <code>commit {a.outputCommit || a.commit}</code>}
              <code>SHA-256 {a.snapshotSha256 || a.manifestSha256}</code>
              {a.diffPath && <code>{a.diffPath}</code>}
              {record && <code>{integrationLabel(record)}</code>}
              {patch && <div className="cumulative-patch">
                <span className="small muted">累计补丁（自目标最初输入提交）{patch.complete ? "" : " · 引用不完整"}</span>
                <code>base {patch.baseCommit}</code>
                <code>output {patch.outputCommit}</code>
                <code>{patch.path}</code>
                <code>SHA-256 {patch.sha256}</code>
                {patch.changedPaths.length > 0 && <code className="wrap">{patch.changedPaths.join("、")}</code>}
              </div>}
              {artifactHostPaths.length > 0 && <div className="host-paths">
                <span className="small muted">Host 补充改动（与成果路径分开记录）</span>
                <code className="wrap">{artifactHostPaths.join("、")}</code>
              </div>}
            </details></li>;
        })}
      </ul><p className="small muted">这些引用固定在具体执行。验收前仍须检查实际 diff 和测试结果。部分成果标明“未验证、非最终”，不能被当作已验收交付。</p></section> : <p className="muted">尚无固定产物。</p>}
      {artifact && <section className="detail-section" aria-label="最终产物与整合证据">
        <h3>最终产物与整合证据</h3>
        <dl className="facts">
          <dt>最终产物</dt><dd className="mono wrap">{artifact.artifactId}</dd>
          <dt>产物类型</dt><dd>{artifact.kind === "resolved-output" ? "经 Host 处理并重新封存的产物" : "执行输出"}</dd>
          {(artifact.outputCommit || artifact.commit) && <><dt>产物提交</dt><dd className="mono wrap">{artifact.outputCommit || artifact.commit}</dd></>}
          <dt>产物校验</dt><dd className="mono wrap">SHA-256 {artifact.snapshotSha256 || artifact.manifestSha256}</dd>
        </dl>
        {integration ? <dl className="facts">
          <dt>整合状态</dt><dd>{integration.state === "not-required" ? "Host 已明确记录无需整合" : "已验证整合"}</dd>
          {integration.state !== "not-required" && integration.strategy && <><dt>整合方式</dt><dd>{integration.strategy}</dd></>}
          {integration.state !== "not-required" && integration.target && <><dt>整合目标</dt><dd className="mono wrap">{`${integration.target.path} @ ${integration.target.ref}`}</dd></>}
          {integration.state !== "not-required" && integration.beforeCommit && integration.afterCommit
            && <><dt>目标变更</dt><dd className="mono wrap">{`${integration.beforeCommit} → ${integration.afterCommit}`}</dd></>}
          {integration.reason && <><dt>记录原因</dt><dd className="wrap">{integration.reason}</dd></>}
          {typeof integration.verification?.summary === "string" && integration.verification.summary.trim()
            && <><dt>验证摘要</dt><dd className="wrap">{integration.verification.summary.trim()}</dd></>}
          {hostPaths.length > 0 && <><dt>Host 补充改动</dt><dd className="wrap">{
            `与成果路径分开记录（${hostPaths.length} 项）：${hostPaths.join("、")}`
          }</dd></>}
          <dt>记录来源</dt><dd>{`${integration.actor} · ${formatDate(integration.createdAt)}`}</dd>
        </dl> : <p className="banner guard-banner" role="status">
          尚未记录最终产物的整合证明。整合与验收由 Host 通过既有 CLI 流程（integration-record 与验收记录）完成，此页只展示结果。
        </p>}
      </section>}
      </div>
      <div id={tabsId + "-execution"} role="tabpanel" aria-labelledby={tabsId + "-execution-tab"} hidden={selectedSection !== "execution"}>
      {recordInfo}
      <h3>执行记录</h3><dl className="facts"><dt>本记录权限</dt><dd>{value.hostId} · 第 {value.ownerGeneration} 代</dd><dt>执行位置</dt><dd>{value.workspace?.path || "未记录"}</dd><dt>当前回合</dt><dd>{value.currentTurn?.turnId || "尚未开始"}</dd><dt>执行尝试</dt><dd>{value.currentTurn?.attemptId || "尚未开始"}</dd><dt>回合会话</dt><dd>{value.currentTurn?.sessionId || "未记录"}</dd></dl>
      <section className="detail-section" aria-label="每次执行的用量">
        <h4>每次执行的用量</h4>
        {usageTurns.length === 0 ? <p className="small muted">尚无执行记录。</p> : <ul className="token-usage-list">
          {usageTurns.map(turn => {
            const usage = tokenUsageView(turn.tokenUsage);
            const config = turn.executionConfiguration;
            return <li key={turn.turnId}>
              <span className="turn-label">第 {turn.turnIndex} 回合</span>
              <span className="small muted">{config ? `${config.adapter} · ${config.model} / ${config.effort}` : "配置未记录"}</span>
              <span className="turn-usage" title={usage.title}>{usage.text}</span>
              <span className="small muted">来源 {turn.tokenUsage?.source || "未记录"}</span>
            </li>;
          })}
        </ul>}
        <p className="small muted">每次执行分别记录，不是会话累计；输入数已含缓存输入，缓存不重复相加；没有记录的执行显示未知，不估算为 0。额度失败或被中断时，已封存的部分成果仍会列在“产物与验收”。</p>
      </section>
      <NativeSessionView task={task} turnSessionId={value.currentTurn?.sessionId} />
      <details className="detail-section"><summary>原始目标</summary><p className="read-text">{task.task}</p></details>
      </div>
    </>}
    </div>
  </div>;
}
