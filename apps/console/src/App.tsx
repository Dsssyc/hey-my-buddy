import { useEffect, useState } from "react";
import { createApi } from "./api";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import { useConsole } from "./use-console";
import { useEditor } from "./use-editor";
import type { Editor } from "./use-editor";
import { useTheme } from "./theme";
import { UNRESOLVED_SAVE_NOTE } from "./console-session";
import { createAuthorityLatch } from "./console-session";
import { Objectives } from "./Objectives";
import { BuddyConfig } from "./BuddyConfig";
import { Settings } from "./Settings";
import { BackupAttention } from "./BackupAttention";
import { Badge, Icon } from "./ui";
import { errorText } from "./api";
import { refreshVisibleReads } from "./global-refresh";
import "./styles.css";
import buddyIcon from "../../../docs/assets/icon.svg?no-inline";

const tabs = { tasks: "委派记录", buddy: "Buddy 配置", settings: "设置" } as const;
type Tab = keyof typeof tabs;
const tabIcons = { tasks: "tasks", buddy: "models", settings: "settings" } as const;
/**
 * URL hash per tab. The 设置 page cannot keep `settings`: that hash was the
 * retired 路由配置 page, and the user's decision is that the old bookmark opens
 * Buddy 配置, where the routing settings now live.
 */
const tabHashes: Record<Tab, string> = { tasks: "tasks", buddy: "buddy", settings: "system" };
/** Old bookmarks of the retired "模型卡片" (`models`) and "路由配置" (`settings`) pages land on their successor. */
const legacyTabs: Record<string, Tab> = { models: "buddy", settings: "buddy" };
function currentTab(): Tab {
  const key = window.location.hash.slice(1).split("/")[0];
  const known = (Object.keys(tabs) as Tab[]).find(tab => tabHashes[tab] === key);
  return known ?? legacyTabs[key] ?? "tasks";
}

export function App({ suppliedApi }: { suppliedApi?: ConsoleApi }) {
  const [api] = useState(() => suppliedApi || createApi(window.location.pathname));
  const state = useConsole(api);
  return state.snapshot ? <Connected api={api} snapshot={state.snapshot} refresh={state.refresh} strictRefresh={() => state.refresh(undefined, true)} connectionError={state.error} updatedAt={state.updatedAt} /> :
    <main className="startup"><img className="brand-icon" src={buddyIcon} alt="" width="30" height="30" />
      <h1>{state.error ? "暂时无法连接黑板" : "正在连接本地黑板"}</h1>
      <p role={state.error ? "alert" : "status"}>{state.error || "正在连接…"}</p>
      {state.error && <button className="button primary" onClick={() => void state.refresh()}>重新连接</button>}
    </main>;
}

function gateStateText(snapshot: Snapshot) {
  return snapshot.gate.phase === "draining" ? "写入排队中" : "独占编辑中";
}

/**
 * The bottom save bar replaces the old global edit switch: it appears only
 * while the page holds a draft (unsaved or still unresolved edits) and offers
 * exactly 放弃 and 保存, plus 取消等待 while another save is ahead.
 */
function SaveBar({ editor, mutationsAvailable, describedBy }: {
  editor: Editor; mutationsAvailable: boolean; describedBy?: string;
}) {
  const label = editor.busy ? (editor.waiting ? "等待其他保存完成…" : "正在保存…")
    : editor.confirming ? "重试同一保存" : "保存";
  return <div className="save-bar" role="region" aria-label="未保存的修改">
    <span className="save-bar-state" role="status">{editor.dirty
      ? `${editor.changeCount} 项未保存`
      : "保存结果待核对"}</span>
    <div className="actions">
      {editor.waiting && <button type="button" className="button small-button" onClick={editor.cancelSave}>取消等待</button>}
      <button type="button" className="button small-button" aria-disabled={editor.busy || editor.confirming || undefined}
        onClick={() => void editor.discard()}>放弃</button>
      <button type="button" className="button primary small-button" aria-disabled={!mutationsAvailable || editor.busy || undefined}
        aria-describedby={describedBy} onClick={() => void editor.save()}>{label}</button>
    </div>
  </div>;
}

function Connected({ api, snapshot, refresh, strictRefresh, connectionError, updatedAt }: {
  api: ConsoleApi; snapshot: Snapshot; refresh: () => Promise<Snapshot | null>; strictRefresh: () => Promise<Snapshot | null>; connectionError: string; updatedAt: number | null;
}) {
  const [tab, setTab] = useState<Tab>(currentTab);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshNote, setRefreshNote] = useState("");
  const [visited, setVisited] = useState(() => new Set<Tab>([currentTab()]));
  const theme = useTheme();
  // One latch for the mounted page: the polling snapshot's own descriptor
  // decides write authority, and a definite board refusal (an expired or
  // invalid login) latches the loss for this session. This is the security
  // bottom line only — it never surfaces as a user-visible handoff state.
  const authority = useState(() => createAuthorityLatch())[0];
  const sessionWritable = authority.writable(snapshot);
  const writesAvailable = !connectionError && sessionWritable;
  const mutationsAvailable = writesAvailable && snapshot.capabilities.evaluationWriteGate !== false;
  const unavailableReason = connectionError
    ? "连接中断；请刷新重试，草稿已保留。"
    : !sessionWritable
      ? "登录已失效；请运行 buddy console 重新登录，草稿已保留。"
      : snapshot.capabilities.evaluationWriteGate === false
        ? "无写入资格；保存不可用。"
        : "";
  const editor = useEditor(api, snapshot, refresh, mutationsAvailable, unavailableReason, authority);
  function select(key: Tab) {
    setTab(key);
    setVisited(previous => previous.has(key) ? previous : new Set([...previous, key]));
  }
  useEffect(() => {
    const changed = () => select(currentTab());
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  const pending = new Set(snapshot.tasks.runs.filter(t => t.workflow?.awaitingHost)
    .map(t => t.delegation?.rootRunId || t.runId)).size;
  const saveRefusal = editor.saveBlockedReason
    || (editor.waiting ? "正在等待其他保存完成；可先取消等待。" : "");
  // P1.2: the internal table revision lives only in the connection tooltip.
  const connectionTitle = connectionError
    ? `连接中断：${connectionError} 请检查服务或刷新重试。`
    : `评价表 V${snapshot.tableRevision}`;
  async function refreshAll() {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshNote("正在刷新…");
    try {
      const outcomes = await Promise.allSettled([strictRefresh(), refreshVisibleReads()]);
      const failed = outcomes.find((result): result is PromiseRejectedResult => result.status === "rejected");
      if (failed) throw failed.reason;
      setRefreshNote(`已刷新 · ${new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date())}`);
    } catch (failure) { setRefreshNote(`刷新失败：${errorText(failure)}`); }
    finally { setRefreshing(false); }
  }
  const conflictTitle = editor.conflict ? `V${editor.conflict.basedOn} → V${editor.conflict.latest}` : undefined;
  const automaticRead = updatedAt === null ? "数据未更新" : `数据截至 ${new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(updatedAt)}`;
  // Draft banners belong to the Buddy settings page; the records page shows none.
  const draftTab = tab === "buddy";
  return <div className="app-shell">
    <a className="skip-link" href="#main" onClick={event => {
      event.preventDefault(); document.getElementById("main")?.focus();
    }}>跳至主要内容</a>
    <header className="app-header">
      <a className="brand" href="#tasks" aria-label="Hey my buddy" onClick={() => select("tasks")}><img className="brand-icon" src={buddyIcon} alt="" width="30" height="30" /><strong>Hey my buddy</strong></a>
      <nav className="primary-tabs" aria-label="主要导航">
        {(Object.keys(tabs) as Tab[]).map(key => <a key={key} href={"#" + tabHashes[key]} onClick={() => select(key)}
          aria-current={tab === key ? "page" : undefined} className={tab === key ? "active" : ""}>
          <Icon name={tabIcons[key]} /><span>{tabs[key]}</span>
          {key === "tasks" && pending > 0 && <span className="nav-count" title="最新记录中等待 Host 决定的目标">{pending}</span>}
          {key === "buddy" && editor.dirty && <span className="nav-unsaved" title="有未保存的修改"><span aria-hidden="true">•</span><span className="sr-only">有未保存的修改</span></span>}
        </a>)}
      </nav>
      <div className="header-status">
        {connectionError && <span className="connection" title={connectionTitle}>连接中断 · 请检查本地服务并刷新</span>}
        {snapshot.gate.phase !== "open" && <Badge tone="amber">{gateStateText(snapshot)}</Badge>}
        <button className="icon-button" aria-label="刷新工作台" title={refreshNote || automaticRead} disabled={refreshing} onClick={() => void refreshAll()}><span className={refreshing ? "refresh-spinning" : undefined}><Icon name="refresh" /></span></button>
      </div>
    </header>
    <main id="main" className="main-content" tabIndex={-1}>
      <h1 className="sr-only">{tabs[tab]}</h1>
      {connectionError && <p className="banner error-banner" role="alert">{connectionError}</p>}
      <BackupAttention report={snapshot.backupPreflight} />
      {draftTab && editor.conflict && !editor.confirming && <div className="banner conflict-banner" role="alert">
        <span title={conflictTitle}>设置已更新；请核对草稿。</span>
        {editor.rebaseConflicts.length > 0 && <ul className="conflict-details">
          {editor.rebaseConflicts.map(issue => <li key={`${issue.kind}:${issue.field}:${issue.profileId}`}>{issue.message}</li>)}
        </ul>}
        <button type="button" className="button small-button" aria-disabled={editor.busy}
          onClick={() => void editor.reloadLatest()}>重新加载最新版本</button>
        <button type="button" className="button small-button danger" aria-disabled={editor.busy}
          onClick={() => void editor.discard()}>放弃修改</button>
      </div>}
      {draftTab && editor.confirming && editor.uncertain && <div className="banner conflict-banner" role="status">
        <span>{UNRESOLVED_SAVE_NOTE}</span>
        <button type="button" className="button small-button" aria-disabled={editor.busy}
          onClick={() => void editor.save()}>重试同一保存</button>
        <button type="button" className="button small-button" aria-disabled={editor.busy}
          onClick={() => void editor.reloadLatest()}>重新加载最新版本</button>
      </div>}
      {draftTab && editor.blocked && <p className="banner guard-banner" role="status">{editor.blocked}</p>}
      {draftTab && editor.error && !editor.confirming && <p className="banner error-banner" role="alert">{editor.error}</p>}
      {draftTab && editor.notice && <p className="publication-notice" role="status">{editor.notice}</p>}
      {saveRefusal && <p id="editor-save-reason" className="sr-only">{saveRefusal}</p>}
      {/* Stable positions preserve page selections, drafts and scroll offsets.
          https://react.dev/learn/preserving-and-resetting-state */}
      {(Object.keys(tabs) as Tab[]).map(key => visited.has(key) && <section key={key} hidden={tab !== key}
        className="view-panel" aria-label={tabs[key]}>
        {key === "tasks" ? <Objectives snapshot={snapshot} api={api} refresh={refresh} active={tab === key}
          authority={authority} writesAvailable={writesAvailable} /> :
          key === "buddy" ? <BuddyConfig snapshot={snapshot} editor={editor} api={api} refresh={refresh} active={tab === key} mutationsAvailable={mutationsAvailable} /> :
            <Settings api={api} csrfToken={snapshot.csrfToken} connectionError={connectionError}
              access={snapshot.consoleAccess} refresh={refresh} writesAvailable={writesAvailable}
              theme={theme.choice} onTheme={theme.setChoice} />}
      </section>)}
      {draftTab && editor.mode && <SaveBar editor={editor} mutationsAvailable={mutationsAvailable}
        describedBy={saveRefusal ? "editor-save-reason" : undefined} />}
    </main>
  </div>;
}
