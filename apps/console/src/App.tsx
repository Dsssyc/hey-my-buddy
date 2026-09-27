import { useEffect, useMemo, useRef, useState } from "react";
import { createApi } from "./api";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import { useConsole } from "./use-console";
import { useEditor } from "./use-editor";
import { useTheme } from "./theme";
import {
  READ_ONLY_ACTION_REFUSAL,
  READ_ONLY_BANNER,
  READ_ONLY_DRAFT_NOTE,
  READ_ONLY_SAVE_REFUSAL,
  UNRESOLVED_HANDOFF_NOTE,
  createAuthorityLatch,
} from "./console-session";
import { Objectives } from "./Objectives";
import { Models } from "./Models";
import { Settings } from "./Settings";
import { Badge, Icon } from "./ui";
import "./styles.css";
import buddyIcon from "../../../docs/assets/icon.svg?no-inline";

const tabs = { tasks: "委派记录", models: "模型卡片", settings: "路由配置" } as const;
type Tab = keyof typeof tabs;
function currentTab(): Tab {
  const key = window.location.hash.slice(1);
  return key in tabs ? key as Tab : "tasks";
}

export function App({ suppliedApi }: { suppliedApi?: ConsoleApi }) {
  const [api] = useState(() => suppliedApi || createApi(window.location.pathname));
  const state = useConsole(api);
  return state.snapshot ? <Connected api={api} snapshot={state.snapshot} refresh={state.refresh} connectionError={state.error} /> :
    <main className="startup"><img className="brand-icon" src={buddyIcon} alt="" width="30" height="30" />
      <h1>{state.error ? "暂时无法连接黑板" : "正在连接本地黑板"}</h1>
      <p role={state.error ? "alert" : "status"}>{state.error || "读取持久记录，不调用模型。"}</p>
      {state.error && <button className="button primary" onClick={() => void state.refresh()}>重新连接</button>}
    </main>;
}

function gateStateText(snapshot: Snapshot) {
  return snapshot.gate.phase === "draining" ? "写入排队中" : "独占编辑中";
}

function Connected({ api, snapshot, refresh, connectionError }: {
  api: ConsoleApi; snapshot: Snapshot; refresh: () => Promise<Snapshot | null>; connectionError: string;
}) {
  const [tab, setTab] = useState<Tab>(currentTab);
  const [visited, setVisited] = useState(() => new Set<Tab>([currentTab()]));
  const theme = useTheme();
  const editSwitch = useRef<HTMLButtonElement>(null);
  const exitWasOpen = useRef(false);
  // One latch for the mounted page: the polling snapshot's own descriptor
  // decides write authority, a definite board refusal latches the loss for this
  // session (an older writable snapshot cannot restore it), and a failed
  // authenticated poll pauses writes without claiming a new-window takeover.
  const authority = useRef(createAuthorityLatch()).current;
  const sessionWritable = authority.writable(snapshot);
  const writesAvailable = !connectionError && sessionWritable;
  const mutationsAvailable = writesAvailable && snapshot.capabilities.evaluationWriteGate !== false;
  const unavailableReason = connectionError
    ? "与本地黑板的连接已中断：保存已停用，草稿仍保留在本页。"
    : !sessionWritable
      ? READ_ONLY_SAVE_REFUSAL
      : snapshot.capabilities.evaluationWriteGate === false
        ? "当前会话没有评价表写入资格：保存已停用。"
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
  // Closing the exit dialog returns focus to the control that opened it.
  useEffect(() => {
    if (editor.exitPrompt) exitWasOpen.current = true;
    else if (exitWasOpen.current) {
      exitWasOpen.current = false;
      editSwitch.current?.focus();
    }
  }, [editor.exitPrompt]);
  const pending = new Set(snapshot.tasks.runs.filter(t => t.workflow?.awaitingHost)
    .map(t => t.delegation?.rootRunId || t.runId)).size;
  const saveLabel = editor.confirming ? "确认保存结果" : "保存更改";
  const draftCount = editor.changedProfiles.length
    ? ` · ${editor.changedProfiles.length} 个配置`
    : editor.configurationDirty ? " · 决策模型配置" : "";
  const saveRefusal = editor.saveBlockedReason
    || (editor.waiting ? "正在等待编辑资格；可先取消等待。" : "");
  function requestExit() {
    if (editor.mode || editor.busy) void editor.requestExit();
    else editor.enter();
  }
  return <div className="app-shell">
    <a className="skip-link" href="#main" inert={editor.exitPrompt || undefined} onClick={event => {
      event.preventDefault(); document.getElementById("main")?.focus();
    }}>跳至主要内容</a>
    <header className="app-header" inert={editor.exitPrompt || undefined}>
      <a className="brand" href="#tasks" aria-label="hey my buddy" onClick={() => select("tasks")}><img className="brand-icon" src={buddyIcon} alt="" width="30" height="30" /><strong>hey my buddy</strong></a>
      <nav className="primary-tabs" aria-label="主要导航">
        {(Object.keys(tabs) as Tab[]).map(key => <a key={key} href={"#" + key} onClick={() => select(key)}
          aria-current={tab === key ? "page" : undefined} className={tab === key ? "active" : ""}>
          <Icon name={key} /><span>{tabs[key]}</span>
          {key === "tasks" && pending > 0 && <span className="nav-count" title="最新记录中等待 Host 决定的目标">{pending}</span>}
        </a>)}
      </nav>
      <div className="header-status">
        <span className="connection" title="关闭页面不影响后台任务">{connectionError ? "连接中断" : "已连接"}</span>
        {!sessionWritable && <Badge tone="amber">只读会话</Badge>}
        {snapshot.gate.phase !== "open" && <Badge tone="amber">{gateStateText(snapshot)}</Badge>}
        <span className="small muted">V{snapshot.tableRevision}</span>
        <div className="edit-cluster">
          <button ref={editSwitch} type="button" role="switch" aria-checked={editor.mode} className="button small-button edit-switch"
            aria-disabled={editor.busy || (!sessionWritable && !editor.mode) || undefined} onClick={requestExit}
            title={editor.busy ? "保存进行中；结果未确认前不会丢弃草稿"
              : !sessionWritable && !editor.mode ? READ_ONLY_ACTION_REFUSAL
                : editor.mode ? "退出编辑模式" : "开启本地草稿，不占用编辑资格"}>
            <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
            <span className="switch-label">编辑模式</span>
          </button>
          <span className={"edit-state" + (editor.mode ? editor.dirty ? " unsaved" : " active" : "")} role="status">
            {editor.mode ? editor.dirty ? `编辑中 · 未保存${draftCount}` : "编辑中" : "只读"}
          </span>
          {editor.mode && <button type="button" className="button primary small-button" aria-disabled={!mutationsAvailable || editor.busy}
            aria-describedby={saveRefusal ? "editor-save-reason" : undefined}
            onClick={() => void editor.save()}>{editor.busy ? (editor.waiting ? "等待编辑资格…" : "正在保存…") : saveLabel}</button>}
          {editor.mode && editor.waiting && <button type="button" className="button small-button" onClick={editor.cancelSave}>取消等待</button>}
        </div>
        <button type="button" role="switch" aria-checked={theme.theme === "dark"} className="icon-button theme-switch"
          aria-label="深色主题" title={theme.theme === "dark" ? "切换到浅色主题" : "切换到深色主题"} onClick={theme.toggle}>
          <Icon name={theme.theme === "dark" ? "moon" : "sun"} />
        </button>
        <button className="icon-button" aria-label="刷新工作台" title="只读刷新，不调用模型" onClick={() => void refresh()}><Icon name="refresh" /></button>
      </div>
    </header>
    <main id="main" className="main-content" tabIndex={-1} inert={editor.exitPrompt || undefined}>
      <h1 className="sr-only">{tabs[tab]}</h1>
      {!sessionWritable && <div className="banner readonly-banner" role="status" data-console-session="read-only">
        <strong>此页面已由新窗口接管写权限</strong>
        <span>{READ_ONLY_BANNER}</span>
        {editor.mode && editor.dirty && <span>{READ_ONLY_DRAFT_NOTE}</span>}
        {editor.uncertain && <span>{UNRESOLVED_HANDOFF_NOTE}</span>}
      </div>}
      {connectionError && <p className="banner error-banner" role="alert">{connectionError}</p>}
      {editor.conflict && !editor.confirming && <div className="banner conflict-banner" role="alert">
        <span>共享评价表已发布 V{editor.conflict.latest}，你的草稿基于 V{editor.conflict.basedOn}。草稿仍保留：重新加载会采用最新发布版本，放弃修改会丢弃本页草稿。</span>
        {editor.rebaseConflicts.length > 0 && <ul className="conflict-details">
          {editor.rebaseConflicts.map(issue => <li key={`${issue.kind}:${issue.field}:${issue.profileId}`}>{issue.message}</li>)}
        </ul>}
        <button type="button" className="button small-button" aria-disabled={editor.busy}
          onClick={() => void editor.reloadLatest()}>重新加载最新版本</button>
        <button type="button" className="button small-button danger" aria-disabled={editor.busy}
          onClick={() => void editor.discard()}>放弃修改</button>
      </div>}
      {editor.blocked && <p className="banner guard-banner" role="status">{editor.blocked}</p>}
      {editor.error && <p className="banner error-banner" role="alert">{editor.error}</p>}
      {editor.notice && <p className="publication-notice" role="status">{editor.notice}</p>}
      {saveRefusal && <p id="editor-save-reason" className="sr-only">{saveRefusal}</p>}
      {/* Stable positions preserve page selections, drafts and scroll offsets.
          https://react.dev/learn/preserving-and-resetting-state */}
      {(Object.keys(tabs) as Tab[]).map(key => visited.has(key) && <section key={key} hidden={tab !== key}
        className={"view-panel" + (key !== "tasks" && editor.mode ? " edit-mode" : "")} aria-label={tabs[key]}>
        {key === "tasks" ? <Objectives snapshot={snapshot} api={api} refresh={refresh} active={tab === key}
          authority={authority} writesAvailable={writesAvailable} /> :
          key === "models" ? <Models snapshot={snapshot} editor={editor} api={api} refresh={refresh} active={tab === key} mutationsAvailable={mutationsAvailable} /> :
            <Settings snapshot={snapshot} editor={editor} />}
      </section>)}
    </main>
    {editor.exitPrompt && <ExitDialog onKeep={editor.keepEditing} onSave={() => void editor.saveAndExit()} onDiscard={() => void editor.discard()} />}
  </div>;
}

/**
 * Modal confirmation with a real focus trap: the background is inert, focus
 * starts on the safe "continue editing" action so Enter cannot publish, Tab
 * wraps inside the dialog, and the caller restores focus to the edit switch.
 */
function ExitDialog({ onKeep, onSave, onDiscard }: { onKeep: () => void; onSave: () => void; onDiscard: () => void }) {
  const backdrop = useRef<HTMLDivElement>(null);
  const initialFocus = useRef<HTMLButtonElement>(null);
  useEffect(() => { initialFocus.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onKeep();
        return;
      }
      if (event.key !== "Tab") return;
      const items = [...(backdrop.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") ?? [])];
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      const active = document.activeElement;
      if (!backdrop.current?.contains(active)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onKeep]);
  return <div className="dialog-backdrop" ref={backdrop}>
    <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="exit-edit-title" aria-describedby="exit-edit-body">
      <h2 id="exit-edit-title">有未保存的修改</h2>
      <p id="exit-edit-body">保存并退出会发布新的评价版本；放弃修改会丢弃本页草稿，不影响已经发布的内容。</p>
      <div className="actions">
        <button type="button" className="button primary" onClick={onSave}>保存并退出</button>
        <button type="button" className="button danger" onClick={onDiscard}>放弃修改</button>
        <button ref={initialFocus} type="button" className="button" onClick={onKeep}>继续编辑</button>
      </div>
    </div>
  </div>;
}
