import { useState } from "react";
import type { ConsoleView, RoutingBudget, RoutingHealth, RoutingMode, Snapshot } from "./types";
import type { Editor } from "./use-editor";
import { Badge, Help } from "./ui";
import { dayClock } from "./objective-display";
import { profileTitle } from "./profile-display";
import { isDecisionCandidate, isFastRouterCandidate, routerAttention } from "./policy";

export const BUDGET_LABEL: Record<RoutingBudget, string> = { brief: "简要", standard: "标准", deep: "深入" };
const BUDGET_HELP = "审阅预算用于后续路由；快速路由固定 60 秒。";
const MODE_LABEL: Record<RoutingMode, string> = { fast: "快速", review: "审阅" };

/**
 * Health in one word for the status row, plus the warning when the recorded
 * window says routing is currently failing. Absent data is unknown, never
 * success; abstentions, cancellations and stale results are not failures.
 */
export function healthSummary(health: RoutingHealth | undefined): { text: string; warning: string } {
  if (!health) return { text: "未知", warning: "" };
  if (health.available === false) return { text: "不可用", warning: `Router 当前不可用${health.reasonCode ? `（${health.reasonCode}）` : ""}；请查看详情，必要时更换 Router。` };
  if (health.sampleCount === 0) return { text: "暂无样本", warning: "" };
  if (health.consecutiveFailures > 0) {
    return {
      text: `连续失败 ${health.consecutiveFailures} 次`,
      warning: `路由连续失败 ${health.consecutiveFailures} 次；请查看详情，必要时更换 Router。`,
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
  return <div className="routing-status" aria-label="路由健康" tabIndex={-1}>
    <h3>路由健康 <Help label="路由健康说明">弃权、取消和过期不计为失败；显示最近 5 条失败。</Help></h3>
    {health?.available === false && <p className="small">Router 当前不可用{health.reasonCode ? `（${health.reasonCode}）` : ""}</p>}
    {!health
      ? <p className="muted">路由摘要未知</p>
      : health.sampleCount === 0
        ? <p className="muted">最近 {health.windowSize} 次内暂无样本</p>
        : <>
          <p className="small">
            最近 {health.sampleCount} 次中失败 {health.failureCount} 次（窗口上限 {health.windowSize} 次）
            {health.consecutiveFailures > 0 ? ` · 连续失败 ${health.consecutiveFailures} 次` : ""}
          </p>
          {(health.abstentionCount > 0 || health.cancelledCount > 0 || health.staleCount > 0) && <p className="small muted">
            {health.abstentionCount > 0 ? `弃权 ${health.abstentionCount} 次` : ""}
            {health.cancelledCount > 0 ? `${health.abstentionCount > 0 ? " · " : ""}取消 ${health.cancelledCount} 次` : ""}
            {health.staleCount > 0 ? `${health.abstentionCount > 0 || health.cancelledCount > 0 ? " · " : ""}过期 ${health.staleCount} 次` : ""}
          </p>}
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
export function RoutingStatusBar({ data, snapshot, editor, onShowRouter, expanded = false }: {
  data: ConsoleView; snapshot: Snapshot; editor: Editor;
  onShowRouter: (profileId: string) => void;
  expanded?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const configuration = snapshot.configuration === null ? null : data.configuration;
  const routerId = configuration?.routerProfileId;
  const router = data.profiles.find(p => p.profileId === routerId);
  const name = router ? profileTitle(router) : routerId || (configuration ? "尚未指定，请选择" : "升级不可用");
  const defaultMode = configuration?.defaultRoutingMode;
  const candidate = defaultMode === "fast" ? isFastRouterCandidate(router) : isDecisionCandidate(router);
  const budget = configuration?.routingBudget;
  const routerDirty = !!editor.draft && snapshot.configuration?.routerProfileId !== routerId;
  const modeDirty = !!editor.draft && snapshot.configuration?.defaultRoutingMode !== defaultMode;
  const budgetDirty = !!editor.draft && snapshot.configuration?.routingBudget !== budget;
  const health = healthSummary(snapshot.routingHealth);
  const warning = !configuration ? `Router 设置升级不可用${snapshot.configurationError?.message ? `：${snapshot.configurationError.message}` : ""}`
    : !routerId ? "未指定 Router；请在档位菜单中选择。"
      : routerAttention(data)?.message || health.warning;
  function setBudget(value: RoutingBudget) {
    editor.update(d => d.configuration ? ({ ...d, configuration: { ...d.configuration, routingBudget: value } }) : d);
  }
  function setMode(value: RoutingMode) {
    editor.update(d => d.configuration ? ({ ...d, configuration: { ...d.configuration, defaultRoutingMode: value } }) : d);
  }
  return <section className={"routing-status-bar" + (warning ? " warning" : "")} aria-label="路由状态">
    <div className="routing-status-line">
      {expanded && <h2>Router</h2>}
      {!expanded && warning && <p className="routing-warning"><span aria-hidden="true">⚠ </span>{warning}</p>}
      {!expanded && <span className="routing-status-facts">
        <span>Router：<strong>{name}</strong>{routerDirty && <span className="unsaved-mark">未保存</span>}</span>
        <span aria-hidden="true" className="routing-sep">｜</span>
        <span>默认模式：<strong>{defaultMode ? MODE_LABEL[defaultMode] : "升级不可用"}</strong>{modeDirty && <span className="unsaved-mark">未保存</span>}</span>
        <span aria-hidden="true" className="routing-sep">｜</span>
        <span>审阅预算：{budget ? BUDGET_LABEL[budget] : "升级不可用"}{budgetDirty && <span className="unsaved-mark">未保存</span>}</span>
        <span aria-hidden="true" className="routing-sep">｜</span>
        <span>状态：{health.text}</span>
      </span>}
      {!expanded && <button type="button" className="button small-button" aria-expanded={open} aria-controls="routing-details"
        onClick={() => setOpen(value => !value)}>详情</button>
      }
    </div>
    <div id="routing-details" className="routing-details" hidden={!open && !expanded}>
      <div className="routing-detail-block">
        <h3>Router <Help label="Router 说明">快速模式需支持无工具调用；审阅模式需具备本地只读资格。请在已启用档位的菜单中设置。</Help></h3>
        {!configuration && <p className="small" role="status">{warning}</p>}
        <p className="router-line" id="router-current" tabIndex={-1}>
          <span>Router：</span><strong>{name}</strong>
          {routerId && <Badge tone={candidate ? "green" : "amber"}>{candidate ? "可担任" : "需要处理"}</Badge>}
          {router && <button type="button" className="button small-button" onClick={() => onShowRouter(router.profileId)}>查看所在家族</button>}
        </p>
      </div>
      <div className="routing-detail-block">
        <h3>默认模式 <Help label="路由数据流向">需要 Router 判断时，任务包发送给 Router 的模型提供方；审阅模式还发送冻结代码副本。单一候选由程序选择。DSH/ZCode 无系统沙盒，不能保证阻止副本外读取或外传。</Help></h3>
        <div className="segmented" role="radiogroup" aria-label="默认路由模式">
          {(["fast", "review"] as RoutingMode[]).map(mode => <label key={mode} className={"segment" + (defaultMode === mode ? " checked" : "")}>
            <input type="radio" name="default-routing-mode" checked={defaultMode === mode} disabled={!editor.editing || !configuration} onChange={() => setMode(mode)} />{MODE_LABEL[mode]}
          </label>)}
        </div>
      </div>
      <div className="routing-detail-block">
        <h3>审阅预算 <Help label="路由预算说明">{BUDGET_HELP}{snapshot.configuration?.routingBudgetLimits && ` 当前记录：${BUDGET_LABEL[snapshot.configuration?.routingBudgetLimits.preset]} ${snapshot.configuration?.routingBudgetLimits.timeoutSeconds} 秒 / ${snapshot.configuration?.routingBudgetLimits.toolCalls} 次工具调用。`}</Help></h3>
        <div className="segmented" role="radiogroup" aria-label="审阅预算">
          {(Object.keys(BUDGET_LABEL) as RoutingBudget[]).map(value => <label key={value}
            className={"segment" + (budget === value ? " checked" : "")}>
            <input type="radio" name="routing-budget" value={value} checked={budget === value}
              disabled={!editor.editing || !configuration} onChange={() => setBudget(value)} />
            {BUDGET_LABEL[value]}
          </label>)}
        </div>
      </div>
      <RoutingHealthDetails health={snapshot.routingHealth} />
    </div>
  </section>;
}
