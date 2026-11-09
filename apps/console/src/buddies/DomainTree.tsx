import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatList } from "../i18n/format";
import type { DomainNode } from "../types";
import { domainColorPrefix } from "../timeline/buddyColors";
import "./DomainTree.css";

type Branch = DomainNode & { children: Branch[]; depth: number; root: number; total: number; x: number; y: number; length: number; gap: number; virtual?: boolean };
const ROW = 56;
const START = 24;

function branchLayout(nodes: DomainNode[], folded: ReadonlySet<string>, allLabel: string, allDefinition: string, measured: ReadonlyMap<string, number>) {
  const active = nodes.filter(node => node.mergedIntoId === null);
  const byId = new Map<string, Branch>(active.map(node => [node.nodeId, { ...node, children: [], depth: 0, root: 0, total: node.directCount, x: 0, y: 0, length: 0, gap: 72 }]));
  const roots: Branch[] = [];
  for (const node of byId.values()) {
    if (node.parentId === null) roots.push(node);
    else byId.get(node.parentId)?.children.push(node);
  }
  const mergedFrom = new Map<string, string[]>();
  const allById = new Map(nodes.map(node => [node.nodeId, node]));
  // Classifications retain the original node after a merge. Follow every
  // pointer so consecutive merges retain all historical counts and names.
  for (const node of nodes) if (node.mergedIntoId !== null) {
    let target = allById.get(node.mergedIntoId)!;
    while (target.mergedIntoId !== null) target = allById.get(target.mergedIntoId)!;
    const branch = byId.get(target.nodeId)!;
    branch.directCount += node.directCount;
    const names = mergedFrom.get(target.nodeId) ?? [];
    names.push(node.name);
    mergedFrom.set(target.nodeId, names);
  }
  let allId = "__domain_all__";
  while (byId.has(allId)) allId += "_";
  const all: Branch | null = roots.length ? { nodeId: allId, name: allLabel, definition: allDefinition, parentId: null, mergedIntoId: null,
    directCount: 0, children: roots, depth: 0, root: -1, total: 0, x: 0, y: 0, length: 0, gap: 72, virtual: true } : null;
  if (all) {
    byId.set(allId, all);
    for (const root of roots) root.parentId = allId;
  }
  const columns: number[] = [];
  const visible: Branch[] = [];
  function count(node: Branch): number {
    node.total = node.directCount + node.children.reduce((sum, child) => sum + count(child), 0);
    return node.total;
  }
  function assign(node: Branch, depth: number, root: number) {
    node.depth = depth; node.root = root;
    // Wide labels expand their whole depth column. No level or node count is capped.
    node.length = measured.get(node.nodeId) ?? Math.max(88, [...node.name].length * 15 + String(node.total).length * 10 + (folded.has(node.nodeId) ? 34 : 0) + 26);
    columns[depth] = Math.max(columns[depth] ?? 0, node.length);
    visible.push(node);
    if (!folded.has(node.nodeId)) for (const [index, child] of node.children.entries()) assign(child, depth + 1, node.virtual ? index : root);
  }
  if (all) { count(all); assign(all, 0, -1); }
  let leaf = 0;
  function position(node: Branch): number {
    const shown = folded.has(node.nodeId) ? [] : node.children;
    const childYs = shown.map(position);
    node.y = childYs.length ? (childYs[0]! + childYs[childYs.length - 1]!) / 2 : 48 + leaf++ * ROW;
    node.gap = childYs.length > 1 ? Math.max(144, (childYs[childYs.length - 1]! - childYs[0]!) * .45) : 72;
    return node.y;
  }
  if (all) position(all);
  const offsets = [START];
  for (let depth = 1; depth < columns.length; depth++) {
    const previous = visible.filter(node => node.depth === depth - 1);
    offsets[depth] = offsets[depth - 1]! + Math.max(...previous.map(node => node.length + node.gap));
  }
  for (const node of visible) node.x = offsets[node.depth]!;
  const width = Math.max(240, ...visible.map(node => node.x + node.length + 28));
  return { active, roots, visible, byId, mergedFrom, width, height: Math.max(120, 96 + (leaf - 1) * ROW) };
}

function relatedNodes(node: Branch, byId: ReadonlyMap<string, Branch>): Set<string> {
  const related = new Set<string>();
  for (let cursor: Branch | undefined = node; cursor; cursor = cursor.parentId === null ? undefined : byId.get(cursor.parentId)) related.add(cursor.nodeId);
  const queue = [node];
  for (let index = 0; index < queue.length; index++) {
    const current = queue[index]!;
    related.add(current.nodeId);
    queue.push(...current.children);
  }
  return related;
}

export function DomainTree({ nodes, locked, command, error, note, clearFeedback }: {
  nodes: DomainNode[]; locked: boolean; error: string; note: string; clearFeedback: () => void; command: (operation: string, params: object) => Promise<boolean>;
}) {
  const { t } = useTranslation("buddies");
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const [measured, setMeasured] = useState<ReadonlyMap<string, number>>(new Map());
  const [activeId, setActiveId] = useState<string | null>(null);
  const [popoverAt, setPopoverAt] = useState<{ left: number; top: number } | null>(null);
  const [editing, setEditing] = useState<DomainNode | null>(null), [merging, setMerging] = useState<DomainNode | null>(null);
  const [name, setName] = useState(""), [definition, setDefinition] = useState("");
  const [target, setTarget] = useState<DomainNode | null>(null);
  const stageRef = useRef<HTMLDivElement>(null), viewportRef = useRef<HTMLDivElement>(null), panelRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const allLabel = t("domainTree.all"), allDefinition = t("domainTree.allDefinition");
  const layout = useMemo(() => branchLayout(nodes, folded, allLabel, allDefinition, measured), [nodes, folded, allLabel, allDefinition, measured]);
  const palette = domainColorPrefix(layout.roots.length);
  const active = activeId ? layout.byId.get(activeId) : undefined;
  const related = active ? relatedNodes(active, layout.byId) : null;
  const excluded = new Set<string>();
  if (merging) {
    const queue = [layout.byId.get(merging.nodeId)!];
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
  useLayoutEffect(() => () => { if (closeTimer.current) clearTimeout(closeTimer.current); }, []);
  useLayoutEffect(() => {
    let cancelled = false;
    const measure = () => {
      if (cancelled || !stageRef.current) return;
      const widths = new Map<string, number>();
      for (const button of stageRef.current.querySelectorAll<HTMLElement>(".domain-branch-node")) {
        const width = button.getBoundingClientRect().width;
        if (width > 0) widths.set(button.dataset.node!, Math.ceil(width + 24));
      }
      if (widths.size) setMeasured(previous => {
        if ([...widths].every(([id, width]) => previous.get(id) === width)) return previous;
        return new Map([...previous, ...widths]);
      });
    };
    measure();
    void document.fonts?.ready.then(measure);
    return () => { cancelled = true; };
  }, [nodes, folded, allLabel, allDefinition]);
  useLayoutEffect(() => {
    if (!active || !stageRef.current || !viewportRef.current || !panelRef.current) return;
    const stage = stageRef.current, viewport = viewportRef.current, panel = panelRef.current;
    const place = () => {
      const stageBox = stage.getBoundingClientRect();
      const panelWidth = panel.offsetWidth, panelHeight = panel.offsetHeight;
      const maxX = Math.max(4, stage.clientWidth - panelWidth - 4);
      const clamp = (value: number) => Math.max(4, Math.min(maxX, value));
      const preferredX = clamp(active.x + active.length + 16 - viewport.scrollLeft);
      const preferredY = active.y + 14;
      const labels = [...stage.querySelectorAll<HTMLElement>(".domain-branch-node")].map(button => {
        const rect = button.getBoundingClientRect();
        return { left: rect.left - stageBox.left, right: rect.right - stageBox.left, top: rect.top - stageBox.top, bottom: rect.bottom - stageBox.top };
      }).filter(rect => rect.right > 0 && rect.left < stage.clientWidth);
      let best = { left: 0, top: 0, extra: Infinity, distance: Infinity };
      for (const left of new Set([preferredX, clamp(active.x - panelWidth - 16 - viewport.scrollLeft), 4, maxX, clamp((stage.clientWidth - panelWidth) / 2)])) {
        const blocked = labels.filter(rect => left < rect.right + 4 && left + panelWidth > rect.left - 4)
          .map(rect => ({ start: rect.top - 4, end: rect.bottom + 4 })).sort((a, b) => a.start - b.start);
        let freeStart = 4;
        const consider = (end: number) => {
          if (end - freeStart < panelHeight) return;
          const top = Math.max(freeStart, Math.min(preferredY, end - panelHeight));
          const extra = Math.max(0, top + panelHeight + 8 - layout.height);
          const distance = Math.abs(left - preferredX) + Math.abs(top - preferredY);
          if (extra < best.extra || extra === best.extra && distance < best.distance) best = { left, top, extra, distance };
        };
        for (const interval of blocked) { consider(interval.start); freeStart = Math.max(freeStart, interval.end); }
        consider(Infinity);
      }
      if (Number.isFinite(best.extra)) {
        stage.style.minHeight = `${Math.ceil(layout.height + best.extra)}px`;
        setPopoverAt({ left: best.left, top: best.top });
      }
    };
    place();
    viewport.addEventListener("scroll", place);
    window.addEventListener("resize", place);
    return () => { stage.style.minHeight = ""; viewport.removeEventListener("scroll", place); window.removeEventListener("resize", place); };
  }, [active, layout]);

  return <div data-slot="domain-tree" className="domain-tree">
    {layout.visible.length ? <div ref={stageRef} className="domain-tree-stage" onPointerLeave={closeSoon} onBlur={closeSoon} onKeyDown={event => { if (event.key === "Escape") setActiveId(null); }}>
      <div ref={viewportRef} className="domain-tree-viewport" role="tree" aria-label={t("domainTree.domains")}>
        <div className="domain-tree-canvas" style={{ width: layout.width, height: layout.height }}>
          <svg aria-hidden="true" width={layout.width} height={layout.height} className="domain-tree-svg">
            {layout.visible.map(node => {
              const parent = node.parentId === null ? null : layout.byId.get(node.parentId)!;
              const color = node.virtual ? "var(--line2)" : palette[node.root]!;
              const dim = related && !related.has(node.nodeId);
              const width = 1.25 + 7.75 * node.total / Math.max(1, layout.visible[0]!.total);
              const gap = parent ? node.x - parent.x - parent.length : 0;
              return <g key={node.nodeId} data-node={node.nodeId} data-dim={dim || undefined} style={{ color }}>
                {parent && <path className="domain-tree-line" strokeWidth={width} d={`M${parent.x + parent.length} ${parent.y} C${parent.x + parent.length + gap * .38} ${parent.y + (node.y - parent.y) * .28},${node.x - gap * .35} ${node.y},${node.x} ${node.y}`} />}
                <line className="domain-tree-line" strokeWidth={width} x1={node.x} y1={node.y} x2={node.x + node.length} y2={node.y} />
              </g>;
            })}
          </svg>
          {layout.visible.map(node => <button key={node.nodeId} type="button" role="treeitem" aria-level={node.depth + 1} data-slot="domain-row" data-node={node.nodeId}
            className="domain-branch-node" style={{ left: node.x, top: node.y - 34 }}
            aria-label={`${node.name}, ${t("domainTree.taskCount", { count: node.total })}${node.children.length ? `, ${t(folded.has(node.nodeId) ? "domainTree.expand" : "domainTree.collapse", { name: node.name })}` : ""}`}
            aria-expanded={node.children.length ? !folded.has(node.nodeId) : undefined}
            onPointerEnter={() => show(node.nodeId)} onFocus={() => show(node.nodeId)}
            onKeyDown={event => { const firstAction = panelRef.current?.querySelector("button"); if (event.key === "Tab" && !event.shiftKey && activeId === node.nodeId && firstAction) { event.preventDefault(); firstAction.focus(); } }}
            onClick={() => { if (node.children.length) setFolded(previous => { const next = new Set(previous); if (next.has(node.nodeId)) next.delete(node.nodeId); else next.add(node.nodeId); return next; }); }}>
            <span className="domain-branch-name">{node.name}</span><span className="domain-branch-count">{node.total}</span>
            {folded.has(node.nodeId) && node.children.length > 0 && <span className="domain-branch-more">{t("domainTree.hidden", { count: node.children.length })}</span>}
          </button>)}
        </div>
      </div>
      {active && <div ref={panelRef} className="domain-tree-popover" style={{ left: popoverAt?.left ?? 0, top: popoverAt?.top ?? 0, visibility: popoverAt ? "visible" : "hidden" }} onPointerEnter={() => { if (closeTimer.current) clearTimeout(closeTimer.current); }} onPointerLeave={closeSoon}
        onKeyDown={event => { if (event.key !== "Tab") return; const buttons = panelRef.current?.querySelectorAll("button"); if (!buttons?.length) return; if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); stageRef.current?.querySelector<HTMLElement>(`[data-node="${CSS.escape(active.nodeId)}"].domain-branch-node`)?.focus(); } else if (!event.shiftKey && document.activeElement === buttons[buttons.length - 1]) { const index = layout.visible.findIndex(node => node.nodeId === active.nodeId); const next = stageRef.current?.querySelectorAll<HTMLElement>(".domain-branch-node")[index + 1]; if (next) { event.preventDefault(); next.focus(); } } }}>
        <p className="domain-tree-popover-title">{active.name} <span>{t("domainTree.taskCount", { count: active.total })}</span></p>
        <p className="domain-tree-popover-definition">{active.definition}</p>
        {!!layout.mergedFrom.get(active.nodeId)?.length && <p className="domain-tree-popover-merged">{t("domainTree.mergedFrom", { names: formatList(layout.mergedFrom.get(active.nodeId)!) })}</p>}
        {!active.virtual && <div className="domain-tree-popover-actions">
          <Button variant="ghost" size="sm" disabled={locked} onClick={() => { clearFeedback(); setEditing(active); setName(active.name); setDefinition(active.definition); }}>{t("maintenance.edit")}</Button>
          <Button variant="ghost" size="sm" disabled={locked || layout.active.length < 2} onClick={() => { clearFeedback(); setMerging(active); setTarget(null); }}>{t("maintenance.merge")}</Button>
        </div>}
      </div>}
    </div> : <p className="text-xs text-ink3">{t("maintenance.emptyDomains")}</p>}
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
        {layout.active.filter(node => !excluded.has(node.nodeId)).map(node => <button key={node.nodeId} aria-pressed={target?.nodeId === node.nodeId} className="border-b px-3 py-2 text-left text-sm wrap-anywhere hover:bg-sunk aria-pressed:bg-sunk aria-pressed:font-semibold transition-colors duration-150 motion-reduce:transition-none" onClick={() => setTarget(node)}>{node.name}</button>)}
      </div></ScrollArea>
      {target && <p className="text-sm wrap-anywhere">{t("maintenance.mergeConfirm", { source: merging?.name, target: target.name })}</p>}
      <div className="flex justify-end gap-2"><Button variant="outline" onClick={() => setMerging(null)}>{t("maintenance.cancel")}</Button><Button disabled={locked || !target} onClick={async () => { if (merging && target && await command("domain_merge", { sourceId: merging.nodeId, targetId: target.nodeId })) setMerging(null); }}>{t("maintenance.merge")}</Button></div>
    </DialogContent></Dialog>
  </div>;
}
