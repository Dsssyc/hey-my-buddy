import { useEffect, useState } from "react";
import type { ConsoleApi } from "./api";
import { ApiError, errorText, uncertainResponse } from "./api";
import type { HarnessHealth, ReviewVerification, Snapshot } from "./types";
import { Help } from "./ui";

const checks = ["requestIdentity", "nativePolicy", "forbiddenTools", "boundaryDenials", "internalRead",
  "inputUnchanged", "sentinelUnchanged", "shutdownConfirmed", "budgetConsistent"];
const statuses = ["verified", "new-version", "unverified", "queued", "running", "stopping", "unconfirmed", "failed"];
const pending = new Set(["queued", "running", "stopping", "unconfirmed"]);
const labels: Record<string, string> = { verified: "已验证", "new-version": "新版本待验证", unverified: "未验证",
  queued: "等待验证", running: "验证中", stopping: "正在停止", unconfirmed: "停止未确认", failed: "验证失败" };
const checkLabels: Record<string, string> = { requestIdentity: "原生身份", nativePolicy: "原生策略", forbiddenTools: "工具限制",
  boundaryDenials: "越界拒绝", internalRead: "目录内读取", inputUnchanged: "输入未变", sentinelUnchanged: "外部文件未变",
  shutdownConfirmed: "停止证据", budgetConsistent: "预算" };

/** Reject malformed verification facts; a true flag alone is never a certificate. */
export function parseReviewVerification(value: unknown, row: HarnessHealth): ReviewVerification | null {
  const record = value as ReviewVerification | null;
  if (!record || record.adapter !== row.adapter || record.version !== row.version || typeof record.platform !== "string"
    || !statuses.includes(record.status) || typeof record.verified !== "boolean" || typeof record.implemented !== "boolean") return null;
  if (record.runId !== undefined && (typeof record.runId !== "string" || record.runId.length > 128)) return null;
  if (record.failedChecks !== undefined && (!Array.isArray(record.failedChecks) || record.failedChecks.some(key => !checks.includes(key)))) return null;
  if (record.verified && (record.status !== "verified" || row.status !== "ready"
    || !checks.every(key => record.checks?.[key] === true))) return null;
  return record;
}

export function HarnessReview({ row, snapshot, api, canWrite, onRefresh }: {
  row: HarnessHealth; snapshot: Snapshot; api: ConsoleApi; canWrite: boolean; onRefresh: () => Promise<unknown>;
}) {
  const certificate = parseReviewVerification(row.reviewVerification, row);
  const profiles = snapshot.profiles.filter(profile => profile.adapter === row.adapter && profile.enabled && profile.available);
  const [profileId, setProfileId] = useState(() => profiles.find(profile => profile.profileId === snapshot.configuration.reviewRouterProfileId)?.profileId ?? "");
  const [intent, setIntent] = useState<{ requestId: string; profileId: string; expectedRevision: number } | null>(null);
  const [runId, setRunId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (certificate?.runId === runId && !pending.has(certificate.status)) setRunId("");
  }, [certificate?.runId, certificate?.status, runId]);
  if (row.adapter !== "codex") return null;
  const waiting = pending.has(certificate?.status ?? "") || !!runId;
  const unavailable = busy || !canWrite || waiting || row.status !== "ready" || !profiles.some(profile => profile.profileId === profileId);
  const status = runId && certificate?.runId !== runId ? "queued" : certificate?.status ?? "unverified";
  async function verify() {
    if (unavailable) return;
    const call = intent ?? { requestId: crypto.randomUUID(), profileId, expectedRevision: row.revision };
    setIntent(call); setBusy(true); setError("");
    try {
      const result = await api.command<{ runId?: string; status?: string }>("harness_verify",
        { adapter: row.adapter, ...call, execute: true }, snapshot.csrfToken);
      if (typeof result.runId !== "string" || !result.runId || typeof result.status !== "string") {
        throw new ApiError("INVALID_RESPONSE", "验证结果未知，请核对本次请求。");
      }
      setRunId(pending.has(result.status) ? result.runId : ""); setIntent(null);
      await onRefresh();
    } catch (failure) {
      setError(errorText(failure));
      if (!uncertainResponse(failure)) setIntent(null);
    } finally { setBusy(false); }
  }
  return <div className="harness-review">
    <span>审阅能力：{certificate?.verified ? "已验证" : labels[status] ?? "未验证"}</span>
    <Help label="审阅能力验证说明">点击会调用所选模型检查权限，检查数据发给该模型提供方；最多增加一次格式纠正。</Help>
    <label>验证配置 <select aria-label="审阅验证配置" value={profileId} disabled={busy || waiting || !!intent || !canWrite}
      onChange={event => setProfileId(event.target.value)}>
      <option value="">请选择配置</option>
      {profiles.map(profile => <option key={profile.profileId} value={profile.profileId}>{profile.model} / {profile.effort}</option>)}
    </select></label>
    <button type="button" className="button small-button" disabled={unavailable} onClick={() => void verify()}>
      {busy ? "提交中" : intent ? "核对本次验证" : "重新验证审阅能力"}
    </button>
    {certificate?.status === "failed" && <p className="small" role="status">
      {(certificate.failedChecks ?? []).map(key => checkLabels[key]).join("、") || "原生检查失败"}；检查 CLI 权限后重新验证。
    </p>}
    {error && <p className="small" role="alert">{error}</p>}
  </div>;
}
