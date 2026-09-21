import { useState } from "react";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import type { Editor } from "./use-editor";
import { Badge, Icon } from "./ui";
import { useDecisionRequest } from "./use-decision-request";
import { DecisionHistory } from "./DecisionHistory";

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
  const [task, setTask] = useState("");
  const decision = useDecisionRequest(api, snapshot, refresh);
  const maintenance = useDecisionRequest(api, snapshot, refresh);
  const canSelect = snapshot.capabilities.selection === true;
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
        <div className="detail-section">
          <h3>整理待处理证据</h3>
          <p className="small muted">
            当前有 {snapshot.pendingEvidence}{" "}
            条待处理。点击后才提交一次整理任务；未启用自动采纳时，只保留建议。
          </p>
          <button
            className="button"
            disabled={
              maintenance.busy ||
              snapshot.capabilities.maintenance !== true ||
              (!maintenance.retryId &&
                (!snapshot.configuration.decisionProfileId ||
                  snapshot.pendingEvidence === 0))
            }
            onClick={() => void maintenance.submit("evaluation_maintain")}
          >
            {maintenance.busy
              ? "正在提交…"
              : maintenance.retryId
                ? "重试同一整理请求"
                : "请求整理"}
          </button>
          {maintenance.message && (
            <p className="inline-message" role="status">
              {maintenance.message}
            </p>
          )}
          {maintenance.retryId && (
            <p className="mono small wrap">请求 ID：{maintenance.retryId}</p>
          )}
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
            disabled={decision.busy || !!decision.retryId}
            value={task}
            onChange={(e) => setTask(e.target.value)}
            maxLength={16000}
            placeholder="例如：为现有状态管理模块补充并发测试，保留公共 API，不修改界面布局。"
          />
        </label>
        <button
          className="button primary"
          disabled={
            decision.busy ||
            !canSelect ||
            (!decision.retryId &&
              (!snapshot.configuration.decisionProfileId || !task.trim()))
          }
          onClick={() =>
            void decision.submit("selection_request", { task: task.trim() })
          }
        >
          {decision.busy
            ? "正在提交…"
            : decision.retryId
              ? "重试同一推荐请求"
              : "请求推荐"}
          <Icon name="arrow" size={16} />
        </button>
        {!canSelect && (
          <p className="small muted">当前服务尚未提供模型决策执行能力。</p>
        )}
        {decision.message && (
          <p className="inline-message" role="status">
            {decision.message}
          </p>
        )}
        {decision.retryId && (
          <p className="mono small wrap">请求 ID：{decision.retryId}</p>
        )}
        {snapshot.gate.phase !== "open" && (
          <p className="small muted">
            评价表正在编辑，新请求会排队，随后读取完整发布版本。
          </p>
        )}
      </section>
      <DecisionHistory snapshot={snapshot} api={api} />
    </div>
  );
}
