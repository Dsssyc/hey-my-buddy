import type { ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { MacroTaskFilter, MacroTaskSummary } from "./macro-task-types";
import { Hint } from "./components/hint";
import { StateMark } from "./components/ui/state-mark";
import { Tabs, TabsList, TabsTrigger } from "./components/ui/tabs";
import { ScrollArea } from "./components/ui/scroll-area";
import { formatNumber, formatRelative, formatMomentMinutes } from "./i18n/format";
import "./macro-list.css";

export type MacroTaskListProps = {
  rows: MacroTaskSummary[]; totals: Record<MacroTaskFilter, number>; verifiedAtMs?: number | null;
  loading: boolean; error: string; nextCursor: string | null; reorder: { count: number | null } | null;
  filter: MacroTaskFilter; query: string; selected: string | null; active?: boolean; visible?: boolean;
  listNavigation?: ReactNode; onCollapse: () => void;
  onFilterChange: (filter: MacroTaskFilter) => void; onQueryChange: (query: string) => void;
  onSelect: (id: string) => void; onRetry: () => void; onMore: () => void; onApplyReorder: () => void;
};
export function ListFold({ label, onClick }: { label: string; onClick: () => void }) {
  return <Hint text={label}><button type="button" className="macro-fold" aria-label={label} onClick={onClick}>
    <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true"><rect x="1.5" y="2.5" width="11" height="9" rx="1"/><path d="M5.2 2.5v9"/></svg>
  </button></Hint>;
}
const categories = ["host", "review", "active", "accepted", "ended"] as const;
const mark = { host: "hand", review: "hand", active: "live", accepted: "done", ended: "idle" } as const;
export function MacroCounts({ row }: { row: MacroTaskSummary }) {
  const { t } = useTranslation("tasks");
  return <>{categories.map(key => {
    const count = key === "ended" ? row.counts.ended - row.counts.accepted : row.counts[key];
    return count > 0 && <span key={key} className="macro-count" aria-label={`${t(key)} ${formatNumber(count)}`}><StateMark kind={mark[key]} />{formatNumber(count)}</span>;
  })}</>;
}
function Entry({ row, selected, onSelect }: { row: MacroTaskSummary; selected: string | null; onSelect: (id: string) => void }) {
  const { t } = useTranslation("tasks");
  const text = row.titleSource === "none" ? t("unnamed") : row.titleSource === "task" ? Array.from(row.title).slice(0, 40).join("") : row.title;
  const kind = row.state === "active" ? "live" : row.state === "host" || row.state === "review" ? "hand" : row.counts.accepted === row.counts.roots ? "done" : "idle";
  const hintTitle = row.titleSource === "task" ? `${row.title} · ${t("source")}` : text;
  const hint = t("entryHint", { title: hintTitle, host: row.sourceHostId ?? t("unknown"), time: row.lastActivityAt === null ? t("unknown") : formatMomentMinutes(Date.parse(row.lastActivityAt), Date.now()) });
  return <li data-macro-task-id={row.macroTaskId}><Hint text={hint}>
    <button type="button" className={`macro-entry${selected === row.macroTaskId ? " selected" : ""}`} aria-pressed={selected === row.macroTaskId} onClick={() => onSelect(row.macroTaskId)}>
      <StateMark kind={kind}/><span className="macro-entry-title">{text}</span><time dateTime={row.lastActivityAt ?? undefined}>{row.lastActivityAt === null ? t("unknown") : formatRelative(Date.parse(row.lastActivityAt), Date.now())}</time>
      <span className="macro-entry-counts"><MacroCounts row={row}/></span>
    </button>
  </Hint></li>;
}
export function MacroTaskList(props: MacroTaskListProps) {
  const { t } = useTranslation("tasks");
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set(["standalone"]));
  const scroll = useRef<HTMLDivElement>(null), pointer = useRef(false), applying = useRef(false);
  const visible = props.visible ?? props.active !== false;
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
  return <section className="macro-list" aria-label={t("list")} onPointerEnter={() => { pointer.current = true; }} onPointerLeave={() => { pointer.current = false; idle(); }}>
    <div className="macro-list-head">
      <div className="macro-list-heading">{props.listNavigation}
        {props.reorder && <Hint text={t("update")}><button type="button" className="macro-update" aria-label={t("update")} onClick={() => { apply(); scroll.current?.querySelector<HTMLElement>("[data-slot=scroll-area-viewport]")?.scrollTo?.(0, 0); }}><StateMark kind="live"/></button></Hint>}
        <ListFold label={t("collapse")} onClick={props.onCollapse}/>
      </div>
      {!(empty && props.totals.all === 0 && !props.query && props.filter === "all") && <>
        <input className="macro-search" aria-label={t("search")} placeholder={t("search")} value={props.query} maxLength={200} onChange={e => props.onQueryChange(e.target.value)}/>
        <Tabs className="macro-filter-tabs" value={props.filter} onValueChange={value => props.onFilterChange(value as MacroTaskFilter)}>
          <TabsList variant="line" aria-label={t("list")}>{(["all", "active", "host", "review"] as const).map(key => <Hint key={key} text={t(key)}><TabsTrigger value={key} aria-label={`${t(key)} ${formatNumber(props.totals[key])}`}>{t(key === "host" ? "filterHost" : key === "review" ? "filterReview" : key)}<span className={key === "host" || key === "review" ? "hand" : ""}>{formatNumber(props.totals[key])}</span></TabsTrigger></Hint>)}</TabsList>
        </Tabs>
      </>}
    </div>
    <ScrollArea ref={scroll} className="macro-list-scroll" viewportProps={{ "aria-label": t("entries"), tabIndex: 0 }} onScrollCapture={idle}>
      {groups.map(group => <section key={group.id} className="macro-group">
        <button type="button" className="macro-group-heading" aria-expanded={!collapsed.has(group.id)} onClick={() => setCollapsed(old => { const next = new Set(old); if (next.has(group.id)) next.delete(group.id); else next.add(group.id); return next; })}>
          <span><span className="macro-chevron" aria-hidden="true">{collapsed.has(group.id) ? "▸" : "▾"}</span>{group.label}</span><span>{formatNumber(group.rows.length)}</span>
        </button>
        {!collapsed.has(group.id) && <ul>{group.rows.map(row => <Entry key={row.macroTaskId} row={row} selected={props.selected} onSelect={props.onSelect}/>)}</ul>}
      </section>)}
      {empty && <p className="macro-list-empty">{t(props.query || props.filter !== "all" ? "noMatch" : "noTasks")}</p>}
      {props.loading && !props.rows.length && <p className="macro-list-empty" role="status">{t("loading")}</p>}
      {props.nextCursor && <button type="button" className="macro-earlier" aria-busy={props.loading} onClick={() => { if (!props.loading) props.onMore(); }}>{t(props.loading ? "loading" : "earlier")}</button>}
    </ScrollArea>
  </section>;
}
