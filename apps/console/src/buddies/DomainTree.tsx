import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatList } from "../i18n/format";
import type { DomainNode } from "../types";
import { readChoiceViewPreference, storeViewPreference, useViewPreference } from "../view-preferences";
import "./DomainTree.css";

type Branch = DomainNode & { children: Branch[]; depth: number; total: number };

function domainOutline(nodes: DomainNode[], isFolded: (id: string) => boolean) {
  const active = nodes.filter(node => node.mergedIntoId === null);
  const byId = new Map<string, Branch>(active.map(node => [node.nodeId, { ...node, children: [], depth: 0, total: 0 }]));
  const roots: Branch[] = [];
  for (const branch of byId.values()) {
    if (branch.parentId === null) roots.push(branch);
    else byId.get(branch.parentId)?.children.push(branch);
  }
  const mergedFrom = new Map<string, string[]>();
  const allById = new Map(nodes.map(node => [node.nodeId, node]));
  for (const node of nodes) if (node.mergedIntoId !== null) {
    let target = allById.get(node.mergedIntoId)!;
    while (target.mergedIntoId !== null) target = allById.get(target.mergedIntoId)!;
    byId.get(target.nodeId)!.directCount += node.directCount;
    const names = mergedFrom.get(target.nodeId) ?? [];
    names.push(node.name);
    mergedFrom.set(target.nodeId, names);
  }
  function count(branch: Branch): number {
    branch.total = branch.directCount + branch.children.reduce((sum, child) => sum + count(child), 0);
    branch.children.sort((a, b) => b.total - a.total || a.name.localeCompare(b.name));
    return branch.total;
  }
  for (const root of roots) count(root);
  roots.sort((a, b) => b.total - a.total || a.name.localeCompare(b.name));
  const visible: Branch[] = [];
  function visit(branch: Branch, depth: number) {
    branch.depth = depth;
    visible.push(branch);
    if (!isFolded(branch.nodeId)) for (const child of branch.children) visit(child, depth + 1);
  }
  for (const root of roots) visit(root, 0);
  return { active, roots, visible, byId, mergedFrom, total: roots.reduce((sum, root) => sum + root.total, 0) };
}

function colorBand(count: number, maximum: number): number {
  return maximum === 0 ? 0 : Math.min(4, Math.round(4 * Math.log1p(count) / Math.log1p(maximum)));
}

export function DomainTree({ nodes, locked, command, error, note, clearFeedback }: {
  nodes: DomainNode[]; locked: boolean; error: string; note: string; clearFeedback: () => void; command: (operation: string, params: object) => Promise<boolean>;
}) {
  const { t } = useTranslation("buddies");
  const [defaultExpanded, setDefaultExpanded] = useViewPreference("domain-tree-expanded");
  const [nodeChoices, setNodeChoices] = useState<Record<string, boolean>>({});
  const [activeId, setActiveId] = useState<string | null>(null);
  const [popoverAt, setPopoverAt] = useState<{ left: number; top: number } | null>(null);
  const [editing, setEditing] = useState<DomainNode | null>(null), [merging, setMerging] = useState<DomainNode | null>(null);
  const [name, setName] = useState(""), [definition, setDefinition] = useState("");
  const [target, setTarget] = useState<DomainNode | null>(null);
  const stageRef = useRef<HTMLDivElement>(null), viewportRef = useRef<HTMLDivElement>(null), panelRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isFolded = (id: string) => {
    const saved = readChoiceViewPreference(`domain-tree-node:${id}`, ["expanded", "collapsed"] as const);
    return !(nodeChoices[id] ?? (saved === null ? defaultExpanded : saved === "expanded"));
  };
  const outline = useMemo(() => domainOutline(nodes, isFolded), [nodes, defaultExpanded, nodeChoices]);
  const active = activeId ? outline.byId.get(activeId) : undefined;
  const maximum = outline.roots[0]?.total ?? 0;
  // All rows share this scale, including those hidden by a fold. A colour names
  // the count represented by a segment, whether that segment is own or a child.
  const colorMaximum = Math.max(0, ...[...outline.byId.values()].flatMap(node =>
    [node.directCount, ...node.children.map(child => child.total)]));
  const allExpanded = [...outline.byId.values()].every(node => !node.children.length || !isFolded(node.nodeId));
  const excluded = new Set<string>();
  if (merging) {
    const queue = [outline.byId.get(merging.nodeId)!];
    for (let index = 0; index < queue.length; index++) {
      excluded.add(queue[index]!.nodeId);
      queue.push(...queue[index]!.children);
    }
  }

  function closeSoon() {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = setTimeout(() => {
      if (!stageRef.current?.contains(document.activeElement) && !stageRef.current?.matches(":hover")) setActiveId(null);
    }, 140);
  }
  function show(id: string) {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    if (id !== activeId) setPopoverAt(null);
    setActiveId(id);
  }
  function toggle(id: string) {
    const expanded = isFolded(id);
    storeViewPreference(`domain-tree-node:${id}`, expanded ? "expanded" : "collapsed");
    setNodeChoices(previous => ({ ...previous, [id]: expanded }));
  }
  function toggleAll() {
    const expanded = !allExpanded;
    setDefaultExpanded(expanded);
    const choices = Object.fromEntries(outline.active.map(node => [node.nodeId, expanded]));
    for (const node of outline.active) storeViewPreference(`domain-tree-node:${node.nodeId}`, expanded ? "expanded" : "collapsed");
    setNodeChoices(choices);
  }
  useLayoutEffect(() => () => { if (closeTimer.current) clearTimeout(closeTimer.current); }, []);
  useLayoutEffect(() => {
    if (!active || !stageRef.current || !panelRef.current) return;
    const stage = stageRef.current, panel = panelRef.current;
    const place = () => {
      const stageBox = stage.getBoundingClientRect();
      const row = stage.querySelector<HTMLElement>(`[data-node="${CSS.escape(active.nodeId)}"].domain-tree-row`);
      const nameBox = row?.querySelector(".domain-tree-name")?.getBoundingClientRect();
      if (!row || !nameBox) return;
      const panelWidth = panel.offsetWidth, panelHeight = panel.offsetHeight;
      const maxX = Math.max(4, stage.clientWidth - panelWidth - 4);
      const clamp = (value: number) => Math.max(4, Math.min(maxX, value));
      const preferredX = clamp(nameBox.right - stageBox.left + 14);
      const preferredY = row.getBoundingClientRect().top - stageBox.top;
      const names = [...stage.querySelectorAll<HTMLElement>(".domain-tree-name")].map(label => {
        const rect = label.getBoundingClientRect();
        return { left: rect.left - stageBox.left, right: rect.right - stageBox.left, top: rect.top - stageBox.top, bottom: rect.bottom - stageBox.top };
      }).filter(rect => rect.right > 0 && rect.left < stage.clientWidth);
      let best = { left: 0, top: 0, extra: Infinity, distance: Infinity };
      for (const left of new Set([preferredX, clamp(nameBox.left - stageBox.left - panelWidth - 14), maxX])) {
        const blocked = names.filter(rect => left < rect.right + 4 && left + panelWidth > rect.left - 4)
          .map(rect => ({ start: rect.top - 4, end: rect.bottom + 4 })).sort((a, b) => a.start - b.start);
        let freeStart = 4;
        const consider = (end: number) => {
          if (end - freeStart < panelHeight) return;
          const top = Math.max(freeStart, Math.min(preferredY, end - panelHeight));
          const extra = Math.max(0, top + panelHeight + 8 - viewportRef.current!.offsetHeight);
          const distance = Math.abs(left - preferredX) + Math.abs(top - preferredY);
          if (extra < best.extra || extra === best.extra && distance < best.distance) best = { left, top, extra, distance };
        };
        for (const interval of blocked) { consider(interval.start); freeStart = Math.max(freeStart, interval.end); }
        consider(Infinity);
      }
      stage.style.minHeight = `${Math.ceil(viewportRef.current!.offsetHeight + best.extra)}px`;
      setPopoverAt({ left: best.left, top: best.top });
    };
    place();
    viewportRef.current?.addEventListener("scroll", place);
    window.addEventListener("resize", place);
    return () => { stage.style.minHeight = ""; viewportRef.current?.removeEventListener("scroll", place); window.removeEventListener("resize", place); };
  }, [active, outline]);

  return <div data-slot="domain-tree" className="domain-tree">
    {!!outline.roots.length ? <>
      <div className="domain-tree-overview">
        <p>{t("domainTree.summary", { roots: outline.roots.length, nodes: outline.active.length, tasks: outline.total })}</p>
        <button type="button" onClick={toggleAll}>
          {t(allExpanded ? "domainTree.collapseAll" : "domainTree.expandAll")}
        </button>
      </div>
      <div ref={stageRef} className="domain-tree-stage" onPointerLeave={closeSoon} onBlur={closeSoon} onKeyDown={event => { if (event.key === "Escape") setActiveId(null); }}>
        <div ref={viewportRef} className="domain-tree-viewport" role="tree" aria-label={t("domainTree.domains")}>
          <div className="domain-tree-list">
            {outline.visible.map(node => {
              const folded = isFolded(node.nodeId);
              const segments = [{ id: node.nodeId, count: node.directCount, own: true }, ...node.children.map(child => ({ id: child.nodeId, count: child.total, own: false }))].filter(segment => segment.count > 0);
              return <button key={node.nodeId} type="button" role="treeitem" aria-level={node.depth + 1} data-slot="domain-row" data-node={node.nodeId}
                className="domain-tree-row" style={{ "--depth": node.depth } as CSSProperties}
                aria-label={`${node.name}, ${t("domainTree.taskCount", { count: node.total })}${node.children.length ? `, ${t(folded ? "domainTree.expand" : "domainTree.collapse", { name: node.name })}` : ""}`}
                aria-expanded={node.children.length ? !folded : undefined}
                onPointerEnter={() => show(node.nodeId)} onFocus={() => show(node.nodeId)}
                onKeyDown={event => { const firstAction = panelRef.current?.querySelector("button"); if (event.key === "Tab" && !event.shiftKey && activeId === node.nodeId && firstAction) { event.preventDefault(); firstAction.focus(); } }}
                onClick={() => { if (node.children.length) toggle(node.nodeId); }}>
                <span className="domain-tree-name">{node.depth > 0 && <span className="domain-tree-guide" aria-hidden="true" />}<span className="domain-tree-chevron" aria-hidden="true">{node.children.length > 0 ? folded ? "▸" : "▾" : ""}</span><span>{node.name}</span></span>
                <span className="domain-tree-count">{node.total}</span>
                <span className="domain-tree-bar" aria-hidden="true" data-node={node.nodeId}>
                  <span className="domain-tree-bar-fill" style={{ width: `${100 * node.total / Math.max(1, maximum)}%` }}>
                    {segments.map(segment => <span key={segment.id} className="domain-tree-segment" data-segment={segment.id} data-own={segment.own || undefined}
                      data-highlight={active?.nodeId === segment.id && segment.id !== node.nodeId || undefined}
                      style={{ flexGrow: segment.count, backgroundColor: `var(--buddy-domain-${colorBand(segment.count, colorMaximum)})` }} />)}
                  </span>
                </span>
              </button>;
            })}
          </div>
        </div>
        {active && <div ref={panelRef} className="domain-tree-popover" style={{ left: popoverAt?.left ?? 0, top: popoverAt?.top ?? 0, visibility: popoverAt ? "visible" : "hidden" }} onPointerEnter={() => { if (closeTimer.current) clearTimeout(closeTimer.current); }} onPointerLeave={closeSoon}
          onKeyDown={event => { if (event.key !== "Tab") return; const buttons = panelRef.current?.querySelectorAll("button"); if (!buttons?.length) return; if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); stageRef.current?.querySelector<HTMLElement>(`[data-node="${CSS.escape(active.nodeId)}"].domain-tree-row`)?.focus(); } else if (!event.shiftKey && document.activeElement === buttons[buttons.length - 1]) { const index = outline.visible.findIndex(node => node.nodeId === active.nodeId); const next = stageRef.current?.querySelectorAll<HTMLElement>(".domain-tree-row")[index + 1]; if (next) { event.preventDefault(); next.focus(); } } }}>
          <p className="domain-tree-popover-title">{active.name} <span>{t("domainTree.taskCount", { count: active.total })}</span></p>
          <p className="domain-tree-popover-definition">{active.definition}</p>
          {!!outline.mergedFrom.get(active.nodeId)?.length && <p className="domain-tree-popover-merged">{t("domainTree.mergedFrom", { names: formatList(outline.mergedFrom.get(active.nodeId)!) })}</p>}
          <div className="domain-tree-popover-actions">
            <Button variant="ghost" size="sm" disabled={locked} onClick={() => { clearFeedback(); setEditing(active); setName(active.name); setDefinition(active.definition); }}>{t("maintenance.edit")}</Button>
            <Button variant="ghost" size="sm" disabled={locked || outline.active.length < 2} onClick={() => { clearFeedback(); setMerging(active); setTarget(null); }}>{t("maintenance.merge")}</Button>
          </div>
        </div>}
      </div>
    </> : <p className="text-xs text-ink3">{t("maintenance.emptyDomains")}</p>}
    {!editing && !merging && note && <p role="status" className="mt-2 text-xs">{note}</p>}
    {!editing && !merging && error && <p role="alert" className="mt-2 text-xs text-fail wrap-anywhere">{error}</p>}
    <Dialog open={!!editing} onOpenChange={open => { if (!open) setEditing(null); }}><DialogContent aria-describedby={undefined}>
      <DialogTitle>{t("maintenance.edit")}</DialogTitle>
      {error && <p role="alert" className="text-sm text-fail wrap-anywhere">{error}</p>}
      <form className="space-y-4" onSubmit={async event => { event.preventDefault(); if (editing && !locked && name.trim() && definition.trim() && await command("domain_rename", { nodeId: editing.nodeId, name: name.trim(), definition: definition.trim() })) setEditing(null); }}>
        <label className="block space-y-2 text-sm"><span>{t("maintenance.name")}</span><Input value={name} maxLength={120} required onChange={event => setName(event.target.value)} /></label>
        <label className="block space-y-2 text-sm"><span>{t("maintenance.definition")}</span><Input value={definition} maxLength={400} required onChange={event => setDefinition(event.target.value)} /></label>
        <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={() => setEditing(null)}>{t("maintenance.cancel")}</Button><Button disabled={locked || !name.trim() || !definition.trim()}>{t("maintenance.save")}</Button></div>
      </form>
    </DialogContent></Dialog>
    <Dialog open={!!merging} onOpenChange={open => { if (!open) setMerging(null); }}><DialogContent aria-describedby={undefined}>
      <DialogTitle>{t("maintenance.mergeTitle")}</DialogTitle>
      {error && <p role="alert" className="text-sm text-fail wrap-anywhere">{error}</p>}
      <p className="text-sm wrap-anywhere">{merging?.name} · {t("maintenance.mergeInto")}</p>
      <ScrollArea className="max-h-64"><div role="group" aria-label={t("maintenance.mergeInto")} className="flex flex-col">
        {outline.active.filter(node => !excluded.has(node.nodeId)).map(node => <button key={node.nodeId} aria-pressed={target?.nodeId === node.nodeId} className="border-b px-3 py-2 text-left text-sm wrap-anywhere hover:bg-sunk aria-pressed:bg-sunk aria-pressed:font-semibold transition-colors duration-150 motion-reduce:transition-none" onClick={() => setTarget(node)}>{node.name}</button>)}
      </div></ScrollArea>
      {target && <p className="text-sm wrap-anywhere">{t("maintenance.mergeConfirm", { source: merging?.name, target: target.name })}</p>}
      <div className="flex justify-end gap-2"><Button variant="outline" onClick={() => setMerging(null)}>{t("maintenance.cancel")}</Button><Button disabled={locked || !target} onClick={async () => { if (merging && target && await command("domain_merge", { sourceId: merging.nodeId, targetId: target.nodeId })) setMerging(null); }}>{t("maintenance.merge")}</Button></div>
    </DialogContent></Dialog>
  </div>;
}
