import { useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import type { Snapshot } from "./types";
import type { Editor } from "./use-editor";
import { Badge, formatDate, Icon } from "./ui";

export function Settings({
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
  const data = editor.draft || snapshot,
    editing = editor.hasAuthority && !editor.busy && !editor.uncertain;
  const current = data.profiles.find(
    (p) => p.profileId === data.configuration.decisionProfileId,
  );
  const [task, setTask] = useState(""),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  const canSelect = snapshot.capabilities.selection === true;
  async function requestDecision() {
    setBusy(true);
    setMessage("");
    try {
      await api.command(
        "selection_request",
        { requestId: crypto.randomUUID(), task: task.trim() },
        snapshot.csrfToken,
      );
      setMessage("推荐请求已记录，可在下方查看结果。");
      await refresh();
    } catch (failure) {
      setMessage(errorText(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="settings-grid">
      <section className="panel settings-panel">
        <div className="panel-heading">
          <span className="eyebrow">DECISION PROFILE</span>
          <h2>让合适的 Buddy 做选择</h2>
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
                  {p.label || p.model} · {p.effort}
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
                {current.provider} / {current.effort}
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
              先在“模型与经验”发现配置，再选择决策模型。查看页面不会发起模型请求。
            </p>
          </div>
        )}
        <div className="setting-row">
          <div>
            <h3>自动整理验收经验</h3>
            <p>在已授权范围内，整理新证据并保留每次修订。</p>
          </div>
          <input
            aria-label="自动整理验收经验"
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
      <section className="panel settings-panel">
        <div className="panel-heading">
          <span className="eyebrow">TRY A DECISION</span>
          <h2>查看一次推荐</h2>
          <p className="muted">
            明确发起后才调用决策模型，推荐不会自动启动执行任务。
          </p>
        </div>
        <label className="field">
          <span>任务与约束</span>
          <textarea
            rows={6}
            value={task}
            onChange={(e) => setTask(e.target.value)}
            maxLength={16000}
            placeholder="例如：为现有状态管理模块补充并发测试，保留公共 API，不修改界面布局。"
          />
        </label>
        <button
          className="button primary"
          disabled={
            busy ||
            !canSelect ||
            !data.configuration.decisionProfileId ||
            !task.trim()
          }
          onClick={() => void requestDecision()}
        >
          {busy ? "正在提交…" : "请求推荐"}
          <Icon name="arrow" size={16} />
        </button>
        {!canSelect && (
          <p className="small muted">当前服务尚未提供模型决策执行能力。</p>
        )}
        {message && (
          <p className="inline-message" role="status">
            {message}
          </p>
        )}
      </section>
      <section className="panel settings-panel decision-history">
        <div className="panel-toolbar">
          <h2>最近决策</h2>
          <span className="small muted">记录依据，不把推荐当作能力证明</span>
        </div>
        {snapshot.decisions.length ? (
          <ul className="decision-list">
            {snapshot.decisions.map((d) => (
              <li key={d.decisionId}>
                <div className="row-between">
                  <strong>{d.task}</strong>
                  <Badge
                    tone={
                      d.status === "failed" || d.status === "deferred"
                        ? "amber"
                        : "neutral"
                    }
                  >
                    {(
                      {
                        queued: "等待中",
                        running: "判断中",
                        recommended: "已推荐",
                        deferred: "交回 Host",
                        failed: "未形成推荐",
                      } as Record<string, string>
                    )[d.status] || d.status}
                  </Badge>
                </div>
                <p>{d.reason || d.error || "等待决策结果。"}</p>
                <div className="small muted">
                  {d.profileId
                    ? data.profiles.find((p) => p.profileId === d.profileId)
                        ?.label || d.profileId
                    : "未选择配置"}{" "}
                  · 评价版本 {d.tableRevision} · {formatDate(d.createdAt)}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="padded muted">
            还没有决策记录。这里不会自动生成演示结果。
          </p>
        )}
      </section>
    </div>
  );
}
