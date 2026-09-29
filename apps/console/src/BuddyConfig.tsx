import { useEffect, useRef, useState } from "react";
import type { ConsoleApi, HarnessCandidate, HarnessHealth, HarnessStatus } from "./api";
import { ApiError, errorText, snapshotHarnesses } from "./api";
import { historyView } from "./draft";
import type { Editor } from "./use-editor";
import type { Snapshot } from "./types";
import { Badge, Empty } from "./ui";
import { familyKey, modelFamilies } from "./console-data";
import { MAX_HISTORY_PROFILES, PROFILE_PAGE_SIZE, useProfileHistory } from "./use-profile-history";
import { SplitView } from "./SplitView";
import { EvaluationHistory } from "./EvaluationHistory";
import { FamilyDetail, effortTagId } from "./FamilyDetail";
import { RoutingStatusBar } from "./RoutingStatusBar";
import { familySearchText, harnessGroups, harnessName, harnessUnavailableText } from "./buddy-display";
import { dayClock } from "./objective-display";
import { LOGIN_EXPIRED_ACTION_REFUSAL } from "./console-session";
import { quotaView } from "./host-workflow";

/** Status wording and the non-colour badge tone; an unknown future state stays visible as recorded. */
const HARNESS_STATUS_LABEL: Record<HarnessStatus, string> = {
  ready: "已找到",
  missing: "未找到",
  "login-required": "需要登录",
  unhealthy: "不健康",
  unknown: "尚未检测",
};
const HARNESS_STATUS_TONE: Record<HarnessStatus, "neutral" | "green" | "amber" | "red"> = {
  ready: "green",
  missing: "red",
  "login-required": "amber",
  unhealthy: "red",
  unknown: "neutral",
};
/** What the user can do when no remedy was recorded for that state. */
const HARNESS_STATUS_REMEDY: Record<HarnessStatus, string> = {
  ready: "",
  unknown: "点击“重新检测”读取当前状态。",
  missing: "安装对应的 CLI，或在下方填写可执行文件的绝对路径后保存。",
  "login-required": "先在该 harness 中完成登录，再点击“重新检测”。",
  unhealthy: "检查该 CLI 能否独立运行；也可以填写其他位置的手动路径。",
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
  version_unverified: "版本未验证",
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
  return HARNESS_STATUS_LABEL[status as HarnessStatus] ?? status;
}
function harnessStatusTone(status: string): "neutral" | "green" | "amber" | "red" {
  return HARNESS_STATUS_TONE[status as HarnessStatus] ?? "neutral";
}
function harnessRemedy(row: HarnessHealth): string {
  return row.remedy?.trim() || HARNESS_STATUS_REMEDY[row.status as HarnessStatus] || "";
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
function HarnessStatus({ snapshot, api, refresh, mutationsAvailable, sessionWritable }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  mutationsAvailable: boolean; sessionWritable: boolean;
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
  const canWrite = mutationsAvailable && sessionWritable;
  const rows = recorded.map(row => replies[row.adapter] ?? row);
  if (!rows.length) return null;
  const readyCount = rows.filter(row => row.status === "ready").length;
  const attention = rows.some(row => row.status !== "ready");
  const lastChecked = rows.map(row => row.checkedAt ?? "").filter(Boolean).sort().at(-1) ?? "";
  // ADR-018 §23: the recorded near-limit reminder is visible without expanding a
  // row; it is always named as the latest observation, never as live quota.
  const quotaAlerts = rows.map(row => ({ row, quota: quotaView(row.quota) }))
    .filter(item => item.quota?.alert);
  const quotaAlertText = quotaAlerts.map(item => harnessName(item.row.adapter)).join("、");
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
        {quotaAlerts.length > 0 && <>
          <span aria-hidden="true" className="routing-sep">｜</span>
          <span className="quota-alert-line" title={`基于最近一次记录的额度观测，不是实时账户额度：${quotaAlertText}`}>
            额度提醒：{quotaAlertText}
          </span>
        </>}
      </span>
      <button type="button" className="button small-button" aria-disabled={busy !== "" || !canWrite}
        title={writableTitle ?? (busy === "*" ? "正在重新检测…" : "按当前记录重新检测全部 harness，不会调用模型")}
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
        return <li key={row.adapter} className={"harness-status-row harness-" + row.status}>
          <div className="harness-status-rowline">
            <span className="harness-name">{name}</span>
            <Badge tone={harnessStatusTone(row.status)}>{harnessStatusText(row.status)}</Badge>
            {quota?.alert && <Badge tone={quota.stale ? "neutral" : "amber"}>
              {quota.limitReported ? "原生记录报告额度限制" : quota.windows.some(window => window.atLimit) ? "额度已到上限" : "额度接近上限"}
            </Badge>}
            <span className="harness-summary" title={harnessTitle(row)}>{harnessSummary(row)}</span>
            <span className="harness-row-actions">
              <button type="button" className="icon-button harness-row-toggle" aria-expanded={isOpen}
                aria-label={`${name} ${isOpen ? "收起详情" : "检测详情"}`}
                title={`${name} ${isOpen ? "收起详情" : "检测详情"}`}
                onClick={() => toggle(row.adapter)}>{isOpen ? "▾" : "▸"}</button>
              <button type="button" className="button small-button" aria-disabled={checking || !canWrite}
                title={writableTitle ?? (checking ? "正在重新检测…" : `按当前记录重新检测 ${name}，不会调用模型`)}
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
              <div><dt>来源</dt><dd>{harnessSource(row)}</dd></div>
              <div><dt>上次检测</dt><dd>{row.checkedAt ? dayClock(row.checkedAt) : "未记录"}</dd></div>
              {row.manualPath && <div><dt>手动路径</dt><dd><code className="mono harness-path">{row.manualPath}</code></dd></div>}
              <div><dt>额度观测</dt><dd>
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
            {remedy && <p className="small harness-remedy">修复办法：{remedy}</p>}
            {candidates.length > 0 && <div className="harness-candidates">
              <h4>已尝试的位置（{candidates.length}）</h4>
              <ul>
                {candidates.map((candidate, index) => <li key={`${candidate.path ?? "unknown"}:${index}`}>
                  <code className="mono harness-path" title={candidate.path}>{candidate.path?.trim() || "未记录路径"}</code>
                  {candidate.source?.trim() && <span className="muted">来源：{candidate.source.trim()}</span>}
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
      setLocalError("请输入可执行文件的绝对路径（以 / 开头，或 Windows 盘符路径）。");
      return;
    }
    setLocalError("");
    onSave(path);
  }
  return <form className="harness-manual" onSubmit={event => { event.preventDefault(); submit(); }}>
    <h4>高级：手动指定路径</h4>
    <p className="small muted">自动检测失败时可用。保存后按该路径重新检测；恢复自动检测会清除这条手动设置。</p>
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

/**
 * "Buddy 配置": which configurations may work as a buddy, and which effort is
 * the Router. The left list groups model families by harness; the right side
 * edits the selected family directly. The routing state sits in one line on
 * top. Nothing here recommends a configuration.
 */
export function BuddyConfig({ snapshot, editor, api, refresh, active = true, mutationsAvailable = true }: {
  snapshot: Snapshot; editor: Editor; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  active?: boolean; mutationsAvailable?: boolean;
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState(""), [enabledOnly, setEnabledOnly] = useState(false);
  /** The user asked to see unavailable configurations as well. */
  const [showUnavailable, setShowUnavailable] = useState(false);
  /** Harness groups the user folded or unfolded, overriding the default. */
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  /** Families added by this page's own discovery, marked "新" for this session. */
  const [fresh, setFresh] = useState<Set<string>>(() => new Set());
  const [focusRequest, setFocusRequest] = useState<{ profileId: string; n: number } | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [note, setNote] = useState("");
  const [guard, setGuard] = useState("");
  const history = useProfileHistory(api, snapshot.csrfToken, snapshot.tableRevision,
    { query, adapter: "", enabled: showUnavailable });
  const data = editor.view;
  const recorded = historyView(snapshot, history.page);
  const sessionWritable = editor.sessionWritable;
  const families = modelFamilies(data.profiles);
  const needle = query.trim().toLowerCase();
  // Two combinable filters. A family is listed when at least one configuration
  // survives both: 只看已启用 keeps families with an enabled effort among the
  // shown ones, and 显示不可用配置 unhides the unavailable efforts, so a family
  // with no available configuration only appears once that box is checked.
  const visible = families.filter(g => {
    const shown = showUnavailable ? g.profiles : g.profiles.filter(p => p.available);
    return shown.length > 0
      && (!enabledOnly || shown.some(p => p.enabled))
      && (!needle || g.profiles.some(p => familySearchText(p).includes(needle)));
  });
  const groups = harnessGroups(visible, data.profiles);
  // The checkbox counts every unavailable configuration: the table-wide count
  // the board records — including a row the bounded snapshot still lists, such
  // as a retained Router — never below what this view already holds. A backend
  // that omits the field still gets the honest local count instead of an
  // invented number.
  const loadedUnavailable = data.profiles.filter(p => !p.available).length;
  const unavailableCount = typeof snapshot.unavailableProfileCount === "number"
    && Number.isFinite(snapshot.unavailableProfileCount)
    ? Math.max(snapshot.unavailableProfileCount, loadedUnavailable)
    : loadedUnavailable;
  // A checked box asks for every unavailable configuration, so the bounded
  // pages continue on their own; the hook's display budget is the hard stop,
  // and this counter keeps a server that repeats a cursor from being asked
  // forever.
  const autoPages = useRef(0);
  // A new filter generation gets a fresh page budget: its first page is a new
  // read, not a continuation of the previous chain.
  useEffect(() => { autoPages.current = 0; }, [query, snapshot.tableRevision]);
  useEffect(() => {
    if (!showUnavailable) { autoPages.current = 0; return; }
    if (history.error || history.loading || !history.hasMore) return;
    if (autoPages.current >= Math.ceil(MAX_HISTORY_PROFILES / PROFILE_PAGE_SIZE)) return;
    autoPages.current += 1;
    history.loadMore();
  }, [showUnavailable, history.error, history.loading, history.hasMore, history.loadMore]);
  const routerIds = new Set([data.configuration.fastRouterProfileId, data.configuration.reviewRouterProfileId]);
  const family = selected ? families.find(g => g.key === selected) : undefined;
  // A refusal recorded by 发现模型 is stale once the page can act again: it is
  // shown only while the action would still be refused, never as a lingering
  // banner over usable controls.
  const shownGuard = editor.editing && mutationsAvailable ? "" : guard;
  // Loaded retained rows join the local view and, while editing, the draft. The
  // revision dependency also clears a page that belonged to the previous table
  // before it could seed a baseline, even when both renders see `null`.
  useEffect(() => { editor.adoptHistory(history.page); }, [history.page, snapshot.tableRevision]);
  useEffect(() => {
    if (focusRequest) document.getElementById(effortTagId(focusRequest.profileId))?.focus();
  }, [focusRequest]);
  function showRouter(profileId: string) {
    const target = data.profiles.find(p => p.profileId === profileId);
    if (!target) return;
    setHistoryOpen(false);
    setSelected(familyKey(target));
    setFolded(previous => ({ ...previous, [target.adapter]: false }));
    setFocusRequest(previous => ({ profileId, n: (previous?.n ?? 0) + 1 }));
  }
  async function discover() {
    if (busy || !mutationsAvailable) {
      setGuard(!sessionWritable
        ? LOGIN_EXPIRED_ACTION_REFUSAL
        : !mutationsAvailable ? "连接中断或缺少写入资格：暂时不能发现模型，草稿仍保留。" : "");
      return;
    }
    setBusy(true); editor.setAuxiliaryBusy(true); setError(""); setGuard(""); setNote("");
    try {
      await api.command("model_catalog_refresh", { requestId: crypto.randomUUID() }, snapshot.csrfToken);
      // The program publishes directory facts itself; this page only re-reads the
      // snapshot and rebases the local draft. It never uploads profiles or cards.
      const next = await refresh();
      if (next) {
        const added = next.profiles.filter(p => !snapshot.profiles.some(known => known.profileId === p.profileId));
        editor.rebase(next);
        setFresh(previous => new Set([...previous, ...added.map(familyKey)]));
        setNote(added.length
          ? `目录已更新：新增 ${added.length} 个配置（默认未启用）。`
          : "目录已更新：没有新的配置。");
      }
    } catch (failure) { setError(errorText(failure)); }
    finally { setBusy(false); editor.setAuxiliaryBusy(false); }
  }
  const list = <section className="panel list-panel" aria-label="模型家族">
    <div className="panel-toolbar"><h2>模型 <span className="muted">{visible.length}</span></h2>
      <div className="actions"><button className="button small-button" aria-pressed={historyOpen} onClick={() => setHistoryOpen(true)}>更新记录</button>
        <button className="button small-button" aria-disabled={busy || !mutationsAvailable}
          title={!sessionWritable ? LOGIN_EXPIRED_ACTION_REFUSAL : !mutationsAvailable ? "连接中断或缺少写入资格" : "由程序发布目录事实，不影响用户设置"}
          onClick={() => void discover()}>发现模型</button></div></div>
    <div className="list-filters">
      <label className="search"><span className="sr-only">搜索模型</span>
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索模型、Harness、提供方" /></label>
      <label className="check-field"><input type="checkbox" checked={enabledOnly} onChange={e => setEnabledOnly(e.target.checked)} />只看已启用</label>
      {unavailableCount > 0 && <label className="check-field"><input type="checkbox" checked={showUnavailable}
        onChange={e => {
          setShowUnavailable(e.target.checked);
          // Checking the box is the explicit request that starts the bounded
          // `model_profiles` read; unchecking only hides what is already loaded.
          if (e.target.checked) history.open();
        }} />显示不可用配置（{unavailableCount}）</label>}
      {showUnavailable && history.loading && <p className="small muted" role="status">正在读取不可用配置…</p>}
      {showUnavailable && history.error && <p className="banner guard-banner" role="alert">{history.error}
        <button type="button" className="button small-button" onClick={history.reload}>重试读取</button></p>}
      {showUnavailable && history.limitReached && <p className="small muted">已读取到显示上限（{MAX_HISTORY_PROFILES} 个配置）；请用搜索查找更早的配置。</p>}
      {(shownGuard || error) && <p className="banner guard-banner" role="status">{shownGuard || error}</p>}
      {note && <p className="success-message" role="status">{note}</p>}
    </div>
    <div className="list-scroll" tabIndex={0} aria-label="模型条目">
      {groups.length ? groups.map(group => {
        const groupProfiles = group.families.flatMap(g => g.profiles);
        const groupEnabled = groupProfiles.filter(p => p.enabled).length;
        const isFolded = folded[group.adapter] ?? groupEnabled === 0;
        const reason = group.unavailable ? harnessUnavailableText(group) : "";
        return <section key={group.adapter} className="harness-group" aria-label={group.name}>
          <button type="button" className="group-heading harness-heading" aria-expanded={!isFolded}
            onClick={() => setFolded(previous => ({ ...previous, [group.adapter]: !isFolded }))}>
            <span>{isFolded ? "▸" : "▾"} {group.name}{group.unavailable ? "（不可用）" : ""}</span>
            <span className="group-count">已启用 {groupEnabled}/{groupProfiles.length}</span>
          </button>
          {group.unavailable && <p className="small muted harness-reason">原因：{reason}</p>}
          {!isFolded && <ul className="profile-list">{group.families.map(g => {
            const enabled = g.profiles.filter(p => p.enabled).length;
            const hasRouter = g.profiles.some(p => routerIds.has(p.profileId));
            // With 显示不可用配置 on, every shown family says how many of its
            // configurations are unavailable and which reasons were recorded.
            const unavailable = g.profiles.filter(p => !p.available);
            const reasons = [...new Set(unavailable.map(p => p.unavailableReason?.trim() ?? "").filter(Boolean))];
            const unavailableText = reasons.join("；");
            const unavailableShown = showUnavailable && unavailable.length > 0;
            const unavailableMark = unavailable.length === g.profiles.length
              ? "不可用" : `不可用 ${unavailable.length}/${g.profiles.length}`;
            const isNew = fresh.has(g.key) || g.profiles.some(profile => profile.newlyDiscovered);
            const label = [g.name, `已启用 ${enabled}/${g.profiles.length}`, hasRouter ? "Router" : "",
              unavailableShown ? (unavailableText ? `${unavailableMark}（${unavailableText}）` : unavailableMark) : "",
              isNew ? "新" : ""].filter(Boolean).join("，");
            return <li key={g.key}>
              <button className={"profile-row family-row" + (family?.key === g.key ? " selected" : "")}
                aria-pressed={family?.key === g.key} aria-label={label}
                onClick={() => { setHistoryOpen(false); setSelected(g.key); }}>
                <span className="row-between"><strong>{g.name}</strong>
                  <span className="family-row-marks">
                    {isNew && <span className="family-new-mark">新</span>}
                    {hasRouter && <span className="router-mark" title="Router 所在家族">Router</span>}
                    <span className="small muted">{enabled}/{g.profiles.length}</span>
                  </span></span>
                {unavailableShown && <span className="small unavailable-mark" title={unavailableText || undefined}>
                  {unavailableMark}{unavailableText ? ` · ${unavailableText}` : ""}</span>}
              </button>
            </li>;
          })}</ul>}
        </section>;
      }) : <Empty title={data.profiles.length ? "没有匹配的模型" : "尚未接入模型"}>
        {data.profiles.length ? "调整搜索，或改选上方的筛选（只看已启用 / 显示不可用配置）。" : "点击“发现模型”读取本机目录。"}</Empty>}
    </div>
  </section>;
  return <div className="buddy-page">
    <RoutingStatusBar data={data} snapshot={snapshot} editor={editor} onShowRouter={showRouter} />
    <HarnessStatus snapshot={snapshot} api={api} refresh={refresh}
      mutationsAvailable={mutationsAvailable} sessionWritable={sessionWritable} />
    <SplitView selected={historyOpen || !!family} list={list} detail={
      <aside className="panel detail-panel" aria-label="模型家族详情">
        <div className="model-detail-content" hidden={!historyOpen}>
          <EvaluationHistory snapshot={recorded} api={api} active={active && historyOpen} onBack={() => setHistoryOpen(false)} />
        </div>
        <div className="model-detail-content" hidden={historyOpen}>
          {family ? <FamilyDetail key={family.key} family={family} data={data} recorded={recorded} editor={editor}
            isNew={fresh.has(family.key)} onCloseList={() => setSelected(null)} />
            : <div className="detail-placeholder"><h2>选择模型家族</h2><p>从左侧选择一个模型家族，查看档位、偏好与评价。</p></div>}
        </div>
      </aside>
    } />
  </div>;
}
