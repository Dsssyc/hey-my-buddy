import type { Workflow } from "./workflow-types";
import { effortText } from "./profile-display";

export function needsRouting(value: Workflow) {
  return value.routing?.status === "needs-host" || value.activeRequest?.kind === "routing" || value.activeRequest?.routing === true;
}

/**
 * Read-only model-routing facts (0.15.1 U4). The recorded execution
 * configuration and routing decision stay visible, but the browser no longer
 * offers the 补齐配置/重试路由 forms: a routing boundary is resolved by the
 * Host through its existing CLI capability flow.
 */
export function RoutingPanel({ value }: { value: Workflow }) {
  const fields = [["adapter", "Harness"], ["provider", "Provider"], ["model", "模型"], ["effort", "Thinking effort"]] as const;
  const needsHost = needsRouting(value);
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
    {needsHost && <p className="small muted" role="status">
      路由需要 Host 补充配置：控制台不再提供补齐或重试表单，请由 Host 通过既有 CLI 流程处理后刷新查看。
    </p>}
  </section>;
}
