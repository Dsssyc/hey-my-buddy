import { useState } from "react";
import type { ConsoleView, RoutingBudget, RoutingHealth, RoutingMode, Snapshot } from "./types";
import type { Editor } from "./use-editor";
import { Badge, Help } from "./ui";
import { dayClock } from "./objective-display";
import { profileTitle } from "./profile-display";
import { isDecisionCandidate, isFastRouterCandidate, routerAttention } from "./policy";

export const BUDGET_LABEL: Record<RoutingBudget, string> = { brief: "简要", standard: "标准", deep: "深入" };
const BUDGET_HELP = "审阅路由的预算档位。快速路由固定 60 秒，不调用工具。保存后用于后续路由。";
const MODE_LABEL: Record<RoutingMode, string> = { fast: "快速", review: "审阅" };

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
  const routerIds = { fast: data.configuration.fastRouterProfileId, review: data.configuration.reviewRouterProfileId };
  const routers = Object.fromEntries((["fast", "review"] as RoutingMode[]).map(mode =>
    [mode, routerIds[mode] ? data.profiles.find(p => p.profileId === routerIds[mode]) : undefined])) as Record<RoutingMode, typeof data.profiles[number] | undefined>;
  const name = (mode: RoutingMode) => routers[mode] ? profileTitle(routers[mode]!) : routerIds[mode] || "尚未指定，请选择";
  const candidate = (mode: RoutingMode) => mode === "fast" ? isFastRouterCandidate(routers[mode]) : isDecisionCandidate(routers[mode]);
  const defaultMode = data.configuration.defaultRoutingMode;
  const budget = data.configuration.routingBudget ?? "standard";
  const routerDirty = (mode: RoutingMode) => !!editor.draft && snapshot.configuration[mode === "fast" ? "fastRouterProfileId" : "reviewRouterProfileId"] !== routerIds[mode];
  const modeDirty = !!editor.draft && snapshot.configuration.defaultRoutingMode !== defaultMode;
  const budgetDirty = !!editor.draft && (snapshot.configuration.routingBudget ?? "standard") !== budget;
  const health = healthSummary(snapshot.routingHealth);
  const warnings = (["fast", "review"] as RoutingMode[]).map(mode => !routerIds[mode]
    ? `尚未指定${MODE_LABEL[mode]} Router：请在档位菜单中选择。`
    : routerAttention(data, mode)?.message).filter(Boolean);
  const warning = warnings.join(" ") || health.warning;
  function setBudget(value: RoutingBudget) {
    editor.update(d => ({ ...d, configuration: { ...d.configuration, routingBudget: value } }));
  }
  function setMode(value: RoutingMode) {
    editor.update(d => ({ ...d, configuration: { ...d.configuration, defaultRoutingMode: value } }));
  }
  return <section className={"routing-status-bar" + (warning ? " warning" : "")} aria-label="路由状态">
    <div className="routing-status-line">
      {warning && <p className="routing-warning"><span aria-hidden="true">⚠ </span>{warning}</p>}
      <span className="routing-status-facts">
        {(["fast", "review"] as RoutingMode[]).map(mode => <span key={mode}>{MODE_LABEL[mode]} Router：<strong>{name(mode)}</strong>{routerDirty(mode) && <span className="unsaved-mark">未保存</span>}</span>)}
        <span aria-hidden="true" className="routing-sep">｜</span>
        <span>默认模式：<strong>{MODE_LABEL[defaultMode]}</strong>{modeDirty && <span className="unsaved-mark">未保存</span>}</span>
        <span aria-hidden="true" className="routing-sep">｜</span>
        <span>审阅预算：{BUDGET_LABEL[budget]}{budgetDirty && <span className="unsaved-mark">未保存</span>}</span>
        <span aria-hidden="true" className="routing-sep">｜</span>
        <span>状态：{health.text}</span>
      </span>
      <button type="button" className="button small-button" aria-expanded={open} aria-controls="routing-details"
        onClick={() => setOpen(value => !value)}>详情</button>
    </div>
    <div id="routing-details" className="routing-details" hidden={!open}>
      <div className="routing-detail-block">
        <h3>Router 位置 <Help label="Router 说明">快速 Router 需要所属 Harness 支持无工具路由调用；审阅 Router 需要当前 Harness 版本已验证只读路由调用。两者都需要已启用且可用，在对应档位的菜单中设置。</Help></h3>
        {(["fast", "review"] as RoutingMode[]).map(mode => <p className="router-line" key={mode}>
          <span>{MODE_LABEL[mode]} Router：</span><strong>{name(mode)}</strong>
          {routerIds[mode] && <Badge tone={candidate(mode) ? "green" : "amber"}>{candidate(mode) ? "可担任" : "需要处理"}</Badge>}
          {routers[mode] && <button type="button" className="button small-button" onClick={() => onShowRouter(routers[mode]!.profileId)}>查看所在家族</button>}
        </p>)}
      </div>
      <div className="routing-detail-block">
        <h3>默认模式</h3>
        <div className="segmented" role="radiogroup" aria-label="默认路由模式">
          {(["fast", "review"] as RoutingMode[]).map(mode => <label key={mode} className={"segment" + (defaultMode === mode ? " checked" : "")}>
            <input type="radio" name="default-routing-mode" checked={defaultMode === mode} disabled={!editor.editing} onChange={() => setMode(mode)} />{MODE_LABEL[mode]}
          </label>)}
        </div>
        <p className="small muted">快速路由固定 60 秒，不调用工具。快速路由会把每个任务的描述发送给快速 Router 所在的模型提供方，包括准备交给其他模型执行的任务；审阅路由还会读取冻结的仓库副本。</p>
      </div>
      <div className="routing-detail-block">
        <h3>审阅预算 <Help label="路由预算说明">{BUDGET_HELP}</Help></h3>
        <div className="segmented" role="radiogroup" aria-label="审阅预算">
          {(Object.keys(BUDGET_LABEL) as RoutingBudget[]).map(value => <label key={value}
            className={"segment" + (budget === value ? " checked" : "")}>
            <input type="radio" name="routing-budget" value={value} checked={budget === value}
              disabled={!editor.editing} onChange={() => setBudget(value)} />
            {BUDGET_LABEL[value]}
          </label>)}
        </div>
        {snapshot.configuration.routingBudgetLimits && <p className="small muted" aria-label="审阅预算上限">
          当前记录：{BUDGET_LABEL[snapshot.configuration.routingBudgetLimits.preset]} {snapshot.configuration.routingBudgetLimits.timeoutSeconds} 秒 / {snapshot.configuration.routingBudgetLimits.toolCalls} 次工具调用
        </p>}
      </div>
      <RoutingHealthDetails health={snapshot.routingHealth} />
    </div>
  </section>;
}
