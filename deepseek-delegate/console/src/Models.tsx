import { useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import { addProfiles, emptyCard, setPreference, splitLines } from "./draft";
import type { Editor } from "./use-editor";
import type { Card, Profile, Snapshot } from "./types";
import { Badge, Empty, formatDate } from "./ui";

export function Models({
  snapshot,
  editor,
  api,
  refresh,
}: {
  snapshot: Snapshot;
  editor: Editor;
  api: ConsoleApi;
  refresh: () => Promise<Snapshot | null>;
}) {
  const [selected, setSelected] = useState<string | null>(null),
    [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [observation, setObservation] = useState("");
  const [project, setProject] = useState(""),
    [conditions, setConditions] = useState("");
  const data = editor.draft || snapshot,
    editing = editor.hasAuthority && !editor.busy && !editor.uncertain;
  const profiles = data.profiles.filter((p) =>
    `${p.label} ${p.model} ${p.provider}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  const profile = data.profiles.find((p) => p.profileId === selected);
  const card = profile
    ? data.cards.find((c) => c.profileId === profile.profileId) ||
      emptyCard(profile.profileId)
    : null;
  function updateCard(patch: Partial<Card>) {
    editor.setDraft((d) =>
      d && profile
        ? {
            ...d,
            cards: [
              ...d.cards.filter((c) => c.profileId !== profile.profileId),
              { ...card!, ...patch },
            ],
          }
        : d,
    );
  }
  async function discover() {
    setBusy(true);
    editor.setAuxiliaryBusy(true);
    setError("");
    try {
      const result = await api.command<{ profiles: Profile[] }>(
        "model_catalog_refresh",
        { requestId: crypto.randomUUID() },
        snapshot.csrfToken,
      );
      if (!Array.isArray(result.profiles))
        throw new Error("目录未返回可识别的模型配置。");
      editor.setDraft((d) => (d ? addProfiles(d, result.profiles) : d));
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
      editor.setAuxiliaryBusy(false);
    }
  }
  async function addObservation() {
    if (!profile) return;
    setBusy(true);
    setError("");
    try {
      await api.command(
        "evaluation_evidence_record",
        {
          commandId: crypto.randomUUID(),
          profileId: profile.profileId,
          kind: "observation",
          summary: observation.trim(),
          source: "user",
          project: project.trim() || null,
          conditions: splitLines(conditions)
            .map((s) => s.trim())
            .filter(Boolean),
        },
        snapshot.csrfToken,
      );
      setObservation("");
      setConditions("");
      await refresh();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="workspace-grid model-grid">
      <section className="panel">
        <div className="panel-toolbar">
          <h2>
            已接入配置 <span className="muted">{data.profiles.length}</span>
          </h2>
          <button
            className="button small-button"
            disabled={!editing || busy}
            onClick={() => void discover()}
          >
            发现模型
          </button>
        </div>
        <label className="search">
          <span className="sr-only">搜索模型配置</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="按模型、Harness 或提供方查找"
          />
        </label>
        {error && (
          <p role="alert" className="error-message padded">
            {error}
          </p>
        )}
        {profiles.length ? (
          <ul className="profile-list">
            {profiles.map((p) => {
              const c = data.cards.find(
                  (item) => item.profileId === p.profileId,
                ),
                pref = data.preferences.find(
                  (item) => item.profileId === p.profileId,
                );
              return (
                <li key={p.profileId}>
                  <button
                    className={`profile-row ${selected === p.profileId ? "selected" : ""}`}
                    aria-pressed={selected === p.profileId}
                    onClick={() => {
                      setSelected(p.profileId);
                      setObservation("");
                    }}
                  >
                    <div className="profile-avatar" aria-hidden="true">
                      {p.adapter.slice(0, 1).toUpperCase()}
                    </div>
                    <div className="profile-copy">
                      <div className="row-between">
                        <h3>{p.label || p.model}</h3>
                        <Badge>{p.effort}</Badge>
                      </div>
                      <p className="small muted">
                        {p.adapter} / {p.provider}
                      </p>
                      <p className="card-summary">
                        {c?.summary || "尚无能力评价。保留未知，等待实际经验。"}
                      </p>
                      <div className="tag-row">
                        <Badge
                          tone={p.enabled && p.available ? "green" : "neutral"}
                        >
                          {!p.enabled
                            ? "已停用"
                            : p.available
                              ? "目录可用"
                              : "可用性待确认"}
                        </Badge>
                        {pref && (
                          <Badge tone="amber">
                            {
                              {
                                prefer: "用户偏好",
                                pin: "固定选择",
                                exclude: "已排除",
                              }[pref.mode]
                            }
                          </Badge>
                        )}
                        <span className="small muted">
                          {c?.sampleCount || 0} 个验证样本
                        </span>
                      </div>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <Empty title="建立共同的能力记录">
            点击“编辑评价表”取得权限，再发现本机已接入的模型。模型目录与能力评价会分别保存。
          </Empty>
        )}
      </section>
      <aside className="panel detail-panel" aria-label="评价卡片详情">
        {profile && card ? (
          <>
            <div className="panel-heading">
              <span className="eyebrow">ASSESSMENT CARD</span>
              <h2>{profile.label || profile.model}</h2>
              <p className="small muted">
                {profile.model} · {profile.effort}
              </p>
            </div>
            <div className="tag-row">
              <Badge>共享经验</Badge>
              <span className="small muted">
                更新于 {formatDate(card.updatedAt)}
              </span>
            </div>
            <dl className="facts">
              <dt>来源</dt>
              <dd>{profile.source || "未记录"}</dd>
              <dt>上下文</dt>
              <dd>
                {profile.contextWindow
                  ? `${new Intl.NumberFormat("zh-CN").format(profile.contextWindow)} tokens`
                  : "未知"}
              </dd>
              <dt>可用性</dt>
              <dd>
                {profile.unavailableReason ||
                  (profile.available
                    ? "目录声明可用，实际调用仍需验证"
                    : "尚未确认")}
              </dd>
            </dl>
            <label className="field">
              <span>当前评价</span>
              <textarea
                rows={4}
                value={card.summary}
                readOnly={!editing}
                onChange={(e) => updateCard({ summary: e.target.value })}
                maxLength={2000}
                placeholder="尚无评价"
              />
            </label>
            {(["strengths", "limitations", "risks"] as const).map((key) => (
              <label className="field" key={key}>
                <span>
                  {
                    {
                      strengths: "适用工作",
                      limitations: "适用限制",
                      risks: "未解决问题",
                    }[key]
                  }
                </span>
                <textarea
                  rows={3}
                  value={card[key].join("\n")}
                  readOnly={!editing}
                  onChange={(e) =>
                    updateCard({ [key]: splitLines(e.target.value) })
                  }
                  placeholder="每行一条，保留适用条件"
                  maxLength={4000}
                />
              </label>
            ))}
            <label className="field">
              <span>用户偏好</span>
              <select
                disabled={!editing}
                value={
                  data.preferences.find(
                    (p) => p.profileId === profile.profileId,
                  )?.mode || ""
                }
                onChange={(e) =>
                  editor.setDraft((d) =>
                    d
                      ? setPreference(
                          d,
                          profile.profileId,
                          e.target.value as "prefer" | "pin" | "exclude" | "",
                        )
                      : d,
                  )
                }
              >
                <option value="">无额外偏好</option>
                <option value="prefer">优先考虑</option>
                <option value="pin">固定选择</option>
                <option value="exclude">排除</option>
              </select>
            </label>
            <label className="field">
              <span>偏好依据</span>
              <input
                disabled={
                  !editing ||
                  !data.preferences.some(
                    (p) => p.profileId === profile.profileId,
                  )
                }
                value={
                  data.preferences.find(
                    (p) => p.profileId === profile.profileId,
                  )?.reason || ""
                }
                maxLength={500}
                placeholder="说明目标或使用经验"
                onChange={(e) =>
                  editor.setDraft((d) =>
                    d
                      ? {
                          ...d,
                          preferences: d.preferences.map((p) =>
                            p.profileId === profile.profileId
                              ? { ...p, reason: e.target.value }
                              : p,
                          ),
                        }
                      : d,
                  )
                }
              />
            </label>
            <label className="checkbox-field">
              <input
                type="checkbox"
                disabled={!editing}
                checked={profile.enabled}
                onChange={(e) =>
                  editor.setDraft((d) =>
                    d
                      ? {
                          ...d,
                          profiles: d.profiles.map((p) =>
                            p.profileId === profile.profileId
                              ? { ...p, enabled: e.target.checked }
                              : p,
                          ),
                        }
                      : d,
                  )
                }
              />
              允许后续选择使用此配置
            </label>
            <section className="detail-section">
              <h3>依据与观察</h3>
              {snapshot.evidence
                .filter((e) => e.profileId === profile.profileId)
                .map((e) => (
                  <div className="evidence-item" key={e.evidenceId}>
                    <p>{e.summary}</p>
                    <span className="small muted">
                      {e.source} · {formatDate(e.createdAt)}
                      {e.project ? ` · ${e.project}` : ""}
                    </span>
                    {e.conditions.length > 0 && (
                      <p className="small">条件：{e.conditions.join("；")}</p>
                    )}
                  </div>
                ))}
              <label className="field">
                <span>补充观察</span>
                <textarea
                  rows={3}
                  value={observation}
                  onChange={(e) => setObservation(e.target.value)}
                  placeholder="描述事实，原始观察不会直接改成已验证成绩"
                  maxLength={2000}
                />
              </label>
              <label className="field">
                <span>项目来源（可选）</span>
                <input
                  value={project}
                  onChange={(e) => setProject(e.target.value)}
                  maxLength={200}
                  placeholder="项目名称或来源标记"
                />
              </label>
              <label className="field">
                <span>适用条件（每行一条）</span>
                <textarea
                  rows={2}
                  value={conditions}
                  onChange={(e) => setConditions(e.target.value)}
                  maxLength={2000}
                  placeholder="例如：React 状态管理；不修改公共 API"
                />
              </label>
              <button
                className="button"
                disabled={
                  busy ||
                  !observation.trim() ||
                  !snapshot.profiles.some(
                    (p) => p.profileId === profile.profileId,
                  )
                }
                onClick={() => void addObservation()}
              >
                记录待整理观察
              </button>
              {!snapshot.profiles.some(
                (p) => p.profileId === profile.profileId,
              ) && (
                <p className="small muted">先发布这个配置，再为它记录观察。</p>
              )}
            </section>
          </>
        ) : (
          <div className="detail-placeholder">
            <div className="empty-mark">↗</div>
            <h2>经验可以跨项目积累</h2>
            <p>
              选择一个模型配置，查看它的能力、适用条件与证据。偏好和实际表现分别记录。
            </p>
          </div>
        )}
      </aside>
    </div>
  );
}
