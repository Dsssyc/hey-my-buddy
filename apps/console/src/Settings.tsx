import type { Snapshot } from "./types";
import type { Editor } from "./use-editor";
import { Badge, Icon } from "./ui";
import { effortText, profileTitle, profileTitleOr } from "./profile-display";
import { decisionAttention, decisionCandidates, hasDecisionCapability } from "./policy";

export function Settings({
  snapshot,
  editor,
}: {
  snapshot: Snapshot;
  editor: Editor;
}) {
  const data = editor.draft || snapshot,
    editing = editor.editing;
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
          <h2>固定决策模型</h2>
          <p className="muted">
            固定一个决策配置，用于在智能路由中比较候选执行配置。它不会递归选择自己，也不承担评价整理；评价更新由配置了 hey-my-buddy skill 的 Harness 执行。
          </p>
        </div>
        <label className="field">
          <span>决策模型配置{editor.configurationDirty && <span className="unsaved-mark">未保存</span>}</span>
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
          只有已启用、目录可用且声明决策能力的配置才会成为候选；{candidates.length} 个候选
          {ineligible > 0 ? ` · ${ineligible} 个已启用配置当前不可用` : ""}。
        </p>
        {candidates.length === 0 && (
          <p className="small muted">
            当前没有候选：请先启用一个目录可用且声明决策能力的配置；若目录资料尚未更新，可在“模型卡片”执行一次模型发现。
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
            <Badge tone={current.available ? "green" : "amber"}>
              {current.available ? "目录可用" : "目录不可用"}
            </Badge>
          </div>
        ) : currentId ? (
          <div className="quiet-note">
            <Icon name="models" />
            <p>
              当前决策配置 {currentId} 已不在目录中。选择一个可用的候选配置，或等待目录再次发现它；保存其他修改不受影响。
            </p>
          </div>
        ) : (
          <div className="quiet-note">
            <Icon name="models" />
            <p>
              先在“模型卡片”发现配置，再选择决策模型。查看页面不会发起模型请求。
            </p>
          </div>
        )}
        {editing && editor.configurationDirty && (
          <p className="small muted" role="status">
            草稿中的决策模型为 {draftName}，已发布的是 {publishedName}。保存并发布后才会生效；正在运行的任务继续使用原配置。
          </p>
        )}
        <div className="policy-note">
          <h3>用户偏好与事实分别保存</h3>
          <p>
            明确指定、优先考虑和排除各有含义。偏好影响后续选择，不会改写已有验收，也不会改变运行中任务的配置。保存只提交你实际修改的字段。
          </p>
        </div>
      </section>
    </div>
  );
}
