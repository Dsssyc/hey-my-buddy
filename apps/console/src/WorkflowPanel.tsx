import { useEffect } from "react";
import type { ReactNode } from "react";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task } from "./types";
import type { HelperDraft } from "./workflow-types";
import { useWorkflow } from "./use-workflow";
import { HelperForm } from "./HelperForm";
import { RoutingPanel, needsRouting } from "./RoutingPanel";
import { Status } from "./ui";
import { excerpt } from "./task-state";
import { DetailTabs } from "./DetailTabs";
import { useRecordDraft } from "./record-drafts";

const lines = (text: string) => text.split("\n").map(s => s.trim()).filter(Boolean);

export function WorkflowPanel({ task, snapshot, api, refresh, selectTask, active = true, onLockChange, onTaskUpdate, recordInfo }: {
  task: Task;
  snapshot: Snapshot;
  api: ConsoleApi;
  refresh: () => Promise<Snapshot | null>;
  selectTask: (runId: string) => void;
  active?: boolean;
  onLockChange?: (locked: boolean) => void;
  onTaskUpdate?: (task: Task) => void;
  recordInfo?: ReactNode;
}) {
  const state = useWorkflow(api, task, snapshot, refresh, active);
  const { value, command } = state;
  const [reason, setReason] = useRecordDraft(task.runId, "reason", "");
  const [input, setInput] = useRecordDraft(task.runId, "input", "");
  const [note, setNote] = useRecordDraft(task.runId, "note", "");
  const [host, setHost] = useRecordDraft(task.runId, "host", "");
  const [helpers, setHelpers] = useRecordDraft<HelperDraft[]>(task.runId, "helpers", []);
  const [autoContinue, setAutoContinue] = useRecordDraft(task.runId, "autoContinue", true);
  const [helperPolicy, setHelperPolicy] = useRecordDraft(task.runId, "helperPolicy", "");
  const [draftRequest, setDraftRequest] = useRecordDraft<string | null>(task.runId, "requestId", null);
  const [section, setSection] = useRecordDraft(task.runId, "section", "");
  const selectedSection = section || (value?.state === "delivered" ? "artifacts" : value?.activeRequest || value?.awaitingHost ? "assistance" : "overview");
  const tabsId = "workflow-" + task.runId;
  useEffect(() => { onLockChange?.(state.busy || state.uncertain); }, [state.busy, state.uncertain, onLockChange]);
  useEffect(() => { if (value) onTaskUpdate?.({ ...task, ...value.task }); }, [value, onTaskUpdate]);
  const requestId = value?.activeRequest?.requestId;
  useEffect(() => {
    if (!requestId || state.uncertain || draftRequest === requestId) return;
    setDraftRequest(requestId);
    setReason(""); setHelpers([]); setAutoContinue(true);
  }, [requestId, state.uncertain]);
  const profiles = snapshot.profiles.filter(p => p.enabled && p.available && ["dsh", "zcode"].includes(p.adapter));
  const locked = state.busy || state.uncertain || !value;
  const activeHelpers = value?.children.some(c => c.role === "helper" && c.state === "active");
  const canApprove = helpers.length > 0 && helpers.every(h =>
    (!h.profileId || profiles.some(p => p.profileId === h.profileId)) && h.task.trim() && h.cwd.startsWith("/") && (h.access === "read" || lines(h.writeScope).length));
  function addHelper() {
    setHelpers(current => [...current, {
      id: crypto.randomUUID(), profileId: "", task: "", cwd: value?.activeRequest?.childTaskId ? "" : value?.workspace?.path || task.cwd,
      kind: "worktree", access: "write", writeScope: "", includeUntracked: "",
    }]);
  }
  function decide(decision: "approve" | "decline") {
    if (!value?.activeRequest) return;
    void command("workflow_decide", {
      expectedRevision: value.revision, requestId: value.activeRequest.requestId,
      decision, reason: reason.trim(), autoContinue,
      helpers: decision === "decline" ? [] : helpers.map(h => {
        const profile = profiles.find(p => p.profileId === h.profileId);
        return {
          requestId: crypto.randomUUID(), task: h.task.trim(), cwd: h.cwd,
          ...(profile ? { adapter: profile.adapter, provider: profile.provider, model: profile.model, effort: profile.effort } : {}),
          timeoutSeconds: 28800, workspace: task.spec?.workspace !== false,
          executionWorkspace: {
            kind: h.kind, cwd: h.cwd, access: h.access, base: { kind: "working-tree" },
            includeUntracked: lines(h.includeUntracked), writeScope: h.access === "read" ? [] : lines(h.writeScope),
            integrator: value.runId,
          },
        };
      }),
    });
  }
  const artifact = value?.artifacts.find(a => a.kind === "output" && a.attemptId === value.finalAttemptId);
  return <div className="workflow-panel">
    {state.error && <p className="error-message" role="alert">{state.error}</p>}
    {state.notice && <p className="success-message" role="status">{state.notice}</p>}
    {state.controlFile && <p className="small wrap">交给新 Host 的控制文件路径：<code>{state.controlFile}</code>。路径可用于 CLI 的 controlFile；不要复制文件中的凭据。</p>}
    {state.uncertain && <button className="button primary" disabled={state.busy} onClick={() => void command("")}>重试同一操作</button>}
    {(value?.awaitingHost || value?.activeRequest?.state === "open") && <div className="attention-bar" role="status"><span>等待决定：{excerpt(value.activeRequest?.summary || value.waitReason, 120)}</span><button className="button small-button" onClick={() => setSection("assistance")}>处理请求</button></div>}
    <DetailTabs id={tabsId} label="委派详情栏目" value={selectedSection}
      items={[["overview", "概览"], ["assistance", "协作与待办"], ["artifacts", "产物与验收"], ["execution", "执行记录"]]} onChange={setSection} />
    <div className="detail-body">
    {!value ? <p role="status">正在读取协作记录…</p> : <>
      <div id={tabsId + "-overview"} role="tabpanel" aria-labelledby={tabsId + "-overview-tab"} hidden={selectedSection !== "overview"}>
      <section className="detail-section">
        <h3>目标与回合</h3><Status status={value.state} />
        <dl className="facts">
          <dt>当前 Host</dt><dd>{value.task.delegation?.currentHostId || value.hostId}</dd>
          <dt>协作版本</dt><dd>V{value.revision}</dd>
          <dt>接续次数</dt><dd>{value.continuationCount}</dd>
          <dt>接续方式</dt><dd>{value.currentTurn?.resumeMode === "native-session" ? "恢复原生会话" : value.currentTurn?.resumeMode === "reconstructed-new-session" ? "新会话，从持久上下文重建" : "首次执行"}</dd>
          <dt>输入提交</dt><dd className="mono wrap">{value.workspace?.inputCommit || "未记录"}</dd>
          <dt>等待原因</dt><dd className="wrap">{value.waitReason}</dd>
          <dt>执行停止</dt><dd>{value.shutdown?.selfConfirmed ? "本任务已确认停止" : "本任务尚未确认停止"}{value.shutdown && !value.shutdown.descendantsConfirmed ? `；目标范围内共 ${value.shutdown.unconfirmedCount} 项执行尚未核实` : ""}</dd>
        </dl>
        <p className="task-description">{excerpt(value.currentTurn?.summary || "本轮尚未提交结构化结果。", 360)}</p>
        {(value.currentTurn?.summary?.length || 0) > 360 && <details><summary>展开回合摘要</summary><p className="task-description">{value.currentTurn?.summary}</p></details>}
        {value.currentTurn?.summaryTruncated && <p className="small muted">当前摘要已截断。完整记录可通过 get 的 includeAudit 选项读取。</p>}
        {!!value.currentTurn?.remaining?.length && <ul>{value.currentTurn.remaining.map((item, i) => <li key={i}>{item}</li>)}</ul>}
      </section>
      </div>
      <div id={tabsId + "-assistance"} role="tabpanel" aria-labelledby={tabsId + "-assistance-tab"} hidden={selectedSection !== "assistance"}>
      <RoutingPanel key={value.activeRequest?.requestId || value.runId} value={value} profiles={profiles} locked={locked} command={command} />
      {value.children.length > 0 && <section className="detail-section"><h3>关联执行</h3><ul className="workflow-children">
        {value.children.map(child => <li key={child.taskId}>
          <span>{child.role === "router" ? "路由计算" : "协助任务"}</span><button className="button small-button mono" onClick={() => selectTask(child.taskId)}>{child.taskId}</button><Status status={child.state} />
        </li>)}
      </ul></section>}
      {value.activeRequest && !needsRouting(value) && <section className="detail-section">
        <h3>{value.activeRequest.kind === "assistance" ? "协助请求" : "需要决定"}</h3>
        {(value.counts?.openRequests || 0) > 1 && <p className="small muted">共有 {value.counts?.openRequests} 项待决定；处理当前请求后会显示下一项。</p>}
        <p>{value.activeRequest.summary}</p>
        {value.activeRequest.preparationError && <p role="alert"><code>{value.activeRequest.preparationError.code}</code> · {value.activeRequest.preparationError.message}</p>}
        <dl className="facts"><dt>已尝试</dt><dd>{value.activeRequest.attempted}</dd><dt>需要完成</dt><dd>{value.activeRequest.neededWork.join("\n")}</dd><dt>验收条件</dt><dd>{value.activeRequest.acceptance}</dd></dl>
        {!!value.activeRequest.expectedArtifacts.length && <p className="small">期望产物：{value.activeRequest.expectedArtifacts.join("、")}</p>}
        {value.activeRequest.childTaskId && <button className="button" onClick={() => selectTask(value.activeRequest!.origin?.runId || value.activeRequest!.childTaskId!)}>打开需要协助的子任务</button>}
        {value.activeRequest.state === "open" && <fieldset className="workflow-controls" disabled={locked}>
          <legend>用户决定</legend>
          <p className="small muted">此处使用当前私有控制台的用户权限。选择和理由会记录在黑板，跨 Buddy 调用仍需明确批准。</p>
          <label className="field"><span>决定理由</span><textarea value={reason} onChange={e => setReason(e.target.value)} maxLength={4000} rows={3} /></label>
          {helpers.map((helper, i) => <HelperForm key={helper.id} helper={helper} index={i} profiles={profiles}
            onChange={next => setHelpers(all => all.map(h => h.id === next.id ? next : h))}
            onRemove={() => setHelpers(all => all.filter(h => h.id !== helper.id))} />)}
          <button className="button small-button" disabled={helpers.length >= 8} onClick={addHelper}>添加协助任务</button>
          {!profiles.length && <p className="small muted">未指定配置时将请求自动路由；若未启用候选配置，任务会等待 Host 补齐。</p>}
          <label className="check-field"><input type="checkbox" checked={autoContinue} onChange={e => setAutoContinue(e.target.checked)} />结果返回后自动接续一次</label>
          <div className="actions"><button className="button primary" disabled={!reason.trim() || !canApprove} onClick={() => decide("approve")}>批准所列协助</button>
            <button className="button" disabled={!reason.trim()} onClick={() => decide("decline")}>拒绝协助并记录理由</button></div>
        </fieldset>}
      </section>}
      {["awaiting-host", "waiting-helpers", "failed"].includes(value.state) && !needsRouting(value) && <fieldset className="workflow-controls" disabled={locked}>
        <legend>手工接续</legend>
        <label className="field"><span>交给下一回合的输入</span><textarea rows={4} maxLength={16000} value={input} onChange={e => setInput(e.target.value)} placeholder="补充事实、你已完成的工作、需要整合的固定产物与下一步" /></label>
        {activeHelpers && <label className="field"><span>仍在执行的协助任务</span><select value={helperPolicy} onChange={e => setHelperPolicy(e.target.value)}><option value="">明确选择处理方式</option><option value="keep">保留，让它们继续执行</option><option value="cancel">发出取消请求，等待真实停止</option></select></label>}
        <button className="button primary" disabled={!input.trim() || (activeHelpers && !helperPolicy)} onClick={() => void command("workflow_continue", {
          expectedRevision: value.revision, input: input.trim(), ...(activeHelpers ? { helperPolicy } : {}),
        })}>提交接续输入</button>
      </fieldset>}
      </div>
      <div id={tabsId + "-artifacts"} role="tabpanel" aria-labelledby={tabsId + "-artifacts-tab"} hidden={selectedSection !== "artifacts"}>
      {value.artifacts.length > 0 ? <section className="detail-section"><h3>固定产物引用</h3><ul className="artifact-list">
        {value.artifacts.map(a => <li key={a.artifactId}><details><summary>{a.kind} · {!a.sourceTaskId || a.sourceTaskId === value.runId ? "本任务" : "关联任务"} · {(a.outputCommit || a.commit || a.artifactId).slice(0, 12)}</summary><code>{a.artifactId}</code>{(a.outputCommit || a.commit) && <code>commit {a.outputCommit || a.commit}</code>}<code>SHA-256 {a.snapshotSha256 || a.manifestSha256}</code>{a.diffPath && <code>{a.diffPath}</code>}</details></li>)}
      </ul><p className="small muted">这些引用固定在具体执行。验收前仍须检查实际 diff 和测试结果。</p></section> : <p className="muted">尚无固定产物。</p>}
      </div>
      <div id={tabsId + "-execution"} role="tabpanel" aria-labelledby={tabsId + "-execution-tab"} hidden={selectedSection !== "execution"}>
      {recordInfo}
      <h3>执行记录</h3><dl className="facts"><dt>本记录权限</dt><dd>{value.hostId} · 第 {value.ownerGeneration} 代</dd><dt>执行位置</dt><dd>{value.workspace?.path || "未记录"}</dd><dt>当前回合</dt><dd>{value.currentTurn?.turnId || "尚未开始"}</dd><dt>执行尝试</dt><dd>{value.currentTurn?.attemptId || "尚未开始"}</dd><dt>原生会话</dt><dd>{value.currentTurn?.sessionId || "未记录"}</dd></dl>
      <details className="detail-section"><summary>原始目标</summary><p className="read-text">{task.task}</p></details>
      <details className="detail-section"><summary>更换 Host 或取消目标</summary><fieldset className="workflow-controls" disabled={locked}>
        <label className="field"><span>新的 Host ID</span><input value={host} maxLength={256} onChange={e => setHost(e.target.value)} /></label>
        <button className="button" disabled={!host.trim() || host.trim() === value.hostId} onClick={() => void command("workflow_takeover", { expectedRevision: value.revision, expectedOwnerGeneration: value.ownerGeneration, newHostId: host.trim() })}>移交控制权</button>
        <p className="small muted">移交会使旧 Host 的操作资格失效，正在执行的回合不重启。新 Host 需取得自己的控制凭据。</p>
        {!["accepted", "cancelled"].includes(value.state) && <button className="button danger" onClick={() => void command("workflow_cancel", { reason: "用户在私有控制台取消目标及其协助任务" })}>取消目标及协助任务</button>}
      </fieldset></details>
      <button className="button small-button" disabled={state.busy} onClick={state.reload}>刷新协作记录</button>
      </div>
    </>}
    </div>
    {selectedSection === "artifacts" && value?.state === "delivered" && <fieldset className="workflow-controls acceptance-controls" disabled={locked || activeHelpers || value.task.shutdownConfirmed !== true || value.shutdown?.descendantsConfirmed !== true}>
      <legend>最终验收</legend><label className="field"><span>实际检查依据</span><textarea rows={2} value={note} maxLength={2000} onChange={e => setNote(e.target.value)} /></label>
      <div className="actions">{(["accepted", "rejected"] as const).map(verdict => <button key={verdict} className={`button ${verdict === "accepted" ? "primary" : ""}`} disabled={!note.trim() || !artifact}
        onClick={() => void command("workflow_acknowledge", { artifactId: artifact?.artifactId, verdict, note: note.trim() })}>{verdict === "accepted" ? "接受最终交付" : "记录验收问题"}</button>)}</div>
    </fieldset>}
  </div>;
}
