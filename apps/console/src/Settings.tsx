import type { Snapshot } from "./types";
import type { Editor } from "./use-editor";
import { Badge, Icon } from "./ui";
import { effortText, profileTitle } from "./profile-display";

export function Settings({
  snapshot,
  editor,
}: {
  snapshot: Snapshot;
  editor: Editor;
}) {
  const data = editor.draft || snapshot,
    editing = editor.hasAuthority && !editor.busy && !editor.uncertain;
  const current = data.profiles.find(
    (p) => p.profileId === data.configuration.decisionProfileId,
  );
  return (
    <div className="settings-page">
      <section className="panel settings-panel">
        <div className="panel-heading">
          <h2>固定决策模型</h2>
          <p className="muted">
            固定一个决策配置，负责比较候选与整理经验。它不会递归选择自己。
          </p>
        </div>
        <label className="field">
          <span>决策模型配置</span>
          <select
            disabled={!editing}
            value={data.configuration.decisionProfileId || ""}
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
            {data.profiles
              .filter((p) => p.enabled)
              .map((p) => (
                <option key={p.profileId} value={p.profileId}>
                  {profileTitle(p)}
                </option>
              ))}
          </select>
        </label>
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
              {current.available ? "目录可用" : "待确认"}
            </Badge>
          </div>
        ) : (
          <div className="quiet-note">
            <Icon name="models" />
            <p>
              先在“模型卡片”发现配置，再选择决策模型。查看页面不会发起模型请求。
            </p>
          </div>
        )}
        <div className="setting-row">
          <div>
            <h3>自动采纳常规整理结果</h3>
            <p>
              允许有依据、保留适用条件和未解决风险的结果发布；其余交回 Host。
            </p>
          </div>
          <input
            aria-label="自动采纳常规整理结果"
            type="checkbox"
            disabled={!editing || snapshot.capabilities.maintenance !== true}
            checked={data.configuration.autoMaintain}
            onChange={(e) =>
              editor.setDraft((d) =>
                d
                  ? {
                      ...d,
                      configuration: {
                        ...d.configuration,
                        autoMaintain: e.target.checked,
                      },
                    }
                  : d,
              )
            }
          />
        </div>
        <div className="policy-note">
          <h3>用户偏好与事实分别保存</h3>
          <p>
            明确指定、优先考虑和排除各有含义。偏好影响后续选择，不会改写已有验收，也不会改变运行中任务的配置。
          </p>
        </div>
      </section>
    </div>
  );
}
