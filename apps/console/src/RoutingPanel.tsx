import { useState } from "react";
import type { Profile } from "./types";
import type { ExecutionConfiguration, Workflow } from "./workflow-types";
import { effortText, profileTitle } from "./profile-display";

export function needsRouting(value: Workflow) {
  return value.routing?.status === "needs-host" || value.activeRequest?.kind === "routing" || value.activeRequest?.routing === true;
}

export function RoutingPanel({ value, profiles, locked, command }: {
  value: Workflow;
  profiles: Profile[];
  locked: boolean;
  command: (operation: string, params: Record<string, unknown>) => Promise<void>;
}) {
  const [configuration, setConfiguration] = useState<ExecutionConfiguration>({ adapter: "", provider: "", model: "", effort: "" });
  const needsHost = needsRouting(value);
  const targetRunId = value.activeRequest?.origin?.runId || value.activeRequest?.childTaskId;
  const fields = [["adapter", "Harness"], ["provider", "Provider"], ["model", "模型"], ["effort", "Thinking effort"]] as const;
  const parameters = {
    expectedRevision: value.revision,
    ...(targetRunId && targetRunId !== value.runId ? { targetRunId } : {}),
  };
  const stopped = value.shutdown?.selfConfirmed && value.shutdown?.descendantsConfirmed;
  return <section className="detail-section">
    <h3>模型路由</h3>
    {value.executionConfiguration ? <p className="mono wrap">
      {fields.map(([key]) => key === "effort"
        ? effortText(value.executionConfiguration![key])
        : value.executionConfiguration![key]).join(" / ")}
    </p> : <p className="muted">尚未确定执行配置。</p>}
    {value.routing && <dl className="facts">
      <dt>路由状态</dt><dd>{value.routing.status}</dd>
      {value.routing.tableRevision != null && <><dt>评价表版本</dt><dd>V{value.routing.tableRevision}</dd></>}
      {value.routing.decisionId && <><dt>决策 ID</dt><dd className="mono wrap">{value.routing.decisionId}</dd></>}
    </dl>}
    {needsHost && <fieldset className="workflow-controls" disabled={locked || !stopped}>
      <legend>补齐配置或重试路由</legend>
      <p>{value.activeRequest?.summary || value.routing?.reason || "路由需要 Host 补充信息。"}</p>
      {targetRunId && <p className="small wrap">接续目标：<code>{targetRunId}</code></p>}
      <p className="small muted">完整配置会跳过决策模型；原请求已指定的字段仍是硬约束。也可以先修正“路由配置”，再为同一目标重试路由。</p>
      <label className="field"><span>填入已启用配置</span><select defaultValue="" onChange={event => {
        const profile = profiles.find(item => item.profileId === event.target.value);
        if (profile) setConfiguration({ adapter: profile.adapter, provider: profile.provider, model: profile.model, effort: profile.effort });
      }}>
        <option value="">手动填写完整配置</option>
        {profiles.map(profile => <option key={profile.profileId} value={profile.profileId}>{profile.adapter} · {profileTitle(profile)}</option>)}
      </select></label>
      {fields.map(([key, label]) => <label className="field" key={key}><span>{label}</span>
        <input value={configuration[key]} maxLength={200} autoComplete="off"
          onChange={event => setConfiguration(current => ({ ...current, [key]: event.target.value }))} />
      </label>)}
      <div className="actions">
        <button className="button primary" disabled={!fields.every(([key]) => configuration[key].trim())}
          onClick={() => void command("workflow_continue", {
            ...parameters, input: "Host 已补齐执行配置，请继续原授权目标。",
            configuration: Object.fromEntries(fields.map(([key]) => [key, configuration[key].trim()])),
          })}>使用此配置接续</button>
        <button className="button" onClick={() => void command("workflow_continue", {
          ...parameters, input: "Host 已检查决策配置，请按原始约束重新路由。", reroute: true,
        })}>重试同一目标的路由</button>
      </div>
      {!stopped && <p className="small muted">需先取得已有执行的停止证据。</p>}
    </fieldset>}
  </section>;
}
