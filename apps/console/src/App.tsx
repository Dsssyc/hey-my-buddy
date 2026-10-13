import { cn } from "cn";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { StateMark } from "@/components/ui/state-mark";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { buddyAttention } from "./buddies/attention";
import { attentionHints } from "./buddies/BuddyHeader";
import { createApi } from "./api";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import { useConsole } from "./use-console";
import { useEditor } from "./use-editor";
import type { Editor } from "./use-editor";
import { useTheme } from "./theme";
import { createAuthorityLatch } from "./console-session";
import { formatClock, formatDateTime } from "./i18n/format";
import { MacroTasks } from "./MacroTasks";
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

/** The logo on the top bar, in the bar's own colours: the bar is dark in both themes. */
function BrandMark() {
  return <svg viewBox="3 3 58 58" className="size-[22px] shrink-0" aria-hidden="true">
    <polygon className="fill-bar-live" points="12,13 22,13 22,61 12,54" />
    <polygon className="fill-bar-live" points="22,32 30,28 30,38 22,42" />
    <polygon className="fill-bar-ink" points="42,3 52,10 52,51 42,51" />
    <polygon className="fill-bar-ink" points="34,26 42,22 42,32 34,36" />
    <polygon className="fill-bar-hand" points="30,28 34,26 34,36 30,38" />
  </svg>;
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
 * The save bar exists only while the page holds a draft: changes that are not
 * saved, or a save whose result is not settled. It offers to discard and to
 * save. A button that cannot
 * act now still takes the click, so that the editor can say why.
 */
function SaveBar({ editor, mutationsAvailable, describedBy }: {
  editor: Editor; mutationsAvailable: boolean; describedBy?: string;
}) {
  const { t } = useTranslation("shell");
  const label = editor.busy ? t("draft.saving")
    : editor.confirming ? t("draft.retry") : t("draft.save");
  return <div className="flex shrink-0 flex-wrap items-center justify-end gap-x-3 gap-y-2 border-t bg-card px-3 py-2.5 sm:px-6" role="region" aria-label={t("draft.label")}>
    <span className="mr-auto flex items-center gap-2 text-hand" role="status"><StateMark kind="hand" />{editor.dirty
      ? t("draft.unsaved", { n: editor.changeCount })
      : t("draft.unresolved")}</span>
    <Button variant="outline" aria-disabled={editor.busy || editor.confirming || undefined}
      onClick={() => void editor.discard()}>{t("draft.discard")}</Button>
    <Button aria-disabled={!mutationsAvailable || editor.busy || undefined}
      aria-describedby={describedBy} onClick={() => void editor.save()}>{label}</Button>
  </div>;
}

/** One line said above a page; its mark and colour tell a failure from something that waits for the user. */
function Notice({ tone, role, children }: { tone: "fail" | "hand" | "plain"; role: "alert" | "status"; children: ReactNode }) {
  return <div role={role} className={cn("flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5 border-b px-3 py-2 sm:px-6",
    tone === "fail" ? "bg-fail-wash text-fail" : tone === "hand" ? "bg-hand-wash text-hand" : "bg-card text-ink2")}>
    {tone !== "plain" && <StateMark kind={tone} />}
    {children}
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
  const mutationsAvailable = writesAvailable;
  const unavailableReason = connectionError
    ? t("draft.unavailable.connection")
    : !sessionWritable ? t("draft.unavailable.login") : "";
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
  const saveRefusal = editor.saveBlockedReason;
  // Draft banners belong to the Buddy settings page; the records page shows none.
  const draftTab = tab === "buddies";
  const verifiedAt = api.readVerifiedAt?.(snapshot) ?? null;
  // What a mark on a tab means; the tab shows it on hover and on focus.
  const buddies = useTranslation("buddies");
  const waiting = attentionHints(buddyAttention(editor.view, snapshot), buddies.t);
  const tabHints: Partial<Record<Tab, string>> = {
    tasks: pending > 0 ? t("nav.pendingHost") : undefined,
    buddies: editor.dirty ? t("nav.unsaved") : waiting.roles ?? waiting.harness,
  };
  return <TooltipProvider delayDuration={300}><div className="flex h-dvh min-h-0 flex-col overflow-hidden">
    <a className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground"
      href="#main" onClick={event => { event.preventDefault(); document.getElementById("main")?.focus(); }}>{t("skipToContent")}</a>
    <header className="flex h-11 shrink-0 items-center gap-2 bg-bar px-3 text-bar-ink sm:gap-7 sm:px-4">
      <a className="flex shrink-0 items-center gap-2 rounded-sm outline-none focus-visible:ring-[3px] focus-visible:ring-bar-live/60"
        href="#tasks" aria-label="Hey my buddy" onClick={() => select("tasks")}>
        <BrandMark />
        <span className="font-semibold tracking-tight max-sm:hidden">Hey my buddy</span>
      </a>
      <nav className="flex h-full" aria-label={t("nav.label")}>
        {tabs.map(key => {
          const hint = tabHints[key];
          const link = <a key={key} href={"#" + key} onClick={() => select(key)} aria-current={tab === key ? "page" : undefined}
            className="relative flex items-center gap-[7px] px-3.5 whitespace-nowrap text-bar-ink2 outline-none transition-colors duration-150 hover:text-bar-ink focus-visible:bg-bar-ink/10 motion-reduce:transition-none aria-[current=page]:font-medium aria-[current=page]:text-bar-ink aria-[current=page]:after:absolute aria-[current=page]:after:inset-x-3.5 aria-[current=page]:after:bottom-0 aria-[current=page]:after:h-0.5 aria-[current=page]:after:bg-bar-live">
            {t(`nav.${key}`)}
            {key === "tasks" && pending > 0 && <span className="min-w-[18px] rounded-sm bg-bar-hand px-[5px] text-center text-xs leading-[18px] font-semibold text-bar tabular-nums">{pending}</span>}
            {key === "buddies" && hint && <span className="size-1.5 rounded-full bg-bar-hand" aria-hidden="true" />}
            {hint && <span className="sr-only">{hint}</span>}
          </a>;
          return hint ? <Tooltip key={key}><TooltipTrigger asChild>{link}</TooltipTrigger><TooltipContent>{hint}</TooltipContent></Tooltip> : link;
        })}
      </nav>
      <div className="ml-auto flex min-w-0 items-center gap-3 text-xs text-bar-ink2">
        {/* The bar shows the time alone; what it is the time of is said on hover and to a screen reader. */}
        <p className={connectionError ? "truncate text-bar-fail" : "truncate"}>
          {connectionError && <span className="max-sm:hidden">{t("connection.lost")} · </span>}
          {verifiedAt !== null
            ? <Tooltip><TooltipTrigger asChild><span><span className="sr-only">{t("snapshot.verified")} </span>
              <time className="font-mono tabular-nums" dateTime={new Date(verifiedAt).toISOString()}>{readTime(verifiedAt)}</time></span></TooltipTrigger>
              <TooltipContent>{t("snapshot.verified")}</TooltipContent></Tooltip>
            : t("snapshot.unverified")}
        </p>
      </div>
    </header>
    <main id="main" className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden outline-none" tabIndex={-1}>
      <h1 className="sr-only">{t(`nav.${tab}`)}</h1>
      {/* What is said above a page: the lost connection and the state of a draft. */}
      <div className="flex shrink-0 flex-col empty:hidden">
        {connectionError && <Notice tone="fail" role="alert">
          <span className="min-w-0 wrap-anywhere">{connectionError}</span>
          <Button variant="outline" size="sm" onClick={() => void refresh()}>{t("connection.retry")}</Button>
        </Notice>}
        {draftTab && editor.confirming && editor.uncertain && <Notice tone="hand" role="status">
          <span>{t("draft.unresolvedNote")}</span>
          <Button variant="outline" size="sm" aria-disabled={editor.busy || undefined}
            onClick={() => void editor.save()}>{t("draft.retry")}</Button>
          <Button variant="outline" size="sm" aria-disabled={editor.busy || undefined}
            onClick={() => void editor.reloadLatest()}>{t("draft.reload")}</Button>
        </Notice>}
        {draftTab && editor.blocked && <Notice tone="plain" role="status">{editor.blocked}</Notice>}
        {draftTab && editor.error && !editor.confirming && <Notice tone="fail" role="alert">{editor.error}</Notice>}
        {draftTab && editor.notice && <Notice tone="plain" role="status">{editor.notice}</Notice>}
      </div>
      {saveRefusal && <p id="editor-save-reason" className="sr-only">{saveRefusal}</p>}
      {/* Stable positions preserve page selections, drafts and scroll offsets.
          https://react.dev/learn/preserving-and-resetting-state */}
      {tabs.map(key => visited.has(key) && <section key={key} hidden={tab !== key}
        className={"min-h-0 min-w-0 flex-1 overflow-hidden" + (key === "buddies" ? "" : " p-2 sm:p-4")} aria-label={t(`nav.${key}`)}>
        {key === "tasks" ? <MacroTasks snapshot={snapshot} api={api} refresh={refresh} active={tab === key}
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
