import { useEffect, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import type { DecisionAudit, DecisionModel } from "./decision-types";
import type { Preference } from "./types";
import { decisionStatus } from "./decision-types";
import { effortText, profileTitle } from "./profile-display";
import { Badge, formatDate } from "./ui";

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
  const constraints = audit.requested?.constraints || {};
  const preferences = table?.preferences || [];
  const selectedPreference = preferences.find(p => p.profileId === audit.profileId);
  const otherPreferences = preferences.filter(p => p !== selectedPreference);
  const preferenceItem = (p: Preference) => <li key={p.profileId}>
    <strong>{named(p.profileId)}</strong> · {{ prefer: "优先考虑", pin: "固定选择", exclude: "排除" }[p.mode]}{p.reason ? `：${p.reason}` : ""}
  </li>;
  const model = audit.decisionModel?.resolved || audit.decisionModel?.requested || audit.input?.profile;
  const reason = audit.reason || audit.error || "尚无决策依据。";
  return <section className="decision-detail" aria-label={audit.kind === "maintain" ? "评价整理详情" : "决策依据详情"}>
    <div className="row-between"><h3>{audit.kind === "maintain" ? "整理结果" : "选择依据"}</h3>
      <Badge tone={["failed", "needs-host", "stale", "cancelled"].includes(audit.status) ? "amber" : "neutral"}>
        {decisionStatus[audit.status] || audit.status}</Badge></div>
    <p className="read-text decision-reason">{reason.length > 360 ? reason.slice(0, 360) + "…" : reason}</p>
    {reason.length > 360 && <details><summary>展开完整依据</summary><p className="read-text">{reason}</p></details>}
    {audit.error && audit.error !== audit.reason && <p className="error-message">{audit.error}</p>}
    <dl className="facts">
      {audit.kind !== "maintain" && <><dt>选中配置</dt><dd>{configurationText(audit.selectedProfile)}</dd></>}
      <dt>决策模型</dt><dd>{configurationText(model)}</dd>
      <dt>评价表版本</dt><dd>V{audit.tableRevision}</dd>
      <dt>决策配置版本</dt><dd>{audit.configurationRevision == null ? "未记录" : `V${audit.configurationRevision}`}</dd>
      <dt>记录时间</dt><dd>{formatDate(audit.createdAt)}</dd>
      {audit.publishedRevision != null && <><dt>发布版本</dt><dd>V{audit.publishedRevision}</dd></>}
    </dl>
    {audit.kind !== "maintain" && <>
      <h3>当时的约束与偏好</h3>
      <p className="small muted">{Object.keys(constraints).length ? `硬约束：${configurationText(constraints)}` : "未记录指定配置的硬约束。"}</p>
      {!!audit.requested?.requiredCapabilities?.length && <p className="small wrap">所需能力：{audit.requested.requiredCapabilities.join("、")}</p>}
      {table ? preferences.length ? <>
        {selectedPreference && <ul className="reason-list">{preferenceItem(selectedPreference)}</ul>}
        {otherPreferences.length > 0 && <details><summary>其他候选偏好（{otherPreferences.length} 条）</summary>
          <ul className="reason-list">{otherPreferences.map(preferenceItem)}</ul></details>}
      </> : <p className="small muted">候选快照中没有用户偏好。</p> : <p className="small muted">本次没有保存模型输入快照，无法展示当时的偏好。</p>}
      <p className="small muted">仅展示这次决定保存的候选范围，不用当前模型卡片补写历史。</p>
    </>}
    {audit.kind === "maintain" && audit.proposal != null && <details className="detail-section"><summary>查看整理建议</summary>
      <p className="small muted">{audit.publishedRevision != null ? "已按记录的版本发布。" : "建议尚未发布。核对后可在模型卡片中编辑评价并发布。"}</p>
      <pre className="result-text">{JSON.stringify(audit.proposal, null, 2)}</pre></details>}
    {audit.noOp && <p className="small muted">本次无需发布新的评价版本。</p>}
    {table && <details className="detail-section"><summary>候选、评价与证据（{profiles.length} 个配置）</summary>
      <pre className="result-text">{JSON.stringify({ profiles, cards: table.cards, preferences: table.preferences, evidence: table.evidence }, null, 2)}</pre></details>}
    <details className="detail-section"><summary>记录标识与原始快照</summary>
      <dl className="facts"><dt>决策 ID</dt><dd>{audit.decisionId}</dd><dt>计算任务</dt><dd>{audit.runId || "未启动计算任务"}</dd>
        <dt>输入 SHA-256</dt><dd>{audit.inputSha256 || "未记录"}</dd></dl>
      <details><summary>发送给决策模型的快照</summary><pre className="result-text">{JSON.stringify(audit.input ?? null, null, 2)}</pre></details>
      <details><summary>持久模型回执</summary><pre className="result-text">{JSON.stringify(audit.output ?? null, null, 2)}</pre></details>
    </details>
  </section>;
}
