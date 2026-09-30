import { useState } from "react";
import type { ConsoleApi, HarnessHealth } from "./api";
import { ApiError, errorText } from "./api";
import type { QuotaRoutingRecord } from "./types";
import { dayClock } from "./objective-display";

/**
 * The minimal quota recovery entry (ADR-019 second stage). One persisted
 * exhaustion record without a recorded reset would exclude its configuration
 * from automatic routing forever, because an excluded configuration is never
 * routed again and so never produces a newer observation. The one-shot retry
 * window fixes that: one hour after the blocking observation (or after a
 * consumed retry, or immediately after an explicit re-detect) one normal
 * routing decision may admit the configuration again.
 *
 * 重新检测 is honest by construction: it only opens that window. It queries no
 * balance, calls no model and never claims the quota recovered — including for
 * harnesses that do have an active account query, which this entry never runs.
 * Only a newer usable native observation clears the exhaustion itself.
 */

/** One blocked record's recovery state, phrased from the recorded facts only. */
function recoveryText(record: QuotaRoutingRecord): string {
  if (record.resetsAt) return `已知重置时间，${dayClock(record.resetsAt)} 后按原规则恢复`;
  const retry = record.retry;
  if (!retry) return "无重置时间，恢复状态未知（需要新的原生观测）";
  if (retry.open) return retry.consumedAt
    ? `上次机会使用于 ${dayClock(retry.consumedAt)}；现可再次重试`
    : "重试机会已开放：选中该提供方后可重试一次";
  return retry.consumedAt
    ? `机会已于 ${dayClock(retry.consumedAt)} 使用，${dayClock(retry.eligibleAt)} 后可再重试`
    : `${dayClock(retry.eligibleAt)} 后允许一次自动路由重试`;
}

/** The providers of this harness that a re-detect could help right now. */
function retryProviders(records: QuotaRoutingRecord[]): string[] {
  return [...new Set(records.filter(record => record.blocked && !record.resetsAt).map(record => record.provider))];
}

export function QuotaRecovery({ row, api, csrfToken, canWrite, title, onRefreshed }: {
  row: HarnessHealth; api: ConsoleApi; csrfToken: string; canWrite: boolean;
  title: string | undefined; onRefreshed: () => Promise<unknown> | void;
}) {
  const records = row.quotaRouting ?? [];
  const blocked = records.filter(record => record.blocked);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  if (!blocked.length) return null;
  const providers = retryProviders(records);
  const writableTitle = !canWrite ? (title ?? "暂时不能执行该操作") : undefined;

  async function redetect(provider: string) {
    if (busy) return;
    if (!canWrite) {
      setError(`${writableTitle}：不能开放额度重试窗口。`);
      return;
    }
    setBusy(provider); setError(""); setNote("");
    try {
      const intentKey = `buddy-quota-redetect:${row.adapter}:${provider}`;
      let requestId: string;
      try {
        const retained = sessionStorage.getItem(intentKey);
        requestId = retained && /^[A-Za-z0-9_-]{1,128}$/.test(retained) ? retained : crypto.randomUUID();
        sessionStorage.setItem(intentKey, requestId);
      } catch {
        throw new ApiError("REQUEST_STORAGE_UNAVAILABLE", "无法保留重新检测请求，请允许本页会话存储后重试。");
      }
      const reply = await api.command<{ duplicate?: boolean; quota?: { opened?: (string | null)[]; alreadyOpen?: (string | null)[] } }>(
        "quota_redetect", { adapter: row.adapter, provider, requestId }, csrfToken);
      if (!reply?.quota || !Array.isArray(reply.quota.opened) || !Array.isArray(reply.quota.alreadyOpen)) {
        throw new ApiError("INVALID_RESPONSE", "重新检测结果未知，请核对本次请求。");
      }
      sessionStorage.removeItem(intentKey);
      const opened = reply?.quota?.opened ?? [];
      const already = reply?.quota?.alreadyOpen ?? [];
      setNote(reply.duplicate
        ? "本次重新检测已处理，以刷新后的状态为准。"
        : opened.length
        ? `已为 ${provider} 开放一次重试机会；选中后检测，不代表额度已恢复。`
        : already.length
          ? `${provider} 的重试窗口已是开放状态，无需重复操作。`
          : `${provider} 当前没有需要重新检测的耗尽记录。`);
      await onRefreshed();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy("");
    }
  }

  return <div className="quota-recovery">
    <h4>额度耗尽记录 <span className="muted">{blocked.length}</span></h4>
    <ul className="quota-windows" aria-label={`${row.adapter} 额度耗尽记录`}>
      {blocked.map(record => <li key={`${record.provider}:${record.limitId ?? ""}`}>
        <span className="quota-name">{record.provider}{record.limitId ? ` · ${record.limitId}` : ""}</span>
        <span className={record.code === "HARNESS_BALANCE_ZERO" ? "quota-flag" : "quota-used"}>
          {record.code === "HARNESS_BALANCE_ZERO" ? "余额为零" : "额度耗尽"}</span>
        <span className="muted">{dayClock(record.observedAt)} 观测 · 来源 {record.source}</span>
        <span className="muted">{recoveryText(record)}</span>
      </li>)}
    </ul>
    <p className="small muted">重新检测只开放一次后续正常调用的重试机会：不查询余额、不调用模型，也不代表额度已恢复；只有更新的可用观测才会清除耗尽。</p>
    <div className="actions">
      {providers.map(provider => <button key={provider} type="button" className="button small-button"
        aria-disabled={busy !== "" || !canWrite}
        title={writableTitle ?? (busy === provider ? "正在开放重试窗口…" : `为 ${provider} 立即开放一次自动路由重试`)}
        aria-label={`重新检测 ${provider} 额度`}
        onClick={() => void redetect(provider)}>重新检测{providers.length > 1 ? ` ${provider}` : ""}</button>)}
    </div>
    {error && <p className="error-message" role="alert">{error}</p>}
    {note && <p className="success-message" role="status">{note}</p>}
  </div>;
}
