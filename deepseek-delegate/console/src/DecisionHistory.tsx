import { useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import type { Decision, Snapshot } from "./types";
import { Badge, formatDate } from "./ui";

type Audit = Decision & {
  input?: unknown;
  output?: unknown;
  proposal?: unknown;
  inputSha256?: string | null;
  pendingEvidenceRemaining?: number;
  publishedRevision?: number | null;
};

const labels: Record<string, string> = {
  queued: "等待中",
  running: "处理中",
  completed: "已完成",
  "needs-host": "交回 Host",
  failed: "执行失败",
  cancelled: "已取消",
  stale: "结果已失效",
};

export function DecisionHistory({
  snapshot,
  api,
}: {
  snapshot: Snapshot;
  api: ConsoleApi;
}) {
  const [audit, setAudit] = useState<Audit | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function inspect(decisionId: string) {
    setBusy(true);
    setError("");
    setAudit(null);
    try {
      const value = await api.command<{ decision: Audit }>(
        "selection_get",
        { decisionId },
        snapshot.csrfToken,
      );
      setAudit(value.decision);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
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
                <strong>
                  {d.kind === "maintain" ? "整理评价证据" : d.task}
                </strong>
                <Badge
                  tone={
                    ["failed", "needs-host", "stale"].includes(d.status)
                      ? "amber"
                      : "neutral"
                  }
                >
                  {labels[d.status] || d.status}
                </Badge>
              </div>
              <p>{d.reason || d.error || "等待处理结果。"}</p>
              <div className="small muted">
                {d.kind === "maintain"
                  ? "评价整理"
                  : d.profileId
                    ? snapshot.profiles.find((p) => p.profileId === d.profileId)
                        ?.label || d.profileId
                    : "未选择配置"}
                {" · "}评价版本 {d.tableRevision}
                {" · "}
                {formatDate(d.createdAt)}
              </div>
              {d.runId && (
                <p className="mono small wrap">计算任务：{d.runId}</p>
              )}
              <button
                className="button small-button"
                disabled={busy}
                onClick={() => void inspect(d.decisionId)}
              >
                查看输入与建议
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="padded muted">
          还没有决策记录。这里不会自动生成演示结果。
        </p>
      )}
      {error && (
        <p className="error-message" role="alert">
          {error}
        </p>
      )}
      {audit && (
        <section className="detail-section" aria-label="决策依据详情">
          <div className="row-between">
            <h3>决策依据</h3>
            <button
              className="button small-button"
              onClick={() => setAudit(null)}
            >
              收起详情
            </button>
          </div>
          <p className="mono small wrap">{audit.decisionId}</p>
          <p>{audit.reason}</p>
          {audit.publishedRevision != null && (
            <p>发布版本：V{audit.publishedRevision}</p>
          )}
          {audit.proposal != null && (
            <details open>
              <summary>整理建议</summary>
              <pre className="result-text">
                {JSON.stringify(audit.proposal, null, 2)}
              </pre>
            </details>
          )}
          <details>
            <summary>发送给决策模型的快照</summary>
            <pre className="result-text">
              {JSON.stringify(audit.input, null, 2)}
            </pre>
          </details>
          <details>
            <summary>持久模型回执</summary>
            <pre className="result-text">
              {JSON.stringify(audit.output, null, 2)}
            </pre>
          </details>
          {audit.inputSha256 && (
            <p className="mono small wrap">输入 SHA-256：{audit.inputSha256}</p>
          )}
        </section>
      )}
    </section>
  );
}
