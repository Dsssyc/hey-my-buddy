import { Fragment, memo, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { MacroTaskFilter, MacroTaskGroup, MacroTaskSort, MacroTaskSummary } from "./macro-task-types";
import { Hint } from "./components/hint";
import { StateMark } from "./components/ui/state-mark";
import { Tabs, TabsList, TabsTrigger } from "./components/ui/tabs";
import { ScrollArea } from "./components/ui/scroll-area";
import { formatDateTime, formatNumber } from "./i18n/format";
import { cn } from "cn";
import { CopyMinus, Folder, FolderOpen, PanelLeft, RefreshCw } from "lucide-react";
import { MacroSort } from "./MacroSort";
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from "./components/ui/collapsible";
import { useFadingText } from "./components/use-fading-text";
import { useTextClipped } from "./components/use-text-clipped";

export type MacroTaskListProps = {
  groups: MacroTaskGroup[]; totals: Record<MacroTaskFilter, number>;
  loading: boolean; error: string; reorder: { count: number | null } | null;
  sort: MacroTaskSort; onSortChange: (sort: MacroTaskSort) => void;
  filter: MacroTaskFilter; query: string; selected: string | null; active?: boolean; visible?: boolean;
  onCollapse: () => void;
  onFilterChange: (filter: MacroTaskFilter) => void; onQueryChange: (query: string) => void;
  onSelect: (id: string) => void; onMore: (groupId: string) => void; onApplyReorder: () => void;
};
/** The list's title carries its total: what the current search matches, or every macro task without one. */
export function MacroListHeading({ count }: { count: number }) {
  const { t } = useTranslation("tasks");
  return <h2 data-slot="macro-list-heading" aria-label={`${t("macro")} ${formatNumber(count)}`} className="flex min-w-0 items-baseline gap-1.5 text-base font-semibold">
    <span className="truncate">{t("macro")}</span>
    <span data-slot="macro-list-total" className="shrink-0 text-sm font-normal tabular-nums text-ink3">{formatNumber(count)}</span>
  </h2>;
}
function FilterTab({ value, label, full, count, tone, busy }: { value: MacroTaskFilter; label: string; full: string; count?: number; tone?: "hand"; busy?: boolean }) {
  const text = useFadingText(label);
  return <Hint text={(text.hint || label !== full) ? full : undefined}><TabsTrigger value={value} className={cn("min-w-0 flex-initial", busy && "state-breathe")} aria-busy={busy || undefined} aria-label={count === undefined ? full : `${full} ${formatNumber(count)}`}>
    <span ref={text.ref} data-slot="macro-filter-name" className={cn("min-w-0", text.className)}>{label}</span>
    {count !== undefined && <span data-slot="macro-filter-count" className={cn("shrink-0 tabular-nums", tone === "hand" ? "text-hand" : "text-ink3")}>{formatNumber(count)}</span>}
  </TabsTrigger></Hint>;
}
export function ListFold({ label, onClick }: { label: string; onClick: () => void }) {
  return <Hint text={label}><Button data-slot="macro-fold" variant="ghost" size="icon-sm" aria-label={label} onClick={onClick}><PanelLeft aria-hidden="true"/></Button></Hint>;
}
function GroupHeading({ label, count, open, standalone }: { label: string; count: number; open: boolean; standalone: boolean }) {
  const Icon = open ? FolderOpen : Folder;
  const [labelRef, clipped] = useTextClipped<HTMLSpanElement>(label);
  return <Hint text={clipped ? label : undefined}><CollapsibleTrigger asChild><button type="button" data-slot="macro-group-heading" className="disclosure-trigger grid h-8 w-full grid-cols-[16px_minmax(0,1fr)_auto] items-center gap-x-2 pr-4 pl-3 text-left text-sm motion-reduce:transition-none">
    <Icon aria-hidden="true" className="size-4 text-ink2" strokeWidth={1.75} strokeDasharray={standalone ? "3 2.4" : undefined}/>
    <span ref={labelRef} className="min-w-0 truncate font-semibold">{label}</span><span className="text-xs text-ink3 tabular-nums">{formatNumber(count)}</span>
  </button></CollapsibleTrigger></Hint>;
}
const Entry = memo(function Entry({ row, basis, selected, onSelect }: { row: MacroTaskSummary; basis: "created" | "activity"; selected: boolean; onSelect: (id: string) => void }) {
  const { t } = useTranslation("tasks");
  const text = row.titleSource === "none" ? t("unnamed") : row.titleSource === "task" ? Array.from(row.title!).slice(0, 40).join("") : row.title!;
  const waiting = row.counts.host + row.counts.review > 0;
  const mark = waiting ? "hand" : row.counts.active > 0 ? "live" : row.counts.failed > 0 ? "fail" : null;
  const status = waiting ? row.counts.host > 0 ? "host" : "review" : row.counts.active > 0 ? "active" : row.counts.failed > 0 ? "failed" : row.counts.roots > 0 && row.counts.accepted === row.counts.roots ? "accepted" : "ended";
  const stamp = basis === "created" ? row.createdAt : row.lastActivityAt;
  const hint = `${row.titleSource === "none" ? text : row.title} · ${t("entryTime", {
    basis: t(basis === "created" ? "entryStarted" : "entryActivity"),
    time: stamp === null ? t("unknown") : formatDateTime(Date.parse(stamp)),
  })}${row.titleSource === "task" ? ` · ${t("source")}` : ""}`;
  return <li data-macro-task-id={row.macroTaskId}><Hint text={hint}>
    <button type="button" data-slot="macro-entry" className={cn("grid h-8 w-full grid-cols-[16px_minmax(0,1fr)] items-center gap-x-2 pr-4 pl-3 text-left text-[13px] text-foreground hover:bg-selected-row aria-pressed:hover:bg-selection transition-colors duration-150 motion-reduce:transition-none outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50", selected ? "bg-selection" : "bg-transparent")} aria-pressed={selected} onClick={() => onSelect(row.macroTaskId)}>
      <span className="flex items-center justify-center">{mark && <Hint text={t(status)}><span className="flex"><StateMark kind={mark} className="size-2.5"/></span></Hint>}</span>
      <span data-slot="macro-entry-title" className="min-w-0 truncate">{text}</span><span className="sr-only">{t(status)}</span>
    </button>
  </Hint></li>;
});
/** Radix measures each appended page, so showing more uses the same reveal as a group. */
function MoreEntries({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setOpen(true));
    return () => cancelAnimationFrame(frame);
  }, []);
  return <Collapsible open={open}><CollapsibleContent>{children}</CollapsibleContent></Collapsible>;
}
/** A response adds one reveal batch, regardless of how many rows the service returned. */
function GroupEntries({ rows, basis, selected, onSelect }: { rows: MacroTaskSummary[]; basis: "created" | "activity"; selected: string | null; onSelect: (id: string) => void }) {
  const history = useRef<{ ids: string[]; ends: number[] }>({ ids: [], ends: [] });
  const ids = rows.map(row => row.macroTaskId);
  const previous = history.current;
  if (ids.length !== previous.ids.length || ids.some((id, index) => id !== previous.ids[index])) {
    const appended = previous.ids.length > 0 && ids.length > previous.ids.length
      && previous.ids.every((id, index) => id === ids[index]);
    history.current = { ids, ends: appended ? [...previous.ends, ids.length] : [ids.length] };
  }
  let start = 0;
  return <>{history.current.ends.map((end, index) => {
    const from = start; start = end;
    const entries = <ul className="m-0 list-none p-0">{rows.slice(from, end).map(row => <Entry key={row.macroTaskId} row={row} basis={basis} selected={selected === row.macroTaskId} onSelect={onSelect}/>)}</ul>;
    return index === 0 ? <Fragment key="initial">{entries}</Fragment> : <MoreEntries key={`${from}:${rows[from]?.macroTaskId}`}>{entries}</MoreEntries>;
  })}</>;
}
export function MacroTaskList(props: MacroTaskListProps) {
  const { t } = useTranslation("tasks");
  const selection = useRef(props.onSelect);
  selection.current = props.onSelect;
  const onSelect = useCallback((id: string) => selection.current(id), []);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set(["standalone"]));
  const scroll = useRef<HTMLDivElement>(null), pointer = useRef(false), applying = useRef(false);
  const visible = props.visible ?? props.active !== false;
  const basis = props.sort.startsWith("created-") ? "created" : "activity";
  if (!props.reorder) applying.current = false;
  function apply() { if (applying.current) return; applying.current = true; props.onApplyReorder(); }
  function idle() {
    const viewport = scroll.current?.querySelector<HTMLElement>("[data-slot=scroll-area-viewport]");
    if (visible && props.reorder && !pointer.current && (viewport?.scrollTop ?? 0) <= 0) apply();
  }
  useEffect(idle, [props.reorder, visible]);
  const groups = props.groups;
  const empty = !groups.length && !props.loading && !props.error;
  return <section data-slot="macro-list" className="flex h-full min-h-0 w-[316px] flex-col border-r bg-card text-sm max-[760px]:w-full" aria-label={t("list")} onPointerEnter={() => { pointer.current = true; }} onPointerLeave={() => { pointer.current = false; idle(); }}>
    <div className="shrink-0 px-4 pt-3.5">
      <div data-slot="macro-list-toolbar" className="flex min-h-7 items-center gap-2"><MacroListHeading count={props.totals.all}/>
        {props.reorder && <Hint text={t("update")}><Button data-slot="macro-update" variant="ghost" size="icon-sm" aria-label={t("update")} onClick={() => { apply(); scroll.current?.querySelector<HTMLElement>("[data-slot=scroll-area-viewport]")?.scrollTo?.(0, 0); }}><RefreshCw aria-hidden="true"/></Button></Hint>}
        <div className="ml-auto flex items-center gap-1">
          <MacroSort sort={props.sort} onChange={props.onSortChange}/>
          <Hint text={t("collapseAll")}><Button data-slot="macro-collapse-all" variant="ghost" size="icon-sm" aria-label={t("collapseAll")} onClick={() => setCollapsed(new Set(groups.map(group => group.id)))}><CopyMinus aria-hidden="true"/></Button></Hint>
          <ListFold label={t("collapse")} onClick={props.onCollapse}/>
        </div>
      </div>
      {!(empty && props.totals.all === 0 && !props.query && props.filter === "all") && <>
        <Input data-slot="macro-search" className="mt-2.5 h-8 text-sm" aria-label={t("search")} placeholder={t("search")} value={props.query} maxLength={200} onChange={e => props.onQueryChange(e.target.value)}/>
        <Tabs data-slot="macro-filter-tabs" className="mt-3 gap-0" value={props.filter} onValueChange={value => props.onFilterChange(value as MacroTaskFilter)}>
          <TabsList variant="line" className="w-full justify-between gap-2" aria-label={t("list")}>
            <FilterTab value="all" busy={props.loading && props.filter === "all"} label={t("all")} full={t("all")}/>
            <FilterTab value="active" busy={props.loading && props.filter === "active"} label={t("active")} full={t("active")} count={props.totals.active}/>
            <FilterTab value="host" busy={props.loading && props.filter === "host"} label={t("filterHost")} full={t("host")} count={props.totals.host} tone="hand"/>
            <FilterTab value="review" busy={props.loading && props.filter === "review"} label={t("filterReview")} full={t("review")} count={props.totals.review} tone="hand"/>
          </TabsList>
        </Tabs>
      </>}
    </div>
    <ScrollArea ref={scroll} className="min-h-0 flex-1" viewportProps={{ "aria-label": t("entries"), tabIndex: 0 }} onScrollCapture={idle}>
      <div className="pt-1.5 pb-4">
      {groups.map(group => <Collapsible key={group.id} data-slot="macro-group" className="[&+&]:mt-2.5" open={!collapsed.has(group.id)} onOpenChange={open => setCollapsed(old => { const next = new Set(old); if (open) next.delete(group.id); else next.add(group.id); return next; })}>
        <GroupHeading label={group.project?.label ?? t("standalone")} count={group.total} open={!collapsed.has(group.id)} standalone={group.id === "standalone"}/>
        <CollapsibleContent>
          <GroupEntries key={`${props.sort}:${props.filter}:${props.query}`} rows={group.macroTasks} basis={basis} selected={props.selected} onSelect={onSelect}/>
          {group.nextCursor && <button type="button" data-slot="macro-group-more" className="grid h-7 w-full grid-cols-[16px_minmax(0,1fr)] items-center gap-x-2 pr-4 pl-3 text-left text-[12.5px] text-ink3 transition-colors duration-150 hover:text-foreground motion-reduce:transition-none outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-busy={props.loading} disabled={props.loading} onClick={() => props.onMore(group.id)}><span/><span>{t("showMore")}</span></button>}
        </CollapsibleContent>
      </Collapsible>)}
      {empty && <p className="p-4 text-xs text-ink3">{t(props.query || props.filter !== "all" ? "noMatch" : "noTasks")}</p>}
      {props.loading && !groups.length && <p className="p-4 text-xs text-ink3" role="status">{t("loading")}</p>}
      </div>
    </ScrollArea>
  </section>;
}
