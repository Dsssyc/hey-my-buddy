import { useMemo, useState } from "react";
import { Info } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
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
  const [pointedId, setPointedId] = useState<string | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [editing, setEditing] = useState<DomainNode | null>(null), [merging, setMerging] = useState<DomainNode | null>(null);
  const [name, setName] = useState(""), [definition, setDefinition] = useState("");
  const [target, setTarget] = useState<DomainNode | null>(null);
  const isFolded = (id: string) => {
    const saved = readChoiceViewPreference(`domain-tree-node:${id}`, ["expanded", "collapsed"] as const);
    return !(nodeChoices[id] ?? (saved === null ? defaultExpanded : saved === "expanded"));
  };
  const outline = useMemo(() => domainOutline(nodes, isFolded), [nodes, defaultExpanded, nodeChoices]);
  const activeId = pointedId ?? focusedId ?? openId;
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
  return <div data-slot="domain-tree" className="domain-tree">
    {!!outline.roots.length ? <>
      <div className="domain-tree-overview">
        <p>{t("domainTree.summary", { roots: outline.roots.length, nodes: outline.active.length, tasks: outline.total })}</p>
        <button type="button" onClick={toggleAll}>
          {t(allExpanded ? "domainTree.collapseAll" : "domainTree.expandAll")}
        </button>
      </div>
      <div className="domain-tree-stage">
        <div className="domain-tree-viewport" role="tree" aria-label={t("domainTree.domains")}>
          <div className="domain-tree-list">
            {outline.visible.map(node => {
              const folded = isFolded(node.nodeId);
              const segments = [{ id: node.nodeId, count: node.directCount, own: true }, ...node.children.map(child => ({ id: child.nodeId, count: child.total, own: false }))].filter(segment => segment.count > 0);
              return <Popover key={node.nodeId} open={openId === node.nodeId} onOpenChange={open => setOpenId(open ? node.nodeId : null)}><div role="treeitem" tabIndex={0} data-details-open={openId === node.nodeId || undefined} aria-level={node.depth + 1} data-slot="domain-row" data-node={node.nodeId}
                className="domain-tree-row" style={{ "--depth": node.depth } as CSSProperties}
                aria-label={`${node.name}, ${t("domainTree.taskCount", { count: node.total })}${node.children.length ? `, ${t(folded ? "domainTree.expand" : "domainTree.collapse", { name: node.name })}` : ""}`}
                aria-expanded={node.children.length ? !folded : undefined}
                onPointerEnter={event => { if (event.currentTarget.contains(event.target as Node)) setPointedId(node.nodeId); }}
                onPointerLeave={event => { if (event.currentTarget.contains(event.target as Node)) setPointedId(null); }}
                onFocus={event => { if (event.currentTarget.contains(event.target)) setFocusedId(node.nodeId); }}
                onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setFocusedId(null); }}
                onKeyDown={event => {
                  // Portal events bubble through React, even though their DOM is outside the row.
                  if (event.target !== event.currentTarget) return;
                  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); if (node.children.length) toggle(node.nodeId); }
                }}
                onClick={event => {
                  if (event.currentTarget.contains(event.target as Node) && !(event.target as Element).closest("button") && node.children.length) toggle(node.nodeId);
                }}>
                <span className="domain-tree-name">{node.depth > 0 && <span className="domain-tree-guide" aria-hidden="true" />}<span className="domain-tree-chevron" aria-hidden="true">{node.children.length > 0 ? folded ? "▸" : "▾" : ""}</span><span className="domain-tree-name-text">{node.name}</span>
                  <PopoverTrigger asChild><button type="button" className="domain-tree-info" aria-label={t("domainTree.details", { name: node.name })}><Info aria-hidden="true" /></button></PopoverTrigger>
                </span>
                <span className="domain-tree-count">{node.total}</span>
                <span className="domain-tree-bar" aria-hidden="true" data-node={node.nodeId}>
                  <span className="domain-tree-bar-fill" style={{ width: `${100 * node.total / Math.max(1, maximum)}%` }}>
                    {segments.map(segment => <span key={segment.id} className="domain-tree-segment" data-segment={segment.id} data-own={segment.own || undefined}
                      data-highlight={activeId === segment.id && segment.id !== node.nodeId || undefined}
                      style={{ flexGrow: segment.count, backgroundColor: `var(--buddy-domain-${colorBand(segment.count, colorMaximum)})` }} />)}
                  </span>
                </span>
                <PopoverContent data-slot="domain-details" aria-label={t("domainTree.details", { name: node.name })} side="bottom" align="start" className="domain-tree-popover w-[304px] max-w-[calc(100vw-2rem)]">
                  <p className="domain-tree-popover-title">{node.name} <span>{t("domainTree.taskCount", { count: node.total })}</span></p>
                  <p className="domain-tree-popover-definition">{node.definition}</p>
                  {!!outline.mergedFrom.get(node.nodeId)?.length && <p className="domain-tree-popover-merged">{t("domainTree.mergedFrom", { names: formatList(outline.mergedFrom.get(node.nodeId)!) })}</p>}
                  <div className="domain-tree-popover-actions">
                    <Button variant="ghost" size="sm" disabled={locked} onClick={() => { clearFeedback(); setEditing(node); setName(node.name); setDefinition(node.definition); }}>{t("maintenance.edit")}</Button>
                    <Button variant="ghost" size="sm" disabled={locked || outline.active.length < 2} onClick={() => { clearFeedback(); setMerging(node); setTarget(null); }}>{t("maintenance.merge")}</Button>
                  </div>
                </PopoverContent>
              </div></Popover>;
            })}
          </div>
        </div>
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
