import { useEffect, useMemo, useState } from "react";
import { createApi } from "./api";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import { useConsole } from "./use-console";
import { useEditor } from "./use-editor";
import { makeDraft } from "./draft";
import { Tasks } from "./Tasks";
import { Models } from "./Models";
import { Settings } from "./Settings";
import { Badge, Icon } from "./ui";
import "./styles.css";

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
    <main className="startup"><div className="brand-icon">b</div>
      <h1>{state.error ? "暂时无法连接黑板" : "正在连接本地黑板"}</h1>
      <p role={state.error ? "alert" : "status"}>{state.error || "读取持久记录，不调用模型。"}</p>
      {state.error && <button className="button primary" onClick={() => void state.refresh()}>重新连接</button>}
    </main>;
}

function Connected({ api, snapshot, refresh, connectionError }: {
  api: ConsoleApi; snapshot: Snapshot; refresh: () => Promise<Snapshot | null>; connectionError: string;
}) {
  const [tab, setTab] = useState<Tab>(currentTab);
  const [visited, setVisited] = useState(() => new Set<Tab>([currentTab()]));
  const editor = useEditor(api, snapshot, refresh);
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
  const gateLabel = snapshot.gate.phase === "open" ? "评价表可读" :
    snapshot.gate.phase === "draining" ? "等待决策收束" :
      snapshot.gate.writer?.kind === "maintenance" ? "评价维护窗口" : "独占编辑中";
  const editingMine = snapshot.gate.writer?.writerId === editor.grant?.writerId && !!editor.grant;
  const baseline = useMemo(() => makeDraft(snapshot), [snapshot]);
  const changed = new Set<string>();
  if (editor.draft) for (const key of ["profiles", "cards", "preferences"] as const) {
    const before = new Map(baseline[key].map(p => [p.profileId, JSON.stringify(p)]));
    const after = new Map(editor.draft[key].map(p => [p.profileId, JSON.stringify(p)]));
    for (const item of [...baseline[key], ...editor.draft[key]]) {
      if (before.get(item.profileId) !== after.get(item.profileId)) changed.add(item.profileId);
    }
  }
  return <div className="app-shell">
    <a className="skip-link" href="#main" onClick={event => {
      event.preventDefault(); document.getElementById("main")?.focus();
    }}>跳至主要内容</a>
    <header className="app-header">
      <a className="brand" href="#tasks" onClick={() => select("tasks")}><span className="brand-icon">b</span><strong>hey my buddy</strong></a>
      <nav className="primary-tabs" aria-label="主要导航">
        {(Object.keys(tabs) as Tab[]).map(key => <a key={key} href={"#" + key} onClick={() => select(key)}
          aria-current={tab === key ? "page" : undefined} className={tab === key ? "active" : ""}>
          <Icon name={key} /><span>{tabs[key]}</span>
          {key === "tasks" && pending > 0 && <span className="nav-count" title="最新记录中等待 Host 决定的目标">{pending}</span>}
        </a>)}
      </nav>
      <div className="header-status">
        <span className="connection" title="关闭页面不影响后台任务">{connectionError ? "连接中断" : "已连接"}</span>
        <span className="small muted">V{snapshot.tableRevision}</span>
        <button className="icon-button" aria-label="刷新工作台" title="只读刷新，不调用模型" onClick={() => void refresh()}><Icon name="refresh" /></button>
      </div>
    </header>
    <main id="main" className="main-content" tabIndex={-1}>
      <h1 className="sr-only">{tabs[tab]}</h1>
      {connectionError && <p className="banner error-banner" role="alert">{connectionError}</p>}
      {(tab !== "tasks" || editor.draft || editor.grant || snapshot.gate.phase !== "open") &&
        <section className={"editor-bar " + (editor.grant ? "editor-active" : "")} aria-label="评价表编辑状态">
          <div><Badge tone={snapshot.gate.phase === "open" ? "green" : "amber"}>{gateLabel}</Badge>
            <span>{editor.grant ? editingMine ? "草稿中 · " + changed.size + " 个配置有改动" : "编辑请求已排队" :
              editor.draft ? "草稿尚未发布" : "配置与评价按版本发布"}</span>
          </div>
          <div className="actions">{editor.grant ? <>
            <button className="button small-button" disabled={editor.busy || editor.uncertain} onClick={() => void editor.discard()}>取消编辑</button>
            <button className="button primary small-button" disabled={editor.busy || (!editor.hasAuthority && !editor.uncertain)}
              onClick={() => void editor.save()}>{editor.busy ? "正在处理…" : editor.uncertain ? "确认保存结果" : "发布新版本"}</button>
          </> : <button className="button small-button" disabled={editor.busy} onClick={() => void editor.begin()}>
            {snapshot.gate.phase === "open" ? "编辑评价表" : "排队编辑"}</button>}</div>
        </section>}
      {editor.draft && !editor.grant && <div className="banner error-banner">
        <span>草稿未发布，编辑资格已失效。请保留需要的内容，再重新编辑。</span>
        <button className="button small-button" onClick={() => void editor.discard()}>放弃旧草稿</button>
      </div>}
      {editor.error && <p className="banner error-banner" role="alert">{editor.error}</p>}
      {editor.notice && <p className="publication-notice" role="status">{editor.notice}</p>}
      {/* Stable positions preserve page selections, drafts and scroll offsets.
          https://react.dev/learn/preserving-and-resetting-state */}
      {(Object.keys(tabs) as Tab[]).map(key => visited.has(key) && <section key={key} hidden={tab !== key} className="view-panel" aria-label={tabs[key]}>
        {key === "tasks" ? <Tasks snapshot={snapshot} api={api} refresh={refresh} active={tab === key} /> :
          key === "models" ? <Models snapshot={snapshot} editor={editor} api={api} refresh={refresh} active={tab === key} /> :
            <Settings snapshot={snapshot} editor={editor} />}
      </section>)}
    </main>
  </div>;
}
