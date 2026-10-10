import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { createApi } from "./api";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import { useConsole } from "./use-console";
import { useEditor } from "./use-editor";
import type { Editor } from "./use-editor";
import { useTheme } from "./theme";
import { UNRESOLVED_SAVE_NOTE } from "./console-session";
import { createAuthorityLatch } from "./console-session";
import { formatClock, formatDateTime } from "./i18n/format";
import { Objectives } from "./Objectives";
import { BuddyConfig } from "./BuddyConfig";
import { Settings } from "./Settings";
import buddyIcon from "../../../docs/assets/icon.svg?no-inline";

/** The pages. Each is addressed by its own name as the URL hash and named by `nav.<page>` in the text. */
const tabs = ["tasks", "buddies", "settings"] as const;
type Tab = (typeof tabs)[number];
/** The page the URL hash names; any other hash opens the tasks. */
function currentTab(): Tab {
  const key = window.location.hash.slice(1).split("/")[0];
  return tabs.find(tab => tab === key) ?? "tasks";
}

/** The clock time of a read made today, and the date with it once the read is older. */
function readTime(ms: number): string {
  return new Date(ms).toDateString() === new Date().toDateString() ? formatClock(ms) : formatDateTime(ms);
}

export function App({ suppliedApi }: { suppliedApi?: ConsoleApi }) {
  const { t } = useTranslation("shell");
  const [api] = useState(() => suppliedApi || createApi(window.location.pathname));
  const state = useConsole(api);
  return state.snapshot ? <Connected api={api} snapshot={state.snapshot} refresh={state.refresh} connectionError={state.error} /> :
    <main className="flex min-h-dvh flex-col items-center justify-center gap-5 p-6 text-center">
      <img src={buddyIcon} alt="" width="32" height="32" className="size-8" />
      <h1 className="text-lg font-semibold">{state.error ? t("startup.failed") : t("startup.connecting")}</h1>
      <p className="text-muted-foreground" role={state.error ? "alert" : "status"}>{state.error || t("startup.waiting")}</p>
      {state.error && <Button onClick={() => void state.refresh()}>{t("startup.reconnect")}</Button>}
    </main>;
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

function Connected({ api, snapshot, refresh, connectionError }: {
  api: ConsoleApi; snapshot: Snapshot; refresh: () => Promise<Snapshot | null>; connectionError: string;
}) {
  const { t } = useTranslation("shell");
  const [tab, setTab] = useState<Tab>(currentTab);
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
    ? "连接中断；请重试连接，草稿已保留。"
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
  // The server's board-wide count of micro tasks awaiting their Host —
  // computed over every run, never inferred from a page of recent rows.
  const pending = snapshot.tasks.pendingCount;
  const saveRefusal = editor.saveBlockedReason
    || (editor.waiting ? "正在等待其他保存完成；可先取消等待。" : "");
  const conflictTitle = editor.conflict ? `V${editor.conflict.basedOn} → V${editor.conflict.latest}` : undefined;
  // Draft banners belong to the Buddy settings page; the records page shows none.
  const draftTab = tab === "buddies";
  const verifiedAt = api.readVerifiedAt?.(snapshot) ?? null;
  // What a mark on a tab means; the tab shows it on hover and on focus.
  const tabHints: Partial<Record<Tab, string>> = {
    tasks: pending > 0 ? t("nav.pendingHost") : undefined,
    buddies: editor.dirty ? t("nav.unsaved") : undefined,
  };
  return <TooltipProvider delayDuration={300}><div className="flex h-dvh min-h-0 flex-col overflow-hidden">
    <a className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground"
      href="#main" onClick={event => { event.preventDefault(); document.getElementById("main")?.focus(); }}>{t("skipToContent")}</a>
    <header className="flex h-12 shrink-0 items-center gap-2 border-b bg-card px-3 sm:gap-6 sm:px-4">
      <a className="flex shrink-0 items-center gap-2 rounded-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        href="#tasks" aria-label="Hey my buddy" onClick={() => select("tasks")}>
        <img src={buddyIcon} alt="" width="24" height="24" className="size-6" />
        <span className="text-sm font-semibold tracking-tight max-sm:hidden">Hey my buddy</span>
      </a>
      <nav className="flex h-full" aria-label={t("nav.label")}>
        {tabs.map(key => {
          const hint = tabHints[key];
          const link = <a key={key} href={"#" + key} onClick={() => select(key)} aria-current={tab === key ? "page" : undefined}
            className="relative flex items-center gap-1.5 px-3 text-sm whitespace-nowrap text-muted-foreground outline-none hover:text-foreground focus-visible:bg-accent aria-[current=page]:font-medium aria-[current=page]:text-foreground aria-[current=page]:after:absolute aria-[current=page]:after:inset-x-3 aria-[current=page]:after:bottom-0 aria-[current=page]:after:h-0.5 aria-[current=page]:after:bg-primary">
            {t(`nav.${key}`)}
            {key === "tasks" && pending > 0 && <Badge tone="warning" className="tabular-nums">{pending}</Badge>}
            {key === "buddies" && editor.dirty && <span className="size-1.5 rounded-full bg-warning" aria-hidden="true" />}
            {hint && <span className="sr-only">{hint}</span>}
          </a>;
          return hint ? <Tooltip key={key}><TooltipTrigger asChild>{link}</TooltipTrigger><TooltipContent>{hint}</TooltipContent></Tooltip> : link;
        })}
      </nav>
      <div className="ml-auto flex min-w-0 items-center gap-3 text-xs text-muted-foreground">
        {snapshot.gate.phase !== "open" && <Badge tone="warning">{snapshot.gate.phase === "draining" ? t("gate.draining") : t("gate.exclusive")}</Badge>}
        {/* A narrow bar keeps the time and leaves the words to the alert below it. */}
        <p className={connectionError ? "truncate text-destructive tabular-nums" : "truncate tabular-nums"}>
          {connectionError && <span className="max-sm:hidden">{t("connection.lost")} · </span>}
          {verifiedAt !== null
            ? <><span className="max-sm:hidden">{t("snapshot.verified")} </span><time dateTime={new Date(verifiedAt).toISOString()}>{readTime(verifiedAt)}</time></>
            : t("snapshot.unverified")}
        </p>
      </div>
    </header>
    <main id="main" className="flex min-h-0 min-w-0 flex-1 flex-col gap-2 overflow-hidden p-2 outline-none sm:p-4" tabIndex={-1}>
      <h1 className="sr-only">{t(`nav.${tab}`)}</h1>
      {connectionError && <p className="flex shrink-0 flex-wrap items-center gap-3 rounded-md border border-danger-line bg-danger-wash px-3 py-2 text-destructive" role="alert">
        <span className="min-w-0 wrap-anywhere">{connectionError}</span>
        <Button variant="outline" size="sm" onClick={() => void refresh()}>{t("connection.retry")}</Button>
      </p>}
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
      {tabs.map(key => visited.has(key) && <section key={key} hidden={tab !== key}
        className="min-h-0 min-w-0 flex-1 overflow-hidden" aria-label={t(`nav.${key}`)}>
        {key === "tasks" ? <Objectives snapshot={snapshot} api={api} refresh={refresh} active={tab === key}
          authority={authority} writesAvailable={writesAvailable} /> :
          key === "buddies" ? <BuddyConfig snapshot={snapshot} editor={editor} api={api} refresh={refresh} active={tab === key} mutationsAvailable={mutationsAvailable} /> :
            <Settings api={api} csrfToken={snapshot.csrfToken} connectionError={connectionError}
              access={snapshot.consoleAccess} refresh={refresh} writesAvailable={writesAvailable}
              active={tab === "settings"}
              theme={theme.choice} onTheme={theme.setChoice} />}
      </section>)}
      {draftTab && editor.mode && <SaveBar editor={editor} mutationsAvailable={mutationsAvailable}
        describedBy={saveRefusal ? "editor-save-reason" : undefined} />}
    </main>
  </div></TooltipProvider>;
}
