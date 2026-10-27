import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { DomainNode } from "../types";

export function DomainTree({ nodes, locked, command, error, note, clearFeedback }: {
  nodes: DomainNode[]; locked: boolean; error: string; note: string; clearFeedback: () => void; command: (operation: string, params: object) => Promise<boolean>;
}) {
  const { t } = useTranslation("buddies");
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<DomainNode | null>(null), [merging, setMerging] = useState<DomainNode | null>(null);
  const [name, setName] = useState(""), [definition, setDefinition] = useState("");
  const [target, setTarget] = useState<DomainNode | null>(null);
  const active = nodes.filter(node => node.mergedIntoId === null);
  function descendants(id: string): Set<string> {
    const ids = new Set<string>();
    for (const node of active.filter(node => node.parentId === id)) { ids.add(node.nodeId); for (const child of descendants(node.nodeId)) ids.add(child); }
    return ids;
  }
  const excluded = merging ? new Set([merging.nodeId, ...descendants(merging.nodeId)]) : new Set<string>();
  function level(parentId: string | null) {
    return active.filter(node => node.parentId === parentId).map(node => {
      const children = active.some(child => child.parentId === node.nodeId);
      const collapsed = folded.has(node.nodeId);
      return <li key={node.nodeId}>
        <div className="flex min-w-0 flex-wrap items-start gap-x-3 gap-y-1 border-b border-line py-2.5" data-slot="domain-row">
          <div className="flex min-w-0 flex-1 items-start gap-1">
            {children ? <Hint text={t(collapsed ? "maintenance.expand" : "maintenance.collapse", { name: node.name })}><button type="button" className="flex size-7 shrink-0 items-center justify-center rounded-sm text-ink2 outline-none hover:bg-sunk focus-visible:ring-2 focus-visible:ring-ring transition-colors duration-150 motion-reduce:transition-none" aria-label={t(collapsed ? "maintenance.expand" : "maintenance.collapse", { name: node.name })} aria-expanded={!collapsed} onClick={() => setFolded(previous => { const next = new Set(previous); if (collapsed) next.delete(node.nodeId); else next.add(node.nodeId); return next; })}>{collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}</button></Hint> : <span className="w-6 shrink-0" />}
            <div className="min-w-0"><p className="font-medium wrap-anywhere">{node.name}</p><p className="mt-1 text-xs wrap-anywhere text-ink2">{node.definition}</p></div>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button variant="ghost" size="sm" disabled={locked} onClick={() => { clearFeedback(); setEditing(node); setName(node.name); setDefinition(node.definition); }}>{t("maintenance.edit")}</Button>
            <Button variant="ghost" size="sm" disabled={locked || active.length < 2} onClick={() => { clearFeedback(); setMerging(node); setTarget(null); }}>{t("maintenance.merge")}</Button>
          </div>
        </div>
        {children && !collapsed && <ul className="ml-3 border-l border-line pl-2">{level(node.nodeId)}</ul>}
      </li>;
    });
  }
  return <div data-slot="domain-tree">
    {active.length ? <ul>{level(null)}</ul> : <p className="text-xs text-ink3">{t("maintenance.emptyDomains")}</p>}
    {!editing && !merging && note && <p role="status" className="mt-2 text-xs">{note}</p>}
    {!editing && !merging && error && <p role="alert" className="mt-2 text-xs text-fail wrap-anywhere">{error}</p>}
    <Dialog open={!!editing} onOpenChange={open => { if (!open) setEditing(null); }}><DialogContent aria-describedby={undefined}>
      <DialogTitle>{t("maintenance.edit")}</DialogTitle>
      {error && <p role="alert" className="text-sm text-fail wrap-anywhere">{error}</p>}
      <form className="space-y-4" onSubmit={async e => { e.preventDefault(); if (editing && !locked && name.trim() && definition.trim() && await command("domain_rename", { nodeId: editing.nodeId, name: name.trim(), definition: definition.trim() })) setEditing(null); }}>
        <label className="block space-y-2 text-sm"><span>{t("maintenance.name")}</span><Input value={name} maxLength={120} required onChange={e => setName(e.target.value)} /></label>
        <label className="block space-y-2 text-sm"><span>{t("maintenance.definition")}</span><Input value={definition} maxLength={400} required onChange={e => setDefinition(e.target.value)} /></label>
        <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={() => setEditing(null)}>{t("maintenance.cancel")}</Button><Button disabled={locked || !name.trim() || !definition.trim()}>{t("maintenance.save")}</Button></div>
      </form>
    </DialogContent></Dialog>
    <Dialog open={!!merging} onOpenChange={open => { if (!open) setMerging(null); }}><DialogContent aria-describedby={undefined}>
      <DialogTitle>{t("maintenance.mergeTitle")}</DialogTitle>
      {error && <p role="alert" className="text-sm text-fail wrap-anywhere">{error}</p>}
      <p className="text-sm wrap-anywhere">{merging?.name} · {t("maintenance.mergeInto")}</p>
      <ScrollArea className="max-h-64"><div role="group" aria-label={t("maintenance.mergeInto")} className="flex flex-col">
        {active.filter(node => !excluded.has(node.nodeId)).map(node => <button key={node.nodeId} aria-pressed={target?.nodeId === node.nodeId} className="border-b px-3 py-2 text-left text-sm wrap-anywhere hover:bg-sunk aria-pressed:bg-sunk aria-pressed:font-semibold transition-colors duration-150 motion-reduce:transition-none" onClick={() => setTarget(node)}>{node.name}</button>)}
      </div></ScrollArea>
      {target && <p className="text-sm wrap-anywhere">{t("maintenance.mergeConfirm", { source: merging?.name, target: target.name })}</p>}
      <div className="flex justify-end gap-2"><Button variant="outline" onClick={() => setMerging(null)}>{t("maintenance.cancel")}</Button><Button disabled={locked || !target} onClick={async () => { if (merging && target && await command("domain_merge", { sourceId: merging.nodeId, targetId: target.nodeId })) setMerging(null); }}>{t("maintenance.merge")}</Button></div>
    </DialogContent></Dialog>
  </div>;
}
