import { useEffect, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import type { DecisionAudit, DecisionModel } from "./decision-types";
import type { Preference } from "./types";
import { decisionStatus } from "./decision-types";
import { effortText, profileTitle } from "./profile-display";
import { Badge, formatDate } from "./ui";
import { fallbackDescription, recordedRoutingMode, routingBasisSummary } from "./routing-display";

export function configurationText(value: DecisionModel | null | undefined): string {
  return value ? [value.adapter, value.provider, value.model, effortText(value.effort ?? value.reasoningEffort)].filter(Boolean).join(" / ") || "未记录" : "未记录";
}

/** This view reads one persisted decision; viewing it never submits a new recommendation. */
export function DecisionDetails({ decisionId, api, csrfToken, active = true, refreshKey = "" }: {
  decisionId: string; api: ConsoleApi; csrfToken: string; active?: boolean; refreshKey?: string;
}) {
  const [record, setRecord] = useState<DecisionAudit | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!active) return;
    // Ignore late responses when another decision is selected or this pane closes.
    // https://react.dev/reference/react/useEffect#fetching-data-with-effects
    let current = true;
    setRecord(null); setError(""); setBusy(true);
    api.command<{ decision: DecisionAudit }>("selection_get", { decisionId, includeAudit: true }, csrfToken)
      .then(({ decision }) => {
        if (decision?.decisionId !== decisionId) throw new Error("决策记录与所选条目不匹配。");
        if (current) setRecord(decision);
      }).catch(reason => { if (current) setError(errorText(reason)); })
      .finally(() => { if (current) setBusy(false); });
    return () => { current = false; };
  }, [api, csrfToken, decisionId, active, refreshKey, retry]);
  // Also fence the render before the next effect clears the previous record.
  const audit = record?.decisionId === decisionId ? record : null;
  if (error) return <div role="alert" className="error-message">无法读取这次决定：{error}
    <button className="button small-button" onClick={() => setRetry(n => n + 1)}>重试读取</button></div>;
  if (!audit || busy) return <p role="status" className="muted">正在读取决策记录…</p>;
  const table = audit.input;
  const profiles = table?.profiles || [];
  const named = (id: string) => {
    const profile = profiles.find(p => p.profileId === id);
    return profile ? `${profile.adapter} · ${profileTitle(profile)}` : id;
  };
  const constraints = audit.constraints ?? audit.requested?.constraints ?? {};
  const requiredCapabilities = audit.requiredCapabilities ?? audit.requested?.requiredCapabilities ?? [];
  const basisLine = routingBasisSummary(audit.routingBasis);
  // A zero-candidate Host boundary never reached a Router either, even though
  // its request carries the frozen would-have-been mode and budget.
  const noRouterCall = audit.routerCalled === false || audit.routingBasis?.candidateCount === 0;
  const programPreferences = audit.routerCalled === false
    ? (audit.output as { programSelection?: { preferences?: Preference[] } } | null)?.programSelection?.preferences
    : undefined;
  const preferences = table?.preferences ?? programPreferences ?? [];
  const selectedPreference = preferences.find(p => p.profileId === audit.profileId);
  const otherPreferences = preferences.filter(p => p !== selectedPreference);
  const preferenceItem = (p: Preference) => <li key={p.profileId}>
    <strong>{named(p.profileId)}</strong> · {{ prefer: "优先考虑", pin: "固定选择", exclude: "排除" }[p.mode]}{p.reason ? `：${p.reason}` : ""}
  </li>;
  const model = audit.decisionModel?.resolved || audit.decisionModel?.requested || audit.input?.profile;
  const reason = audit.reason || audit.error || "未记录";
  const recorded = (value: unknown) => value == null ? "未记录" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  const outcomeText = (value: string | null | undefined) => value == null ? "未记录" : ({ matched: "符合", alternative: "选择其他配置", absent: "无偏好", none: "无偏好", fallback: "采用回退候选", "not-applicable": "不适用" }[value] ?? value);
  const output = audit.output as { status?: string; decision?: { profileId?: string | null; reason?: string; evidence?: unknown } } | null;
  const abstention = audit.status === "needs-host" && audit.profileId == null
    && output?.status === "ok" && output.decision?.profileId === null
    && typeof output.decision.reason === "string" && !!output.decision.reason.trim()
    && Array.isArray(output.decision.evidence)
    && Object.keys(output.decision).sort().join(",") === "evidence,profileId,reason";
  const evidence = Array.isArray(audit.evidence) && audit.evidence.every(entry =>
    entry != null && typeof entry === "object" && typeof entry.kind === "string" && typeof entry.ref === "string")
    ? audit.evidence : null;
  return <section className="decision-detail" aria-label={audit.kind === "maintain" ? "评价整理详情" : "决策依据详情"}>
    <div className="row-between"><h3>{audit.kind === "maintain" ? "整理结果" : "选择依据"}</h3>
      <Badge tone={!abstention && ["failed", "needs-host", "stale", "cancelled"].includes(audit.status) ? "amber" : "neutral"}>
        {abstention ? decisionStatus.abstention : decisionStatus[audit.status] || audit.status}</Badge></div>
    <p className="read-text decision-reason">{reason.length > 360 ? reason.slice(0, 360) + "…" : reason}</p>
    {reason.length > 360 && <details><summary>展开完整依据</summary><p className="read-text">{reason}</p></details>}
    {audit.error && audit.error !== audit.reason && <p className="error-message">{audit.error}</p>}
    <dl className="facts">
      {audit.kind !== "maintain" && <><dt>选中配置</dt><dd>{configurationText(audit.selectedProfile)}</dd></>}
      <dt>路由模型</dt><dd>{configurationText(model)}</dd>
      {audit.kind !== "maintain" && <><dt>请求模式</dt><dd>{recordedRoutingMode(audit.requestedRoutingMode)}</dd>
        <dt>实际模式</dt><dd>{noRouterCall ? "未调用 Router" : recordedRoutingMode(audit.routingMode)}</dd>
        <dt>模式降级</dt><dd>{fallbackDescription(audit.fallback)}</dd></>}
      <dt>评价表版本</dt><dd>V{audit.tableRevision}</dd>
      <dt>决策配置版本</dt><dd>{audit.configurationRevision == null ? "未记录" : `V${audit.configurationRevision}`}</dd>
      <dt>记录时间</dt><dd>{formatDate(audit.createdAt)}</dd>
      {audit.publishedRevision != null && <><dt>发布版本</dt><dd>V{audit.publishedRevision}</dd></>}
    </dl>
    {audit.kind !== "maintain" && <>
      <dl className="facts">
        <dt>程序用户偏好结果</dt><dd>{outcomeText(audit.policyCheck?.userPreference)}</dd>
        <dt>预算配置</dt><dd>{noRouterCall ? "未调用 Router" : audit.routingMode === "fast" ? "快速路由固定 60 秒，无工具" : audit.budget?.preset ? ({ brief: "简要", quick: "简要（历史记录）", standard: "标准", deep: "深入" }[audit.budget.preset] ?? audit.budget.preset) : "未记录"}</dd>
        <dt>耗时 / 上限</dt><dd>{recorded(audit.usage?.elapsedMs)} 毫秒 / {recorded(audit.budget?.timeoutSeconds)} 秒</dd>
        <dt>{audit.routingMode === "fast" ? "工具调用" : "工具调用 / 上限"}</dt><dd>{recorded(audit.usage?.toolCalls)}{audit.routingMode !== "fast" && ` / ${recorded(audit.budget?.toolCalls)}`}</dd>
        <dt>读取字节</dt><dd>{recorded(audit.usage?.bytesRead)}</dd>
      </dl>
      <h3>引用证据</h3>
      {evidence == null ? <p className="small muted">未记录</p> : evidence.length ? <ul className="reason-list">{evidence.map((entry, index) => <li key={index}>{entry.kind} · {entry.ref}</li>)}</ul> : <p className="small muted">无引用证据</p>}
      <details className="detail-section"><summary>原生身份、停止证据与输入核验</summary>
        <dl className="facts"><dt>原生身份</dt><dd><pre className="result-text">{recorded(audit.nativeIdentity)}</pre></dd>
          <dt>停止证据</dt><dd><pre className="result-text">{recorded(audit.stopEvidence)}</pre></dd>
          <dt>输入核验</dt><dd><pre className="result-text">{recorded(audit.inputVerification)}</pre></dd></dl>
      </details>
      <h3>当时的约束与偏好</h3>
      <p className="small muted">{Object.keys(constraints).length ? `硬约束：${configurationText(constraints)}` : "未记录指定配置的硬约束。"}</p>
      {!!requiredCapabilities.length && <p className="small wrap">所需能力：{requiredCapabilities.join("、")}</p>}
      {audit.routerCalled === false &&
        <p className="small muted">本次由程序直接选定：唯一合法候选，未调用 Router。</p>}
      {basisLine && <p className="small muted wrap">路由依据：{basisLine}（冻结记录，不随当前配置变化）</p>}
      {table || programPreferences ? preferences.length ? <>
        {selectedPreference && <ul className="reason-list">{preferenceItem(selectedPreference)}</ul>}
        {otherPreferences.length > 0 && <details><summary>其他候选偏好（{otherPreferences.length} 条）</summary>
          <ul className="reason-list">{otherPreferences.map(preferenceItem)}</ul></details>}
      </> : <p className="small muted">候选快照无用户偏好</p> : <p className="small muted">输入快照未记录</p>}
    </>}
    {audit.kind === "maintain" && audit.proposal != null && <details className="detail-section"><summary>查看整理建议</summary>
      <p className="small muted">{audit.publishedRevision != null ? "已发布" : "未发布"}</p>
      <pre className="result-text">{JSON.stringify(audit.proposal, null, 2)}</pre></details>}
    {audit.noOp && <p className="small muted">无新版本</p>}
    {table && <details className="detail-section"><summary>候选、评价与证据（{profiles.length} 个配置）</summary>
      <pre className="result-text">{JSON.stringify({ profiles, cards: table.cards, preferences: table.preferences, evidence: table.evidence }, null, 2)}</pre></details>}
    <details className="detail-section"><summary>记录标识与原始快照</summary>
      <dl className="facts"><dt>决策 ID</dt><dd>{audit.decisionId}</dd><dt>计算任务</dt><dd>{audit.runId || "未启动计算任务"}</dd>
        <dt>输入 SHA-256</dt><dd>{audit.inputSha256 || "未记录"}</dd></dl>
      <details><summary>发送给路由模型的快照</summary><pre className="result-text">{JSON.stringify(audit.input ?? null, null, 2)}</pre></details>
      <details><summary>{audit.routerCalled === false ? "程序选择记录" : "持久模型回执"}</summary><pre className="result-text">{JSON.stringify(audit.output ?? null, null, 2)}</pre></details>
    </details>
  </section>;
}
