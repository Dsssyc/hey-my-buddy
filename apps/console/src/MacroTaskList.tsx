import type { ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { MacroTaskFilter, MacroTaskSort, MacroTaskSummary } from "./macro-task-types";
import { Hint } from "./components/hint";
import { StateMark } from "./components/ui/state-mark";
import { Tabs, TabsList, TabsTrigger } from "./components/ui/tabs";
import { ScrollArea } from "./components/ui/scroll-area";
import { formatNumber, formatRelative } from "./i18n/format";
import { cn } from "cn";
import { CopyMinus, PanelLeft, RefreshCw } from "lucide-react";
import { MacroSort } from "./MacroSort";
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import { Collapsible, CollapsibleTrigger, CollapsibleContent, Disclosure } from "./components/ui/collapsible";
import { useTextClipped } from "./components/use-text-clipped";

export type MacroTaskListProps = {
  rows: MacroTaskSummary[]; totals: Record<MacroTaskFilter, number>;
  loading: boolean; error: string; nextCursor: string | null; reorder: { count: number | null } | null;
  sort: MacroTaskSort; onSortChange: (sort: MacroTaskSort) => void;
  filter: MacroTaskFilter; query: string; selected: string | null; active?: boolean; visible?: boolean;
  listNavigation?: ReactNode; onCollapse: () => void;
  onFilterChange: (filter: MacroTaskFilter) => void; onQueryChange: (query: string) => void;
  onSelect: (id: string) => void; onMore: () => void; onApplyReorder: () => void;
};
export function ListFold({ label, onClick }: { label: string; onClick: () => void }) {
  return <Hint text={label}><Button data-slot="macro-fold" variant="ghost" size="icon-sm" aria-label={label} onClick={onClick}><PanelLeft aria-hidden="true"/></Button></Hint>;
}
export function MacroCounts({ row }: { row: MacroTaskSummary }) {
  const { t } = useTranslation("tasks");
  const counts = [
    { key: "waiting", count: row.counts.host + row.counts.review, mark: "hand" },
    { key: "active", count: row.counts.active, mark: "live" },
    { key: "failed", count: row.counts.failed, mark: "fail" },
    { key: "accepted", count: row.counts.accepted, mark: "done" },
    { key: "ended", count: row.counts.ended - row.counts.accepted - row.counts.failed, mark: "idle" },
  ] as const;
  return <>{counts.map(({ key, count, mark }) => count > 0 && <span key={key} className="inline-flex items-center gap-1.5 tabular-nums"><Hint text={t(key)}><span><StateMark kind={mark} className="size-2.5"/></span></Hint><span className="sr-only">{t(key)} {formatNumber(count)}</span><span aria-hidden="true">{formatNumber(count)}</span></span>)}</>;
}
function Entry({ row, basis, selected, onSelect }: { row: MacroTaskSummary; basis: "created" | "activity"; selected: string | null; onSelect: (id: string) => void }) {
  const { t } = useTranslation("tasks");
  const text = row.titleSource === "none" ? t("unnamed") : row.titleSource === "task" ? Array.from(row.title!).slice(0, 40).join("") : row.title!;
  const [titleRef, clipped] = useTextClipped<HTMLSpanElement>(text);
  const waiting = row.counts.host + row.counts.review > 0;
  const edge = waiting ? "before:bg-hand-mark" : row.counts.active > 0 ? "before:bg-foreground" : null;
  const status = waiting ? "waiting" : row.counts.active > 0 ? "active" : row.counts.failed > 0 ? "failed" : row.counts.roots > 0 && row.counts.accepted === row.counts.roots ? "accepted" : "ended";
  const hint = row.titleSource === "task" ? `${row.title} · ${t("source")}` : clipped ? text : undefined;
  const stamp = basis === "created" ? row.createdAt : row.lastActivityAt;
  return <li data-macro-task-id={row.macroTaskId}><Hint text={hint}>
    <button type="button" data-slot="macro-entry" className={cn("relative grid w-full grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-2 gap-y-0.5 border-t px-4 py-2.5 text-left text-sm text-foreground hover:bg-muted aria-pressed:hover:bg-selection-hover transition-colors duration-150 motion-reduce:transition-none outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50", edge && "before:absolute before:inset-y-0 before:left-0 before:w-[3px]", edge, selected === row.macroTaskId ? "bg-selection" : "bg-transparent")} aria-pressed={selected === row.macroTaskId} onClick={() => onSelect(row.macroTaskId)}>
      <span ref={titleRef} data-slot="macro-entry-title" className={cn("min-w-0 font-medium", clipped ? "text-fade" : "truncate")}>{text}</span><time className="whitespace-nowrap text-xs text-ink3 tabular-nums" dateTime={stamp ?? undefined}><span className="sr-only">{t(basis === "created" ? "sort.created" : "sort.activity")}</span>{stamp === null ? t("unknown") : formatRelative(Date.parse(stamp), Date.now())}</time><span className="sr-only">{t(status)}</span>
      <span data-slot="macro-entry-counts" className="col-span-2 flex gap-3 text-xs text-ink2"><MacroCounts row={row}/></span>
    </button>
  </Hint></li>;
}
export function MacroTaskList(props: MacroTaskListProps) {
  const { t, i18n } = useTranslation("tasks");
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
  const groups = useMemo(() => {
    const projects = new Map<string, { id: string; label: string; rows: MacroTaskSummary[] }>();
    const standalone: MacroTaskSummary[] = [];
    for (const row of props.rows) {
      if (row.kind === "standalone") { standalone.push(row); continue; }
      const id = JSON.stringify(["project", row.project?.id ?? null]);
      if (!projects.has(id)) projects.set(id, { id, label: row.project?.label ?? t("unknownProject"), rows: [] });
      projects.get(id)!.rows.push(row);
    }
    return [...projects.values(), ...(standalone.length ? [{ id: "standalone", label: t("standalone"), rows: standalone }] : [])];
  }, [props.rows, t]);
  const empty = !props.rows.length && !props.loading && !props.error;
  return <section data-slot="macro-list" className="flex h-full min-h-0 w-[316px] flex-col border-r bg-card text-sm max-[760px]:w-full" aria-label={t("list")} onPointerEnter={() => { pointer.current = true; }} onPointerLeave={() => { pointer.current = false; idle(); }}>
    <div className="shrink-0 px-4 pt-3.5">
      <div data-slot="macro-list-toolbar" className="flex min-h-7 items-center gap-2">{props.listNavigation}
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
          <TabsList variant="line" className="w-full justify-between gap-3" aria-label={t("list")}>{(["all", "active", "host", "review"] as const).map(key => <Hint key={key} text={i18n.language.startsWith("en") && (key === "host" || key === "review") ? t(key) : undefined}><TabsTrigger className="flex-none" value={key} aria-label={`${t(key)} ${formatNumber(props.totals[key])}`}>{t(key === "host" ? "filterHost" : key === "review" ? "filterReview" : key)}<span className={cn("tabular-nums", key === "host" || key === "review" ? "text-hand" : "text-ink3")}>{formatNumber(props.totals[key])}</span></TabsTrigger></Hint>)}</TabsList>
        </Tabs>
      </>}
    </div>
    <ScrollArea ref={scroll} className="min-h-0 flex-1" viewportProps={{ "aria-label": t("entries"), tabIndex: 0 }} onScrollCapture={idle}>
      {groups.map(group => <Collapsible key={group.id} data-slot="macro-group" open={!collapsed.has(group.id)} onOpenChange={open => setCollapsed(old => { const next = new Set(old); if (open) next.delete(group.id); else next.add(group.id); return next; })}>
        <CollapsibleTrigger asChild><button type="button" data-slot="macro-group-heading" className="disclosure-trigger group flex h-8 w-full items-center justify-between gap-2 px-4 text-left text-xs motion-reduce:transition-none">
          <span className="flex min-w-0 items-center gap-1"><Disclosure open={!collapsed.has(group.id)}/><span className="min-w-0 text-fade">{group.label}</span></span><span className="tabular-nums">{formatNumber(group.rows.length)}</span>
        </button></CollapsibleTrigger>
        <CollapsibleContent><ul className="m-0 list-none p-0">{group.rows.map(row => <Entry key={row.macroTaskId} row={row} basis={basis} selected={props.selected} onSelect={props.onSelect}/>)}</ul></CollapsibleContent>
      </Collapsible>)}
      {empty && <p className="p-4 text-xs text-ink3">{t(props.query || props.filter !== "all" ? "noMatch" : "noTasks")}</p>}
      {props.loading && !props.rows.length && <p className="p-4 text-xs text-ink3" role="status">{t("loading")}</p>}
      {props.nextCursor && <Button variant="outline" size="sm" className="mx-auto my-3 flex text-xs" aria-busy={props.loading} onClick={() => { if (!props.loading) props.onMore(); }}>{t(props.loading ? "loading" : "earlier")}</Button>}
    </ScrollArea>
  </section>;
}
