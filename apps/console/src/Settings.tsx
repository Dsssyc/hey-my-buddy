import type { RoutingBudget, RoutingHealth, Snapshot } from "./types";
import type { ConsoleApi } from "./api";
import type { Editor } from "./use-editor";
import { Badge, Icon } from "./ui";
import { dayClock } from "./objective-display";
import { effortText, profileTitle, profileTitleOr } from "./profile-display";
import { decisionAttention, decisionCandidates, hasDecisionCapability } from "./policy";
import { StoragePanel } from "./StoragePanel";

/**
 * Read-only routing status (0.15 R4): the snapshot's bounded selection-health
 * window. Abstentions, cancellations and stale results are reported as
 * themselves, never as model failures; absent data is unknown, not zero. This
 * section never invokes a model, works in read-only sessions, and carries no
 * write control of any kind.
 */
function RoutingStatus({ health }: { health: RoutingHealth | undefined }) {
  const routingOutcomes = health ? [
    health.budgetExhaustedCount == null ? null : `预算耗尽 ${health.budgetExhaustedCount} 次`,
    health.boundsRejectedCount == null ? null : `边界检查拒绝 ${health.boundsRejectedCount} 次`,
    health.inputChangedCount == null ? null : `输入已变化 ${health.inputChangedCount} 次`,
  ].filter((entry) => entry !== null) : [];
  return <div className="routing-status" aria-label="路由状态">
    <h3>路由状态</h3>
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
          {health.recentFailures.length > 0 && <ul className="routing-failures">
            {health.recentFailures.map(failure => <li key={failure.decisionId}>
              <span>{dayClock(failure.at)}</span>
              <code>{failure.code}</code>
              {failure.runId ? <span className="muted">{failure.runId}</span> : <span className="muted">委派未记录</span>}
            </li>)}
          </ul>}
          <p className="small muted">只读统计，读取不触发模型；最近的失败记录最多列出 5 条。</p>
        </>}
  </div>;
}

export function Settings({
  snapshot,
  editor,
  api,
  connectionError = "",
}: {
  snapshot: Snapshot;
  editor: Editor;
  api: ConsoleApi;
  connectionError?: string;
}) {
  const data = editor.draft || snapshot,
    editing = editor.editing;
  const budget = data.configuration.routingBudget ?? "standard";
  const modelDirty = !!editor.draft && snapshot.configuration.decisionProfileId !== editor.draft.configuration.decisionProfileId;
  const budgetDirty = !!editor.draft && (snapshot.configuration.routingBudget ?? "standard") !== budget;
  const currentId = data.configuration.decisionProfileId;
  const current = data.profiles.find((p) => p.profileId === currentId);
  const publishedId = snapshot.configuration.decisionProfileId;
  const publishedCurrent = snapshot.profiles.find(
    (p) => p.profileId === publishedId,
  );
  const draftName = current ? profileTitle(current) : currentId || "尚未配置";
  const publishedName = publishedCurrent
    ? profileTitle(publishedCurrent)
    : publishedId || "尚未配置";
  // Candidates must be enabled, currently available and declare the `decision`
  // capability Host reports; coding ability alone is not enough.
  const candidates = decisionCandidates(data.profiles);
  const currentIsCandidate = candidates.some((p) => p.profileId === currentId);
  const attention = decisionAttention(data);
  const ineligible = data.profiles.filter(
    (p) => p.enabled && !p.available && hasDecisionCapability(p),
  ).length;
  return (
    <div className="settings-page">
      <section className="panel settings-panel">
        <div className="panel-heading">
          <h2>路由模型</h2>
          <p className="muted">
            选择一个经过验证支持只读结构化回合的路由模型，用于检查委派输入并选择执行配置。它不会递归选择自己，也不承担评价整理；评价更新由配置了 hey-my-buddy skill 的 Harness 执行。
          </p>
        </div>
        <label className="field">
          <span>路由模型配置{modelDirty && <span className="unsaved-mark">未保存</span>}</span>
          <select
            disabled={!editing}
            value={currentId || ""}
            onChange={(e) =>
              editor.setDraft((d) =>
                d
                  ? {
                      ...d,
                      configuration: {
                        ...d.configuration,
                        decisionProfileId: e.target.value || null,
                      },
                    }
                  : d,
              )
            }
          >
            <option value="">尚未配置</option>
            {/* A stale configured value stays visible without becoming a selectable candidate. */}
            {currentId && !currentIsCandidate && (
              <option value={currentId} disabled>
                {profileTitleOr(current, currentId)}（需要处理）
              </option>
            )}
            {candidates.map((p) => (
              <option key={p.profileId} value={p.profileId}>
                {profileTitle(p)}
              </option>
            ))}
          </select>
        </label>
        <p className="small muted">
          只有已启用、目录可用且经过验证支持只读结构化回合的配置才会成为候选；{candidates.length} 个候选
          {ineligible > 0 ? ` · ${ineligible} 个已启用配置当前不可用` : ""}。
        </p>
        {candidates.length === 0 && (
          <p className="small muted">
            还没有经过验证的路由模型。
          </p>
        )}
        {attention && (
          <p className="banner attention-banner" role="status">
            {attention.message}
          </p>
        )}
        {current ? (
          <div className="configuration-summary">
            <div className="profile-avatar">
              {current.adapter.slice(0, 1).toUpperCase()}
            </div>
            <div>
              <strong>{current.model}</strong>
              <p className="small muted">
                {[current.provider, effortText(current.effort)]
                  .filter(Boolean)
                  .join(" / ")}
              </p>
            </div>
            <Badge tone={currentIsCandidate ? "green" : "amber"}>
              {currentIsCandidate ? "路由可用" : "路由不可用"}
            </Badge>
          </div>
        ) : currentId ? (
          <div className="quiet-note">
            <Icon name="models" />
            <p>
              当前路由配置 {currentId} 已不在目录中。选择一个可用的候选配置，或等待目录再次发现它；保存其他修改不受影响。
            </p>
          </div>
        ) : (
          <div className="quiet-note">
            <Icon name="models" />
            <p>
              选择经过验证的路由模型后，保存并发布即可生效。查看页面不会发起模型请求。
            </p>
          </div>
        )}
        {editor.mode && modelDirty && (
          <p className="small muted" role="status">
            草稿中的路由模型为 {draftName}，已发布的是 {publishedName}。保存并发布后才会生效；正在运行的任务继续使用原配置。
          </p>
        )}
        <label className="field">
          <span>路由预算{budgetDirty && <span className="unsaved-mark">未保存</span>}</span>
          <select disabled={!editing} value={budget} onChange={e => editor.setDraft(d => d ? {
            ...d, configuration: { ...d.configuration, routingBudget: e.target.value as RoutingBudget },
          } : d)}>
            <option value="quick">快速</option><option value="standard">标准</option><option value="deep">深入</option>
          </select>
        </label>
        <p className="small muted">上限待实测：快速 60 秒 / 8 次工具调用；标准 300 秒 / 24 次工具调用；深入 600 秒 / 64 次工具调用。保存并发布后用于后续路由。</p>
        <div className="policy-note">
          <h3>用户偏好与事实分别保存</h3>
          <p>
            明确指定、优先考虑和排除各有含义。偏好影响后续选择，不会改写已有验收，也不会改变运行中任务的配置。保存只提交你实际修改的字段。
          </p>
        </div>
        <RoutingStatus health={snapshot.routingHealth} />
      </section>
      <StoragePanel api={api} csrfToken={snapshot.csrfToken} connectionError={connectionError} />
    </div>
  );
}
