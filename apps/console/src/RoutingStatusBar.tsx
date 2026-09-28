import { useState } from "react";
import type { ConsoleView, RoutingBudget, RoutingHealth, Snapshot } from "./types";
import type { Editor } from "./use-editor";
import { Badge, Help } from "./ui";
import { dayClock } from "./objective-display";
import { profileTitle } from "./profile-display";
import { decisionAttention, decisionCandidates, isDecisionCandidate } from "./policy";

export const BUDGET_LABEL: Record<RoutingBudget, string> = { quick: "快速", standard: "标准", deep: "深入" };
const BUDGET_HELP = "上限待实测：快速 60 秒 / 8 次工具调用；标准 300 秒 / 24 次工具调用；深入 600 秒 / 64 次工具调用。保存后用于后续路由。";

/**
 * Health in one word for the status row, plus the warning when the recorded
 * window says routing is currently failing. Absent data is unknown, never
 * success; abstentions, cancellations and stale results are not failures.
 */
export function healthSummary(health: RoutingHealth | undefined): { text: string; warning: string } {
  if (!health) return { text: "未知", warning: "" };
  if (health.sampleCount === 0) return { text: "暂无样本", warning: "" };
  if (health.consecutiveFailures > 0) {
    return {
      text: `连续失败 ${health.consecutiveFailures} 次`,
      warning: `路由最近连续失败 ${health.consecutiveFailures} 次：请在“详情”中查看失败明细；持续失败时更换 Router。`,
    };
  }
  return { text: "正常", warning: "" };
}

/**
 * Read-only routing health (0.15 R4): the snapshot's bounded selection-health
 * window. It never invokes a model, works in read-only sessions and carries no
 * write control of any kind.
 */
export function RoutingHealthDetails({ health }: { health: RoutingHealth | undefined }) {
  const routingOutcomes = health ? [
    health.budgetExhaustedCount == null ? null : `预算耗尽 ${health.budgetExhaustedCount} 次`,
    health.boundsRejectedCount == null ? null : `边界检查拒绝 ${health.boundsRejectedCount} 次`,
    health.inputChangedCount == null ? null : `输入已变化 ${health.inputChangedCount} 次`,
  ].filter((entry) => entry !== null) : [];
  return <div className="routing-status" aria-label="路由健康">
    <h3>路由健康 <Help label="路由健康说明">只读统计，读取不触发模型；弃权、取消和过期结果不计为失败；最近的失败记录最多列出 5 条。</Help></h3>
    {!health
      ? <p className="muted">路由摘要暂不可用。</p>
      : health.sampleCount === 0
        ? <p className="muted">窗口内暂无已记录样本（窗口上限 {health.windowSize} 次），没有可报告的成功或失败。</p>
        : <>
          <p className="small">
            最近 {health.sampleCount} 次中失败 {health.failureCount} 次（窗口上限 {health.windowSize} 次）
            {health.consecutiveFailures > 0 ? ` · 连续失败 ${health.consecutiveFailures} 次` : ""}
          </p>
          <p className="small muted">
            {health.abstentionCount > 0 ? `弃权 ${health.abstentionCount} 次` : ""}
            {health.cancelledCount > 0 ? `${health.abstentionCount > 0 ? " · " : ""}取消 ${health.cancelledCount} 次` : ""}
            {health.staleCount > 0 ? ` · 过期 ${health.staleCount} 次` : ""}
            {(health.abstentionCount > 0 || health.cancelledCount > 0 || health.staleCount > 0) ? "，不计为失败" : "无弃权、取消或过期结果"}
          </p>
          {routingOutcomes.length > 0 && <p className="small muted">{routingOutcomes.join(" · ")}</p>}
          <p className="small">
            最后一次成功：{health.lastSuccessAt ? `${dayClock(health.lastSuccessAt)}${health.lastSuccessDecisionId ? ` · ${health.lastSuccessDecisionId}` : ""}` : "无成功记录"}
          </p>
          {health.recentFailures.length > 0 && <ul className="routing-failures" aria-label="失败明细">
            {health.recentFailures.map(failure => <li key={failure.decisionId}>
              <span>{dayClock(failure.at)}</span>
              <code>{failure.code}</code>
              {failure.runId ? <span className="muted">{failure.runId}</span> : <span className="muted">委派未记录</span>}
            </li>)}
          </ul>}
        </>}
  </div>;
}

/**
 * The one-line global routing state of the Buddy settings page: Router, budget
 * and health. It turns into a warning with the resolving action only when the
 * user has something to do. "详情" gathers everything routing: the current
 * Router (with a jump to its family), the budget choice and the health window.
 * The Router itself is chosen on an effort tag, never here.
 */
export function RoutingStatusBar({ data, snapshot, editor, onShowRouter }: {
  data: ConsoleView; snapshot: Snapshot; editor: Editor;
  onShowRouter: (profileId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const routerId = data.configuration.decisionProfileId;
  const router = routerId ? data.profiles.find(p => p.profileId === routerId) : undefined;
  const routerName = router ? profileTitle(router) : routerId || "尚未指定";
  const budget = data.configuration.routingBudget ?? "standard";
  const routerDirty = !!editor.draft && snapshot.configuration.decisionProfileId !== routerId;
  const budgetDirty = !!editor.draft && (snapshot.configuration.routingBudget ?? "standard") !== budget;
  const attention = decisionAttention(data);
  const health = healthSummary(snapshot.routingHealth);
  const candidates = decisionCandidates(data.profiles).length;
  const warning = !routerId
    ? "尚未指定 Router：请在具备 decision 能力的档位菜单中选择“设为 Router”。"
    : attention?.message || health.warning;
  function setBudget(value: RoutingBudget) {
    editor.update(d => ({ ...d, configuration: { ...d.configuration, routingBudget: value } }));
  }
  return <section className={"routing-status-bar" + (warning ? " warning" : "")} aria-label="路由状态">
    <div className="routing-status-line">
      {warning && <p className="routing-warning"><span aria-hidden="true">⚠ </span>{warning}</p>}
      <span className="routing-status-facts">
        <span>Router：<strong>{routerName}</strong>{routerDirty && <span className="unsaved-mark">未保存</span>}</span>
        <span aria-hidden="true" className="routing-sep">｜</span>
        <span>预算：{BUDGET_LABEL[budget]}{budgetDirty && <span className="unsaved-mark">未保存</span>}</span>
        <span aria-hidden="true" className="routing-sep">｜</span>
        <span>状态：{health.text}</span>
      </span>
      <button type="button" className="button small-button" aria-expanded={open} aria-controls="routing-details"
        onClick={() => setOpen(value => !value)}>详情</button>
    </div>
    <div id="routing-details" className="routing-details" hidden={!open}>
      <div className="routing-detail-block">
        <h3>当前 Router <Help label="Router 说明">Router 是某个档位上的角色：在具备 decision 能力的档位标签菜单中选择“设为 Router”。只有已启用、可用且能力列表含 decision 的档位才能担任；当前 {candidates} 个候选。它不会递归选择自己，也不承担评价整理。</Help></h3>
        {routerId ? <p className="router-line">
          <strong>{routerName}</strong>
          <Badge tone={isDecisionCandidate(router) ? "green" : "amber"}>{isDecisionCandidate(router) ? "可担任" : "需要处理"}</Badge>
          {router && <button type="button" className="button small-button" onClick={() => onShowRouter(router.profileId)}>查看所在家族</button>}
        </p> : <p className="muted">尚未指定</p>}
      </div>
      <div className="routing-detail-block">
        <h3>路由预算 <Help label="路由预算说明">{BUDGET_HELP}</Help></h3>
        <div className="segmented" role="radiogroup" aria-label="路由预算">
          {(Object.keys(BUDGET_LABEL) as RoutingBudget[]).map(value => <label key={value}
            className={"segment" + (budget === value ? " checked" : "")}>
            <input type="radio" name="routing-budget" value={value} checked={budget === value}
              disabled={!editor.editing} onChange={() => setBudget(value)} />
            {BUDGET_LABEL[value]}
          </label>)}
        </div>
      </div>
      <RoutingHealthDetails health={snapshot.routingHealth} />
    </div>
  </section>;
}
