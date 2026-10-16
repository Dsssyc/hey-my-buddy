import { useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { cn } from "cn";
import { Trans, useTranslation } from "react-i18next";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task } from "./types";
import { Hint } from "./components/hint";
import { useTextClipped } from "./components/use-text-clipped";
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import { ScrollArea } from "./components/ui/scroll-area";
import { Collapsible, CollapsibleContent } from "./components/ui/collapsible";
import { StateMark } from "./components/ui/state-mark";
import { TooltipProvider } from "./components/ui/tooltip";
import { ListFold } from "./MacroTaskList";
import { formatCalendarDate, formatClockMinutes, formatNumber } from "./i18n/format";
import { taskTitle } from "./task-state";
import { TaskDetail } from "./task-detail/TaskDetail";
import { stateWord } from "./task-detail/Header";
import { createTaskDetailClient } from "./task-detail/client";
import { macroTaskKey } from "./task-detail/location";
import { useTaskHistory } from "./use-task-history";
import { useRecordAnchor } from "./use-record-anchor";

function Entry({ task, selected, onSelect }: { task: Task; selected: boolean; onSelect: () => void }) {
  const { t } = useTranslation("tasks");
  const detail = useTranslation("taskDetail").t;
  const title = taskTitle(task);
  const text = title.source === "none" ? t("unnamed") : title.text;
  const [ref, clipped] = useTextClipped<HTMLSpanElement>(text);
  const word = stateWord(task, task.lineage?.rootLeadRunId != null);
  const kind = task.lineage?.kind;
  const buddy = task.lineage?.buddy;
  const label = kind === "helper" ? t("helper") : kind === "decision" ? t("calculation") : kind === "execution" ? t("command") : null;
  const hint = title.source === "task" ? `${title.fullText} · ${t("source")}` : clipped ? title.fullText : undefined;
  return <li data-run-id={task.runId}><Hint text={hint}>
    <button type="button" data-slot="record-entry" aria-pressed={selected} onClick={onSelect}
      className={cn("relative grid w-full grid-cols-[18px_minmax(0,1fr)_auto] items-baseline gap-x-2 gap-y-0.5 border-t px-4 py-2.5 text-left text-sm outline-none hover:bg-muted aria-pressed:hover:bg-live-wash transition-colors duration-150 motion-reduce:transition-none focus-visible:ring-[3px] focus-visible:ring-ring/50", selected ? "bg-live-wash before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-live" : "bg-transparent")}>
      <StateMark kind={word.mark} className="self-center"/><span className="sr-only">{detail(`state.${word.key}`)}</span>
      <span ref={ref} data-slot="record-title" className="truncate font-medium">{text}</span>
      <time dateTime={task.createdAt} className="whitespace-nowrap text-xs text-ink3 tabular-nums">{formatClockMinutes(Date.parse(task.createdAt))}</time>
      <span className="col-start-2 col-span-2 truncate text-xs text-ink2">{label && <><span className="text-foreground">{label}</span>{" · "}</>}{task.lineage?.project?.label ?? t("unknownProject")}{buddy ? ` · ${buddy.model} / ${buddy.effort}` : null}</span>
    </button>
  </Hint></li>;
}

/** All records, read only from their own paginated route. The address owns selection. */
export function Records({ snapshot, api, active = true, selected, onSelect, onOpenMicroTask, listNavigation, listCollapsed = false, onCollapseList, onExpandList }: {
  snapshot: Snapshot; api: ConsoleApi; active?: boolean; selected: string | null; onSelect: (id: string | null) => void;
  onOpenMicroTask: (macroTaskId: string, runId: string) => void;
  listNavigation?: ReactNode; listCollapsed?: boolean; onCollapseList: () => void; onExpandList: () => void;
}) {
  const { t } = useTranslation("tasks");
  const [query, setQuery] = useState("");
  const history = useTaskHistory(api, { rootsOnly: false, query }, active);
  const scroll = useRef<HTMLDivElement>(null);
  const remember = useRecordAnchor(scroll, history.runs.length, history.loading);
  const groups = useMemo(() => {
    const all = new Map<string, Task[]>();
    for (const task of history.runs) {
      const day = new Date(task.createdAt).toDateString();
      if (!all.has(day)) all.set(day, []);
      all.get(day)!.push(task);
    }
    return [...all].map(([day, runs]) => ({ day, runs }));
  }, [history.runs]);
  const client = useMemo(() => createTaskDetailClient(api, snapshot.csrfToken), [api, snapshot.csrfToken]);
  function open(id: string, macroTaskId?: string) {
    if (macroTaskId) { onOpenMicroTask(macroTaskId, id); return; }
    const task = history.runs.find(row => row.runId === id);
    const macro = task?.workflow ? macroTaskKey(task) : null;
    if (macro) onOpenMicroTask(macro, id); else onSelect(id);
  }
  const today = new Date(), yesterday = new Date(); yesterday.setDate(today.getDate() - 1);
  const dayLabel = (day: string, at: string) => day === today.toDateString() ? t("today") : day === yesterday.toDateString() ? t("yesterday") : formatCalendarDate(Date.parse(at));
  return <TooltipProvider delayDuration={300}><Collapsible open={!listCollapsed} onOpenChange={open => open ? onExpandList() : onCollapseList()} asChild><div className={cn("grid h-full min-h-0 min-w-0 transition-[grid-template-columns] duration-200 motion-reduce:transition-none max-[760px]:flex max-[760px]:flex-col", listCollapsed ? "grid-cols-[0px_minmax(0,1fr)]" : "grid-cols-[316px_minmax(0,1fr)]")}>
    <CollapsibleContent className={cn("h-full min-h-0 min-w-0", selected && "max-[760px]:hidden")}>
    <section data-slot="records-list" aria-label={t("recordsList")} className="flex h-full min-h-0 w-[316px] flex-col border-r bg-card text-sm max-[760px]:w-full">
      <div className="shrink-0 px-4 pt-3.5 pb-3">
        <div className="flex min-h-7 items-center gap-2">{listNavigation}<span className="ml-auto"><ListFold label={t("recordsCollapse")} onClick={onCollapseList}/></span></div>
        <Input data-slot="records-search" className="mt-2.5 h-8 text-sm" aria-label={t("search")} placeholder={t("search")} value={query} maxLength={200} onChange={event => setQuery(event.target.value)}/>
      </div>
      {history.newIds.size > 0 && <button type="button" data-slot="records-new" className="flex h-8 shrink-0 items-center gap-1.5 border-t px-4 text-xs text-ink2 transition-colors duration-150 hover:bg-muted motion-reduce:transition-none" onClick={() => { history.reset(); scroll.current?.querySelector<HTMLElement>("[data-slot=scroll-area-viewport]")?.scrollTo?.(0, 0); }}>
        <span><Trans t={t} i18nKey="recordsNew" values={{ n: formatNumber(history.newIds.size) }} components={{ count: <b className="tabular-nums"/> }}/></span><span>·</span><span className="ml-auto text-foreground underline underline-offset-4">{t("recordsLatest")}</span>
      </button>}
      {history.error && <div role="alert" className="flex shrink-0 flex-wrap items-center gap-2 border-t bg-fail-wash px-4 py-2 text-xs"><StateMark kind="fail"/><Hint text={history.error}><span tabIndex={0}>{t("recordsFailed")}</span></Hint><Button variant="outline" size="sm" className="ml-auto" onClick={() => void history.retry()}>{t("retry")}</Button></div>}
      <ScrollArea ref={scroll} className="min-h-0 flex-1" viewportProps={{ "aria-label": t("recordsEntries"), tabIndex: 0 }}>
        {groups.map(group => <section key={group.day}><h3 className="flex h-8 items-center px-4 text-xs font-normal text-ink3 tabular-nums">{dayLabel(group.day, group.runs[0].createdAt)}</h3><ul>{group.runs.map(task => <Entry key={task.runId} task={task} selected={selected === task.runId} onSelect={() => open(task.runId)}/>)}</ul></section>)}
        {!history.runs.length && !history.loading && !history.error && <p className="p-4 text-xs text-ink3">{t(query ? "recordsNoMatch" : "recordsEmpty")}</p>}
        {history.loading && !history.runs.length && <p role="status" className="p-4 text-xs text-ink3">{t("loading")}</p>}
        {history.nextCursor && <div className="flex justify-center border-t py-3"><Button data-slot="records-more" variant="ghost" size="sm" className="text-xs text-ink2" aria-busy={history.loading} onClick={() => { if (!history.loading) { remember(); void history.more(); } }}>{t(history.loading ? "loading" : "earlier")}</Button></div>}
      </ScrollArea>
    </section></CollapsibleContent>
    <div className={cn("col-start-2 flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto bg-card", !selected && !listCollapsed && "max-[760px]:hidden")}>
      {listCollapsed && <div className="flex shrink-0 items-center gap-3 border-b px-4 py-2">{listNavigation}<ListFold label={t("recordsExpand")} onClick={onExpandList}/></div>}
      {selected ? <TaskDetail key={selected} client={client} runId={selected} timeline={null} timelineLoading={false} nowMs={null} step={1} origin={null} active={active} backTo="records" listed={history.runs.find(row => row.runId === selected)?.revision ?? null} onStep={() => undefined} onBack={() => onSelect(null)} onOpenRun={open}/> : <p className="m-auto p-6 text-sm text-ink3">{t("recordsChoose")}</p>}
    </div>
  </div></Collapsible></TooltipProvider>;
}
