import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ConsoleApi, StorageApplyResult, StoragePlan } from "./api";
import { ApiError, errorText, uncertainResponse } from "./api";
import { clockTime } from "./objective-display";
import { useBackgroundInert } from "./modal";

/** Fixed display order (0.16 storage panel); unknown ids stay visible after these. */
const CATEGORY_ORDER = ["harnesses", "zcode", "workspaces", "runtimes", "backup", "durable"] as const;
const CATEGORY_LABEL: Record<string, string> = {
  harnesses: "Harness 私有会话",
  zcode: "ZCode 私有主目录",
  workspaces: "受管检出",
  runtimes: "运行时",
  backup: "备份",
  durable: "看板与记录",
};
/** Categories whose reclaimable column is structurally protected. */
const ALWAYS_PROTECTED = new Set(["backup", "durable"]);
/**
 * Short Chinese for the backend's stable protection reason codes
 * (src/hey_my_buddy/blackboard/tasks/storage.py and workflow.py `_cleanup_reasons`); unknown codes
 * stay visible as-is.
 */
const REASON_LABEL: Record<string, string> = {
  "shutdown-unconfirmed": "停止尚未确认",
  "grace-period": "宽限期未满",
  "acceptance-time-unproven": "验收时间未证实",
  "pending-continuations": "有待处理的续接",
  "open-requests": "有未决请求",
  "continuation-supported": "仍可能续接",
  "process-inspection-unavailable": "进程状态无法检查",
  "runtime-in-use": "运行时正在使用",
  "runtime-identity-unproven": "运行时身份未证实",
  "retention-history-unproven": "保留期记录未证实",
  "retained-runtime": "保留的运行时",
  "owner-unproven": "归属未证实",
  "user-account-protected": "账户目录由用户管理",
  "linked-path": "存在链接路径",
  "not-accepted": "目标尚未验收",
  "final-artifact-missing": "最终产物缺失",
  "integration-missing": "整合记录缺失",
  "unresolved-conflict": "有未解决的冲突",
  "active-children": "有进行中的协助任务",
  "workspace-dependency": "检出依赖未满足",
  "durable-record": "持久记录",
  // Apply-time per-item codes.
  "candidate-changed": "数据已变化",
  "STORAGE_CHANGED": "候选已变化",
  "STORAGE_UNSAFE": "移除路径身份已变化",
  "STORAGE_PROTECTED": "受保护的数据",
  "filesystem-error": "文件系统错误",
};

function reasonText(code: string): string {
  return REASON_LABEL[code] ?? code;
}

/** 1024-based KB/MB/GB with one decimal; the tooltip keeps the exact bytes. */
function byteText(bytes: number): { text: string; title: string } {
  if (!Number.isFinite(bytes) || bytes < 0) return { text: "未记录", title: "未记录" };
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes, unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  const text = unit === 0 ? `${Math.round(value)} ${units[unit]}`
    : `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
  return { text, title: `${bytes.toLocaleString("en-US")} 字节` };
}

type PlanPhase =
  | { kind: "idle" }
  | { kind: "planning" }
  | { kind: "planned" }
  | { kind: "applying" }
  | { kind: "applied" }
  /** The reply was lost or unreadable; the result is unknown and replayable. */
  | { kind: "unknown" }
  /** STORAGE_INCOMPLETE: deletion already started; resume the same identity. */
  | { kind: "incomplete" };

/**
 * The settings 存储 panel (0.16): reads `storage_plan` only on an explicit
 * 检查占用 click — entering the page and the 3-second refresh never trigger
 * it, because a plan scans disk and is private and expiring. Cleanup requires
 * a modal confirmation bound to the exact plan; a lost reply keeps the same
 * command identity so 重试同一请求 replays it. Orphan processes are listed
 * only. Reclamation is not an evaluation-table draft: the panel takes no draft,
 * no edit switch and no writer lease, so any authenticated window may use it.
 */
export function StoragePanel({ api, csrfToken, connectionError }: {
  api: ConsoleApi;
  csrfToken: string;
  connectionError: string;
}) {
  const [phase, setPhase] = useState<PlanPhase>({ kind: "idle" });
  const [plan, setPlan] = useState<StoragePlan | null>(null);
  const [planError, setPlanError] = useState("");
  const [result, setResult] = useState<StorageApplyResult | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [applyCommandId, setApplyCommandId] = useState("");
  const [showSkipped, setShowSkipped] = useState(false);
  const [showRemoved, setShowRemoved] = useState(false);
  const [reasonsOpen, setReasonsOpen] = useState<Set<string>>(new Set());
  const [now, setNow] = useState(() => Date.now());
  const alive = useRef(true);
  const inFlight = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);
  // Expiry displays and the confirm button's disabled state need a current
  // clock while a plan is shown; this is display-only, never a background task.
  useEffect(() => {
    if (phase.kind === "idle") return;
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, [phase.kind]);

  const disabled = !!connectionError;
  const disabledTitle = connectionError || undefined;
  const expired = useMemo(() => {
    if (!plan) return false;
    const at = Date.parse(plan.expiresAt);
    return Number.isFinite(at) ? now >= at : true;
  }, [plan, now]);
  const totalReclaimable = useMemo(() =>
    plan ? plan.categories.reduce((total, category) => total + (Number.isFinite(category.reclaimableBytes) ? category.reclaimableBytes : 0), 0) : 0,
  [plan]);
  const totalBytes = useMemo(() =>
    plan ? plan.categories.reduce((total, category) => total + (Number.isFinite(category.bytes) ? category.bytes : 0), 0) : 0,
  [plan]);

  const requestPlan = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setPhase({ kind: "planning" });
    setPlanError("");
    // A fresh check invalidates any open confirmation: the dialog must never
    // confirm a plan other than the one it showed.
    setConfirmOpen(false);
    try {
      const next = await api.storagePlan(csrfToken);
      if (!alive.current) return;
      setPlan(next);
      setResult(null);
      setShowSkipped(false);
      setApplyCommandId("");
      setPhase({ kind: "planned" });
    } catch (failure) {
      if (!alive.current) return;
      // The previously shown plan stays; a refusal never clears it.
      setPlanError(errorText(failure));
      setPhase(plan ? { kind: "planned" } : { kind: "idle" });
    } finally {
      inFlight.current = false;
    }
  }, [api, csrfToken, plan]);

  const applyPlan = useCallback(async (planId: string, commandId: string) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setPhase({ kind: "applying" });
    setPlanError("");
    try {
      const reply = await api.storageApply(planId, commandId, csrfToken);
      if (!alive.current) return;
      setResult(reply);
      setShowRemoved(false);
      setPhase({ kind: "applied" });
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && failure.code === "STORAGE_INCOMPLETE") {
        // Deletion already started and is resumable with the same identity;
        // this is neither a failure nor a claim that nothing ran.
        setPhase({ kind: "incomplete" });
        return;
      }
      if (uncertainResponse(failure)) {
        setPhase({ kind: "unknown" });
        return;
      }
      setPlanError(errorText(failure));
      setPhase({ kind: "planned" });
    } finally {
      inFlight.current = false;
    }
  }, [api, csrfToken]);

  function openConfirm() {
    if (!plan || expired || totalReclaimable <= 0) return;
    // One command identity per user-visible plan: a retry after a lost reply
    // reuses it instead of minting a second cleanup.
    if (!applyCommandId) setApplyCommandId(crypto.randomUUID());
    setConfirmOpen(true);
  }

  const orderedCategories = useMemo(() => {
    if (!plan) return [];
    const known = CATEGORY_ORDER.map(id => plan.categories.find(category => category.id === id))
      .filter((category): category is NonNullable<typeof category> => !!category);
    const unknown = plan.categories.filter(category => !(CATEGORY_ORDER as readonly string[]).includes(category.id));
    return [...known, ...unknown];
  }, [plan]);

  const reclaimTotalText = byteText(totalReclaimable);

  return <section className="panel storage-panel" aria-busy={phase.kind === "planning" || phase.kind === "applying"} aria-label="存储">
    <div className="panel-heading">
      <h2>存储</h2>
    </div>
    <div className="storage-overview" role="group" aria-label="占用概览">
      <div><span>总占用</span><strong title={plan ? byteText(totalBytes).title : undefined}>{plan ? byteText(totalBytes).text : "尚未检查"}</strong></div>
      <div><span>可回收</span><strong title={plan ? reclaimTotalText.title : undefined}>{plan ? reclaimTotalText.text : "尚未检查"}</strong></div>
    </div>
    {phase.kind === "idle" && !plan && <div className="actions">
      <button type="button" className="button" disabled={disabled} title={disabledTitle} onClick={() => void requestPlan()}>检查占用</button>
    </div>}
    {phase.kind === "planning" && !plan && <p role="status" className="small muted">正在检查…</p>}
    {planError && <p className="error-message" role="alert">{planError}
      <span className="actions"><button type="button" className="button small-button" onClick={() => void requestPlan()}>重试</button></span></p>}
    {plan && <div className="storage-result">
      <div className="storage-result-head">
        <span>{phase.kind === "applied"
          ? "已过期 · 请重新检查"
          : expired
            ? "计划已过期 · 请重新检查"
            : `检查于 ${clockTime(plan.createdAt)} · ${expiryText(plan, now)}内可清理`}</span>
        <button type="button" className="button small-button"
          disabled={disabled || phase.kind === "planning" || phase.kind === "applying" || phase.kind === "unknown" || phase.kind === "incomplete"}
          title={disabledTitle
            ?? (phase.kind === "applying" ? "正在清理…"
              : phase.kind === "unknown" || phase.kind === "incomplete"
                ? "清理结果未知；请先重试同一请求" : undefined)}
          onClick={() => void requestPlan()}>重新检查</button>
      </div>
      <table className="storage-table">
        <thead><tr><th scope="col">类别</th><th scope="col">占用</th><th scope="col">可回收</th><th scope="col"></th></tr></thead>
        <tbody>
          {orderedCategories.map(category => {
            const bytes = byteText(category.bytes);
            const protectedHere = ALWAYS_PROTECTED.has(category.id);
            const reclaim = category.reclaimableBytes > 0 ? byteText(category.reclaimableBytes) : null;
            const open = reasonsOpen.has(category.id);
            return <tr key={category.id}>
              <th scope="row">{CATEGORY_LABEL[category.id] ?? category.id}</th>
              <td title={bytes.title}>{bytes.text}</td>
              <td title={protectedHere ? "看板、记录及当前备份受保护" : reclaim?.title ?? "没有可回收数据"}>
                {reclaim ? reclaim.text : "—"}
              </td>
              <td>
                {category.reasons.length > 0 && <button type="button" className="storage-reasons-toggle"
                  aria-expanded={open} onClick={() => setReasonsOpen(previous => {
                    const next = new Set(previous);
                    if (next.has(category.id)) next.delete(category.id); else next.add(category.id);
                    return next;
                  })}>为何保留 {open ? "▾" : "▸"}</button>}
              </td>
            </tr>;
          })}
          <tr className="storage-total">
            <th scope="row">合计</th>
            <td title={byteText(totalBytes).title}>
              {byteText(totalBytes).text}
            </td>
            <td title={reclaimTotalText.title}>{totalReclaimable > 0 ? reclaimTotalText.text : "—"}</td>
            <td></td>
          </tr>
        </tbody>
      </table>
      {orderedCategories.filter(category => category.reasons.length > 0 && reasonsOpen.has(category.id)).map(category =>
        <div key={category.id} className="storage-reasons">
          <ul>{category.reasons.map((code, index) => <li key={index}>{reasonText(code)}</li>)}</ul>
        </div>)}
      {plan.orphanProcesses.length > 0 && <div className="storage-orphans">
        <h3>游离进程（{plan.orphanProcesses.length}）</h3>
        <p className="small muted">清理不会停止这些进程。</p>
        <ul>
          {plan.orphanProcesses.map(process => <li key={process.pid}>
            <span>{process.kind === "daemon" ? "服务" : process.kind === "supervisor" ? "执行进程" : process.kind || "未记录类型"}</span>
            <code>pid {process.pid}</code>
            {(process.stateDir || process.runtimeDir) && <span className="mono storage-orphan-path"
              title={[process.stateDir, process.runtimeDir].filter(Boolean).join("\n")}>
              {[process.stateDir, process.runtimeDir].filter(Boolean).join(" · ")}
            </span>}
          </li>)}
        </ul>
      </div>}
      {phase.kind === "applied" && result && <div className="storage-outcome" role="status">
        <p>已回收 {byteText(result.removedBytes).text}（{result.removed.length} 项）</p>
        {result.removed.length > 0 && <>
          <button type="button" className="inspector-link" aria-expanded={showRemoved}
            onClick={() => setShowRemoved(current => !current)}>{showRemoved ? "收起已删项目" : "查看已删项目"}</button>
          {showRemoved && <ul className="storage-skipped">
            {result.removed.map((item, index) => <li key={item.id || index}
              title={byteText(item.bytes).title}>{item.path}：{byteText(item.bytes).text}</li>)}
          </ul>}
        </>}
        {result.skipped.length > 0 && <>
          <p>跳过 {result.skipped.length} 项 · 数据已变化</p>
          <button type="button" className="inspector-link" aria-expanded={showSkipped}
            onClick={() => setShowSkipped(current => !current)}>{showSkipped ? "收起逐项原因" : "查看逐项原因"}</button>
          {showSkipped && <ul className="storage-skipped">
            {result.skipped.map((item, index) => <li key={item.id || index}>
              {item.path}：{item.reasons.length ? item.reasons.map(reasonText).join("、") : "数据已变化或条件不再满足"}
            </li>)}
          </ul>}
        </>}
      </div>}
      <div className="actions storage-actions">
        {(phase.kind === "unknown" || phase.kind === "incomplete") && <>
          <span role="status">{phase.kind === "unknown"
            ? "清理结果未知，可能已执行"
            : "清理未完成 · 已开始删除"}</span>
          {/* The durable receipt stays replayable after plan expiry; only a
              connection failure or a definitive reply ends the retry. */}
          <button type="button" className="button small-button" disabled={disabled}
            title={disabledTitle ?? "重试同一请求可继续或核对清理"}
            onClick={() => void applyPlan(plan.planId, applyCommandId)}>重试同一请求</button>
        </>}
        {phase.kind !== "unknown" && phase.kind !== "incomplete" && phase.kind !== "applied"
          && <button type="button" className="button danger"
            disabled={disabled || phase.kind === "applying" || phase.kind === "planning" || totalReclaimable <= 0 || expired}
            title={disabledTitle
              ?? (totalReclaimable <= 0 ? "没有可回收数据"
                : expired ? "计划已过期，请重新检查"
                  : phase.kind === "applying" ? "正在清理…" : undefined)}
            onClick={openConfirm}>清理可回收数据（{reclaimTotalText.text}）</button>}
        {phase.kind === "applying" && <span role="status">正在清理…</span>}
      </div>
    </div>}
    {confirmOpen && plan && <StorageConfirmDialog
      plan={plan} total={totalReclaimable} expired={expired}
      onCancel={() => setConfirmOpen(false)}
      onConfirm={() => {
        setConfirmOpen(false);
        void applyPlan(plan.planId, applyCommandId);
      }} />}
  </section>;
}

function expiryText(plan: StoragePlan, now: number): string {
  const at = Date.parse(plan.expiresAt);
  if (!Number.isFinite(at)) return "未知时限";
  const minutes = Math.max(0, Math.round((at - now) / 60000));
  if (minutes < 1) return "不到 1 分钟";
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} 小时 ${rest} 分` : `${hours} 小时`;
}

/** The modal cleanup confirmation: focus trap, Esc cancels, safe initial focus. */
function StorageConfirmDialog({ plan, total, expired, onCancel, onConfirm }: {
  plan: StoragePlan;
  total: number;
  expired: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const backdrop = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  useBackgroundInert(backdrop);
  useEffect(() => { cancelRef.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
        return;
      }
      if (event.key !== "Tab") return;
      const items = [cancelRef.current, confirmRef.current].filter((item): item is HTMLButtonElement => !!item);
      if (items.length < 2) return;
      const active = document.activeElement;
      if (!backdrop.current?.contains(active)) {
        event.preventDefault();
        items[0]!.focus();
      } else if (event.shiftKey && active === items[0]) {
        event.preventDefault();
        items[items.length - 1]!.focus();
      } else if (!event.shiftKey && active === items[items.length - 1]) {
        event.preventDefault();
        items[0]!.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);
  const rows = plan.categories.filter(category => category.reclaimableBytes > 0);
  return <div className="dialog-backdrop" ref={backdrop}>
    <div className="dialog stop-dialog" role="dialog" aria-modal="true" aria-labelledby="storage-confirm-title" aria-describedby="storage-confirm-body">
      <h2 id="storage-confirm-title">清理可回收数据</h2>
      <div id="storage-confirm-body">
        <ul>
          {rows.map(category => <li key={category.id}>
            {CATEGORY_LABEL[category.id] ?? category.id}：{byteText(category.reclaimableBytes).text}
          </li>)}
          <li>合计：{byteText(total).text}</li>
          <li>计划检查时间：{clockTime(plan.createdAt)}</li>
        </ul>
        <p>将逐项复核并跳过变化项目；看板、记录和当前备份不会删除。</p>
        {expired && <p className="error-message" role="alert">计划已过期；请关闭并重新检查</p>}
      </div>
      <div className="actions">
        <button ref={cancelRef} type="button" className="button" onClick={onCancel}>取消</button>
        <button ref={confirmRef} type="button" className="button danger" disabled={expired} onClick={onConfirm}>确认清理</button>
      </div>
    </div>
  </div>;
}
