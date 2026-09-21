import { useEffect, useState } from "react";
import { createApi } from "./api";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import { useConsole } from "./use-console";
import { useEditor } from "./use-editor";
import { Tasks } from "./Tasks";
import { Models } from "./Models";
import { Settings } from "./Settings";
import { Badge, Icon } from "./ui";
import "./styles.css";

const tabs = {
  tasks: ["工作队列", "让每一次委派，都有清楚的去向。"],
  models: ["模型与经验", "共同积累的能力记录，保留每条经验的适用条件。"],
  settings: ["决策配置", "明确偏好，让日常选择保持轻量。"],
} as const;
type Tab = keyof typeof tabs;
function currentTab(): Tab {
  const key = window.location.hash.slice(1);
  return key in tabs ? (key as Tab) : "tasks";
}

export function App({ suppliedApi }: { suppliedApi?: ConsoleApi }) {
  const [api] = useState(
    () => suppliedApi || createApi(window.location.pathname),
  );
  const state = useConsole(api);
  return state.snapshot ? (
    <Connected
      api={api}
      snapshot={state.snapshot}
      refresh={state.refresh}
      connectionError={state.error}
    />
  ) : (
    <main className="startup">
      <div className="brand-icon">b</div>
      <span className="eyebrow">HEY MY BUDDY</span>
      <h1>{state.error ? "暂时无法连接黑板" : "正在连接本地黑板"}</h1>
      <p role={state.error ? "alert" : "status"}>
        {state.error || "读取配置、评价与任务状态，不调用模型。"}
      </p>
      {state.error && (
        <button className="button primary" onClick={() => void state.refresh()}>
          重新连接
        </button>
      )}
    </main>
  );
}

function Connected({
  api,
  snapshot,
  refresh,
  connectionError,
}: {
  api: ConsoleApi;
  snapshot: Snapshot;
  refresh: () => Promise<Snapshot | null>;
  connectionError: string;
}) {
  const [tab, setTab] = useState<Tab>(currentTab);
  const editor = useEditor(api, snapshot, refresh);
  useEffect(() => {
    const changed = () => setTab(currentTab());
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  const running = snapshot.tasks.runs.filter(
    (t) => t.status === "running",
  ).length;
  const review = snapshot.tasks.runs.filter(
    (t) =>
      !t.acceptedAt && ["completed", "failed", "cancelled"].includes(t.status),
  ).length;
  const gateLabel =
    snapshot.gate.phase === "open"
      ? "评价表可读"
      : snapshot.gate.phase === "draining"
        ? "等待决策收束"
        : "独占编辑中";
  const editingMine =
    snapshot.gate.writer?.writerId === editor.grant?.writerId && !!editor.grant;
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        跳至主要内容
      </a>
      <aside className="sidebar">
        <a className="brand" href="#tasks">
          <span className="brand-icon">b</span>
          <span>
            <strong>hey my buddy</strong>
            <small>本地协作工作台</small>
          </span>
        </a>
        <div className="sidebar-label">工作空间</div>
        <nav aria-label="主要导航">
          {(Object.keys(tabs) as Tab[]).map((key) => (
            <a
              key={key}
              href={`#${key}`}
              aria-current={tab === key ? "page" : undefined}
              className={tab === key ? "active" : ""}
            >
              <Icon name={key} />
              <span>{tabs[key][0]}</span>
              {key === "tasks" && snapshot.tasks.total > 0 && (
                <span className="nav-count">{snapshot.tasks.total}</span>
              )}
            </a>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="connection">
            <span
              className={`connection-dot ${connectionError ? "offline" : ""}`}
            />
            {connectionError ? "连接中断" : "本地黑板已连接"}
          </div>
          <p>页面关闭后，后台任务继续运行。</p>
          <span className="version-label">PYTHON BLACKBOARD · C-TWO</span>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <span className="breadcrumb">
            工作台 <span>/</span> {tabs[tab][0]}
          </span>
          <div className="topbar-actions">
            <Badge tone={snapshot.gate.phase === "open" ? "green" : "amber"}>
              {gateLabel}
            </Badge>
            <button
              className="icon-button"
              aria-label="刷新工作台"
              title="刷新，不调用模型"
              onClick={() => void refresh()}
            >
              <Icon name="refresh" />
            </button>
          </div>
        </header>
        <main id="main" className="main-content">
          <div className="page-heading">
            <div>
              <span className="eyebrow">BUDDY / {tab.toUpperCase()}</span>
              <h1>{tabs[tab][0]}</h1>
              <p>{tabs[tab][1]}</p>
            </div>
            <div className="revision">
              <span>共享评价</span>
              <strong>V{snapshot.tableRevision}</strong>
            </div>
          </div>
          <section className="overview-strip" aria-label="当前状态">
            <div>
              <span>执行中的任务</span>
              <strong>
                {running.toString().padStart(2, "0")}
                <small>RUNNING</small>
              </strong>
            </div>
            <div>
              <span>等待验收</span>
              <strong>
                {review.toString().padStart(2, "0")}
                <small>REVIEW</small>
              </strong>
            </div>
            <div>
              <span>已接入配置</span>
              <strong>
                {snapshot.profiles.length.toString().padStart(2, "0")}
                <small>PROFILES</small>
              </strong>
            </div>
            <div>
              <span>待整理证据</span>
              <strong>
                {snapshot.pendingEvidence.toString().padStart(2, "0")}
                <small>EVIDENCE</small>
              </strong>
            </div>
          </section>
          {connectionError && (
            <p className="banner error-banner" role="alert">
              {connectionError}
            </p>
          )}
          <section
            className={`editor-bar ${editor.grant ? "editor-active" : ""}`}
            aria-label="评价表编辑状态"
          >
            <div>
              <Icon name={editor.grant ? "clock" : "models"} />
              <span>
                {editor.grant
                  ? editingMine
                    ? "正在编辑草稿，保存后一次发布新版本。"
                    : "编辑请求已排队，等待已有读者或写者结束。"
                  : snapshot.gate.phase === "open"
                    ? "共享评价按版本发布，实际执行与评价维护互不混淆。"
                    : "评价表正在更新，新的选择请求将等待；已有执行继续。"}
              </span>
            </div>
            <div className="actions">
              {editor.grant ? (
                <>
                  <button
                    className="button small-button"
                    disabled={editor.busy || editor.uncertain}
                    onClick={() => void editor.discard()}
                  >
                    取消编辑
                  </button>
                  <button
                    className="button primary small-button"
                    disabled={
                      editor.busy || (!editor.hasAuthority && !editor.uncertain)
                    }
                    onClick={() => void editor.save()}
                  >
                    {editor.busy
                      ? "正在处理…"
                      : editor.uncertain
                        ? "确认保存结果"
                        : "发布新版本"}
                  </button>
                </>
              ) : (
                <button
                  className="button small-button"
                  disabled={editor.busy}
                  onClick={() => void editor.begin()}
                >
                  {snapshot.gate.phase === "open" ? "编辑评价表" : "排队编辑"}
                </button>
              )}
            </div>
          </section>
          {editor.draft && !editor.grant && (
            <div className="banner error-banner">
              <p>草稿未发布，编辑资格已失效。可先复制保留内容，再重新编辑。</p>
              <button
                className="button small-button"
                onClick={() => void editor.discard()}
              >
                放弃旧草稿
              </button>
            </div>
          )}
          {editor.error && (
            <p className="banner error-banner" role="alert">
              {editor.error}
            </p>
          )}
          {editor.notice && (
            <p className="banner success-banner" role="status">
              {editor.notice}
            </p>
          )}
          {tab === "tasks" ? (
            <Tasks snapshot={snapshot} api={api} refresh={refresh} />
          ) : tab === "models" ? (
            <Models
              snapshot={snapshot}
              editor={editor}
              api={api}
              refresh={refresh}
            />
          ) : (
            <Settings
              snapshot={snapshot}
              editor={editor}
              api={api}
              refresh={refresh}
            />
          )}
          <footer className="page-footer">
            <span>当前展示持久记录 · 每 3 秒只读刷新</span>
            <span>控制权清楚，经验有据。</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
