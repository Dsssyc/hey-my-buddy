import { useEffect, useState } from "react";
import type { ConsoleApi, HarnessCandidate, HarnessHealth, HarnessStatus as HarnessStatusValue } from "./api";
import { ApiError, errorText, snapshotHarnesses } from "./api";
import type { Snapshot } from "./types";
import type { BuddyLocation } from "./buddy-navigation";
import { Badge } from "./ui";
import { harnessName } from "./buddy-display";
import { dayClock } from "./objective-display";
import { LOGIN_EXPIRED_ACTION_REFUSAL } from "./console-session";
import { quotaView } from "./host-workflow";
import { HarnessReview } from "./HarnessReview";
import { QuotaRecovery } from "./QuotaRecovery";
import { billingLabel } from "./BillingQuotaLabel";
import { HarnessAccount } from "./HarnessAccount";

/** Status wording and the non-colour badge tone; an unknown future state stays visible as recorded. */
const HARNESS_STATUS_LABEL: Record<HarnessStatusValue, string> = {
  ready: "已找到",
  missing: "未找到",
  "login-required": "需要登录",
  unhealthy: "不健康",
  unknown: "尚未检测",
};
const HARNESS_STATUS_TONE: Record<HarnessStatusValue, "neutral" | "green" | "amber" | "red"> = {
  ready: "green",
  missing: "red",
  "login-required": "amber",
  unhealthy: "red",
  unknown: "neutral",
};
/** What the user can do when no remedy was recorded for that state. */
const HARNESS_STATUS_REMEDY: Record<HarnessStatusValue, string> = {
  ready: "",
  unknown: "点击“重新检测”查看状态。",
  missing: "安装 CLI，或填写手动路径。",
  "login-required": "在 Harness 中登录后重新检测。",
  unhealthy: "检查 CLI，或更换手动路径。",
};
/** Stable discovery reason codes in short Chinese; an unknown code stays as recorded. */
const HARNESS_REASON_LABEL: Record<string, string> = {
  HARNESS_NOT_CHECKED: "尚未检测",
  HARNESS_NOT_FOUND: "未找到可执行文件",
  HARNESS_HANDSHAKE_FAILED: "握手失败",
  HARNESS_LOGIN_REQUIRED: "需要登录",
  HARNESS_UNHEALTHY: "运行不健康",
  HARNESS_INVALID_RESULT: "检测结果无效",
  not_found: "未找到",
  not_executable: "不是可执行文件",
  permission_denied: "没有执行权限",
  handshake_failed: "握手失败",
  login_required: "需要登录",
};
const HARNESS_CANDIDATE_STATUS_LABEL: Record<string, string> = {
  ready: "可用",
  ok: "可用",
  failed: "握手失败",
  missing: "未找到",
  "not-found": "未找到",
  "login-required": "需要登录",
  unhealthy: "不健康",
};

function harnessStatusText(status: string): string {
  return HARNESS_STATUS_LABEL[status as HarnessStatusValue] ?? status;
}
function harnessStatusTone(status: string): "neutral" | "green" | "amber" | "red" {
  return HARNESS_STATUS_TONE[status as HarnessStatusValue] ?? "neutral";
}
function harnessRemedy(row: HarnessHealth): string {
  return row.remedy?.trim() || HARNESS_STATUS_REMEDY[row.status as HarnessStatusValue] || "";
}
function harnessReasonText(code: string | undefined): string {
  const value = code?.trim() ?? "";
  return value ? HARNESS_REASON_LABEL[value] ?? value : "";
}
function harnessCandidateText(candidate: HarnessCandidate): string {
  const status = candidate.status?.trim() ?? "";
  const parts = [status ? HARNESS_CANDIDATE_STATUS_LABEL[status] ?? status : "", harnessReasonText(candidate.reasonCode)]
    .filter(Boolean);
  // "missing" and "not_found" describe the same fact; say it once.
  return [...new Set(parts)].join(" · ");
}
/** The chosen executable: the recorded one, else the launched command's first word. */
function harnessPath(row: HarnessHealth): string {
  return row.executable?.trim() || row.command?.[0]?.trim() || "";
}
function harnessSource(row: HarnessHealth): string {
  return row.source?.trim() || (row.manualPath ? "手动路径" : "未记录");
}
/** One line of facts under the harness name: found, or what is missing instead. */
function harnessSummary(row: HarnessHealth): string {
  const path = harnessPath(row);
  switch (row.status) {
    case "ready": {
      // ADR-017 §15: a found harness names its path, version and source in the row.
      const facts = [path ? `路径 ${path}` : "", row.version?.trim() ? `版本 ${row.version}` : "",
        `来源 ${harnessSource(row)}`].filter(Boolean);
      return `找到：${facts.join(" · ")}`;
    }
    case "login-required": return path ? `已找到 ${path}，但需要先登录` : "需要先登录";
    case "unhealthy": return path ? `已找到 ${path}，但检测未通过` : "检测未通过";
    case "missing": return `未找到可执行文件（已尝试 ${row.candidates?.length ?? 0} 处）`;
    default: return "尚未检测";
  }
}
/** The row's fuller facts — status, remedy and stored path — for the hover tooltip. */
function harnessTitle(row: HarnessHealth): string {
  const remedy = harnessRemedy(row);
  return [harnessSummary(row), remedy ? `修复办法：${remedy}` : "", row.manualPath ? `手动路径：${row.manualPath}` : ""]
    .filter(Boolean).join("\n");
}
/** A manual path is absolute; Windows drive paths count as absolute too. */
function absoluteHarnessPath(value: string): boolean {
  return value.startsWith("/") || /^[A-Za-z]:[\\/]/.test(value);
}

/** One explicit harness action; kept so a failed attempt can be retried as-is. */
type HarnessAction =
  | { kind: "check"; adapter?: string }
  | { kind: "set"; adapter: string; path: string | null };

/**
 * The Buddy 配置 harness strip (ADR-017 §15). It reads the recorded health of
 * every supported harness from the snapshot — found path, version and source,
 * or the attempted locations and the remedy — plus when it was last checked.
 * 重新检测 is always an explicit click that calls the existing `capabilities`
 * operation with `refresh: true` (no model call), then reloads the snapshot.
 * The advanced manual absolute path is offered only when automatic detection
 * did not succeed, or to change or clear an already stored one; every save
 * carries the recorded revision so a concurrent check is never overwritten.
 */
export function HarnessStatus({ snapshot, api, refresh, mutationsAvailable, sessionWritable, location, active }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  mutationsAvailable: boolean; sessionWritable: boolean;
  location: BuddyLocation; active: boolean;
}) {
  const recorded = snapshotHarnesses(snapshot);
  /** Rows returned by this page's own actions, shown until a snapshot reload lands. */
  const [replies, setReplies] = useState<Record<string, HarnessHealth>>({});
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  /** The adapter being checked, "*" for a full re-check, "" when idle. */
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [retry, setRetry] = useState<HarnessAction | null>(null);
  useEffect(() => {
    if (active && location.target) setOpen(previous => new Set([...previous, location.target!]));
  }, [location, active]);
  useEffect(() => {
    if (!active || !location.target || !open.has(location.target)) return;
    const row = document.getElementById(`harness-${location.target}`);
    row?.focus();
    row?.scrollIntoView?.({ block: "nearest" });
  }, [location, active, open]);
  const canWrite = mutationsAvailable && sessionWritable;
  const rows = recorded.map(row => replies[row.adapter] ?? row);
  if (!rows.length) return null;
  const readyCount = rows.filter(row => row.status === "ready").length;
  const attention = rows.some(row => row.status !== "ready");
  const lastChecked = rows.map(row => row.checkedAt ?? "").filter(Boolean).sort().at(-1) ?? "";
  const writableTitle = !sessionWritable
    ? LOGIN_EXPIRED_ACTION_REFUSAL
    : !mutationsAvailable ? "连接中断或缺少写入资格" : undefined;

  function toggle(adapter: string) {
    setOpen(previous => {
      const next = new Set(previous);
      if (next.has(adapter)) next.delete(adapter); else next.add(adapter);
      return next;
    });
  }
  function showFacts(adapter: string, kind: "account" | "quota") {
    setOpen(previous => new Set([...previous, adapter]));
    window.requestAnimationFrame(() => {
      const element = document.getElementById(`harness-${adapter}-${kind}`);
      element?.focus();
      element?.scrollIntoView?.({ block: "nearest" });
    });
  }
  function expectedRevision(adapter: string): number {
    return rows.find(row => row.adapter === adapter)?.revision ?? 0;
  }
  /** The reload is the point of the action: the snapshot stays the recorded truth. */
  async function reloadSnapshot() {
    const next = await refresh();
    if (next) setReplies({});
  }
  async function run(action: HarnessAction) {
    if (busy) return;
    if (!canWrite) {
      const reason = writableTitle ?? "暂时不能执行该操作";
      setError(`${reason}：不能重新检测或修改 harness。`);
      setRetry(null);
      return;
    }
    setBusy(action.kind === "check" ? (action.adapter ?? "*") : action.adapter);
    setError(""); setNote(""); setRetry(null);
    try {
      if (action.kind === "check") {
        const checked = await api.harnessRefresh(snapshot.csrfToken, action.adapter);
        setReplies(previous => {
          const next = { ...previous };
          for (const row of checked) next[row.adapter] = row;
          return next;
        });
        setNote(action.adapter
          ? `已重新检测 ${harnessName(action.adapter)}：${harnessStatusText(checked.find(row => row.adapter === action.adapter)?.status ?? "unknown")}。`
          : `已重新检测 ${checked.length} 个 harness：可用 ${checked.filter(row => row.status === "ready").length} 个，其余 ${checked.filter(row => row.status !== "ready").length} 个。`);
      } else {
        const updated = await api.harnessSet(action.adapter, action.path, expectedRevision(action.adapter), snapshot.csrfToken);
        setReplies(previous => ({ ...previous, [updated.adapter]: updated }));
        setNote(`${action.path ? `已保存手动路径：${harnessPath(updated) || action.path}` : "已恢复自动检测"}；${harnessName(updated.adapter)} ${harnessStatusText(updated.status)}。`);
      }
      await reloadSnapshot();
    } catch (failure) {
      setError(errorText(failure));
      setRetry(action);
      // A revision conflict means the recorded facts changed: reread them so a
      // retry fences against the new revision instead of replaying a stale one.
      if (failure instanceof ApiError && ["REVISION_CONFLICT", "CONFLICT", "STALE_GENERATION"].includes(failure.code)) {
        await reloadSnapshot();
      }
    } finally {
      setBusy("");
    }
  }
  return <section className={"harness-status-bar" + (attention ? " warning" : "")} aria-busy={busy !== ""} aria-label="Harness 状态">
    <div className="harness-status-line">
      <strong>Harness</strong>
      <span className="harness-status-facts">
        <span>可用 {readyCount}/{rows.length}</span>
        <span aria-hidden="true" className="routing-sep">｜</span>
        <span>上次检测：{lastChecked ? dayClock(lastChecked) : "未记录"}</span>
      </span>
      <button type="button" className="button small-button" aria-disabled={busy !== "" || !canWrite}
        title={writableTitle ?? (busy === "*" ? "正在重新检测…" : "重新检测全部 Harness")}
        onClick={() => void run({ kind: "check" })}>重新检测全部</button>
    </div>
    {error && <p className="banner guard-banner" role="alert">{error}
      {retry && <button type="button" className="button small-button" onClick={() => void run(retry)}>重试</button>}</p>}
    {note && <p className="success-message" role="status">{note}</p>}
    <ul className="harness-status-list" aria-label="Harness 检测结果">
      {rows.map(row => {
        const name = harnessName(row.adapter);
        const isOpen = open.has(row.adapter);
        const checking = busy === row.adapter || busy === "*";
        const candidates = row.candidates ?? [];
        const reason = harnessReasonText(row.reasonCode);
        const remedy = harnessRemedy(row);
        // ADR-018 §23: the latest recorded observation only; a stale or unknown
        // window never reads as available or as 0%.
        const quota = quotaView(row.quota);
        return <li key={row.adapter} id={`harness-${row.adapter}`} tabIndex={-1} className={"harness-status-row harness-" + row.status}>
          <div className="harness-status-rowline">
            <span className="harness-name">{name}</span>
            <Badge tone={harnessStatusTone(row.status)}>{harnessStatusText(row.status)}</Badge>
            {quota?.alert && <Badge tone={quota.stale ? "neutral" : "amber"}>
              {quota.limitReported ? "原生记录报告额度限制" : quota.windows.some(window => window.atLimit) ? "额度已到上限" : "额度接近上限"}
            </Badge>}
            <span className="harness-summary" title={harnessTitle(row)}>{harnessSummary(row)}</span>
            <span className="harness-row-actions">
              <button type="button" className="button small-button" aria-label={`${name} 账户`} onClick={() => showFacts(row.adapter, "account")}>账户</button>
              <button type="button" className="button small-button" aria-label={`${name} 额度`} onClick={() => showFacts(row.adapter, "quota")}>额度</button>
              <button type="button" className="icon-button harness-row-toggle" aria-expanded={isOpen}
                aria-label={`${name} ${isOpen ? "收起详情" : "检测详情"}`}
                title={`${name} ${isOpen ? "收起详情" : "检测详情"}`}
                onClick={() => toggle(row.adapter)}>{isOpen ? "▾" : "▸"}</button>
              <button type="button" className="button small-button" aria-disabled={checking || !canWrite}
                title={writableTitle ?? (checking ? "正在重新检测…" : `重新检测 ${name}`)}
                aria-label={`重新检测 ${name}`} onClick={() => void run({ kind: "check", adapter: row.adapter })}>重新检测</button>
            </span>
          </div>
          {isOpen && <div className="harness-detail">
            <dl className="harness-facts">
              <div><dt>状态</dt><dd>{harnessStatusText(row.status)}{reason ? `（${reason}）` : ""}</dd></div>
              <div><dt>路径</dt><dd>{harnessPath(row)
                ? <code className="mono harness-path" title={row.command?.join(" ") || harnessPath(row)}>{harnessPath(row)}</code>
                : <span className="muted">未记录</span>}</dd></div>
              <div><dt>版本</dt><dd>{row.version?.trim() || <span className="muted">未记录</span>}</dd></div>
              <div><dt>计费</dt><dd>{Object.entries(row.billingByProvider ?? {}).map(([provider, fact]) => `${provider}：${billingLabel(fact)}`).join(' · ') || '未知'}</dd></div>
              <div><dt>来源</dt><dd>{harnessSource(row)}</dd></div>
              <div><dt>上次检测</dt><dd>{row.checkedAt ? dayClock(row.checkedAt) : "未记录"}</dd></div>
              {row.manualPath && <div><dt>手动路径</dt><dd><code className="mono harness-path">{row.manualPath}</code></dd></div>}
              <div id={`harness-${row.adapter}-quota`} tabIndex={-1}><dt>额度观测</dt><dd>
                {!quota
                  ? <span className="muted">未记录观测（未知）</span>
                  : <>
                    <span>{quota.stale ? "最近一次观测，可能已过期" : "最近一次观测"} · 来源 {quota.source}
                      {quota.provider ? ` · ${quota.provider}` : ""} · {dayClock(quota.observedAt)}</span>
                    <ul className="quota-windows" aria-label={`${name} 额度窗口`}>
                      {quota.windows.map(window => <li key={window.name} className={window.nearLimit ? "quota-near" : undefined}>
                        <span className="quota-name">{window.name}</span>
                        <span className="quota-used">{window.usedPercent === null ? "使用率未知" : `使用率 ${window.usedText}`}</span>
                        {window.nearLimit && <span className="quota-flag">{window.atLimit ? "已到上限" : "接近上限"}</span>}
                        <span className="muted">重置 {window.resetsAt ? dayClock(window.resetsAt) : "未记录"}</span>
                      </li>)}
                    </ul>
                    <span className="small muted">{quota.note}</span>
                  </>}
              </dd></div>
            </dl>
            <HarnessAccount row={row} name={name} api={api} csrfToken={snapshot.csrfToken} canWrite={canWrite}
              title={writableTitle} onRefreshed={reloadSnapshot} />
            <HarnessReview row={row} />
            <QuotaRecovery row={row} api={api} csrfToken={snapshot.csrfToken} canWrite={canWrite}
              title={writableTitle} onRefreshed={reloadSnapshot} />
            {remedy && <p className="small harness-remedy">{remedy}</p>}
            {candidates.length > 0 && <div className="harness-candidates">
              <h4>已尝试的位置（{candidates.length}）</h4>
              <ul>
                {candidates.map((candidate, index) => <li key={`${candidate.path ?? "unknown"}:${index}`}>
                  <code className="mono harness-path" title={candidate.path}>{candidate.path?.trim() || "未记录路径"}</code>
                  {candidate.source?.trim() && <span className="muted">{candidate.source.trim()}</span>}
                  {harnessCandidateText(candidate) && <span className="muted">{harnessCandidateText(candidate)}</span>}
                </li>)}
              </ul>
            </div>}
            {(row.status !== "ready" || !!row.manualPath) && <HarnessManualPath key={`${row.adapter}:${row.revision}`}
              row={row} name={name} disabled={!canWrite} busy={checking} title={writableTitle}
              onSave={path => void run({ kind: "set", adapter: row.adapter, path })} />}
          </div>}
        </li>;
      })}
    </ul>
  </section>;
}

/** The advanced fallback: one absolute path, or clearing it back to auto detection. */
function HarnessManualPath({ row, name, disabled, busy, title, onSave }: {
  row: HarnessHealth; name: string; disabled: boolean; busy: boolean;
  title: string | undefined; onSave: (path: string | null) => void;
}) {
  const [value, setValue] = useState(row.manualPath ?? "");
  const [localError, setLocalError] = useState("");
  function submit() {
    const path = value.trim();
    if (!absoluteHarnessPath(path)) {
      setLocalError("请输入可执行文件的绝对路径。");
      return;
    }
    setLocalError("");
    onSave(path);
  }
  return <form className="harness-manual" onSubmit={event => { event.preventDefault(); submit(); }}>
    <h4>高级：手动指定路径</h4>
    <p className="small muted">保存后立即检测；恢复自动检测将清除手动路径。</p>
    <label className="harness-manual-field"><span>可执行文件绝对路径</span>
      <input value={value} onChange={event => setValue(event.target.value)} placeholder="/usr/local/bin/codex"
        aria-label={`${name} 手动路径`} disabled={disabled} /></label>
    <div className="actions">
      <button type="submit" className="button small-button" aria-disabled={busy || disabled}
        title={title ?? (busy ? "正在保存并检测…" : "保存这条路径并立即检测")}>保存并检测</button>
      {row.manualPath && <button type="button" className="button small-button" aria-disabled={busy || disabled}
        title={title ?? "清除手动路径，恢复自动检测"} onClick={() => { setLocalError(""); onSave(null); }}>恢复自动检测</button>}
    </div>
    {localError && <p className="error-message" role="alert">{localError}</p>}
  </form>;
}
